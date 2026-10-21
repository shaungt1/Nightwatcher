import argparse
import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Settings:
    def __init__(
        self,
        camera_index=0,
        clips_dir=Path("recordings"),
        port=8765,
        motion_threshold=2500,
        inactivity_seconds=2.0,
        fps=20.0,
        ntfy_topic="",
        ntfy_server="https://ntfy.sh",
    ):
        self.camera_index = camera_index
        self.clips_dir = Path(clips_dir)
        self.port = port
        self.motion_threshold = motion_threshold
        self.inactivity_seconds = inactivity_seconds
        self.fps = fps
        self.ntfy_topic = ntfy_topic.strip()
        self.ntfy_server = ntfy_server.rstrip("/")

    @classmethod
    def from_environment(cls):
        return cls(
            camera_index=int(os.getenv("NIGHTWATCHER_CAMERA", "0")),
            clips_dir=os.getenv("NIGHTWATCHER_CLIPS", "recordings"),
            port=int(os.getenv("NIGHTWATCHER_PORT", "8765")),
            motion_threshold=int(os.getenv("NIGHTWATCHER_MOTION_THRESHOLD", "2500")),
            inactivity_seconds=float(os.getenv("NIGHTWATCHER_INACTIVITY_SECONDS", "2")),
            fps=float(os.getenv("NIGHTWATCHER_FPS", "20")),
            ntfy_topic=os.getenv("NIGHTWATCHER_NTFY_TOPIC", ""),
            ntfy_server=os.getenv("NIGHTWATCHER_NTFY_SERVER", "https://ntfy.sh"),
        )


class MotionDetector:
    def __init__(self, cv2_module, threshold):
        self.cv2 = cv2_module
        self.threshold = threshold
        self.frames_seen = 0
        self.background = cv2_module.createBackgroundSubtractorMOG2(
            history=300, varThreshold=25, detectShadows=True
        )

    def detects_activity(self, frame):
        self.frames_seen += 1
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        foreground = self.background.apply(gray)
        _, foreground = self.cv2.threshold(foreground, 200, 255, self.cv2.THRESH_BINARY)
        foreground = self.cv2.morphologyEx(
            foreground,
            self.cv2.MORPH_OPEN,
            self.cv2.getStructuringElement(self.cv2.MORPH_ELLIPSE, (3, 3)),
        )
        if self.frames_seen <= 30:
            return False
        return self.cv2.countNonZero(foreground) >= self.threshold


class MotionMonitor:
    def __init__(self, settings, cv2_module=None):
        if cv2_module is None:
            try:
                import cv2 as cv2_module
            except ImportError as exc:
                raise RuntimeError(
                    "OpenCV is required. Install dependencies with: "
                    "python -m pip install -r requirements.txt"
                ) from exc

        self.settings = settings
        self.cv2 = cv2_module
        self.detector = MotionDetector(cv2_module, settings.motion_threshold)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._alerts = queue.Queue()
        self._thread = None
        self._alert_thread = None
        self._capture = None
        self._writer = None
        self._events = []
        self._recording = False
        self._motion = False
        self._last_event = None
        self._error = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("monitor has already been started")
        self._stop.clear()
        self._alert_thread = threading.Thread(target=self._send_alerts, daemon=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._alert_thread.start()
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._alerts.put(None)
        if self._alert_thread is not None:
            self._alert_thread.join(timeout=5)

    def status(self):
        with self._lock:
            return {
                "monitoring": self._thread is not None and self._thread.is_alive(),
                "motion_detected": self._motion,
                "recording": self._recording,
                "camera": self.settings.camera_index,
                "last_event": self._last_event,
                "events": len(self._events),
                "error": self._error,
            }

    def events(self):
        with self._lock:
            return list(reversed(self._events))

    def _set_error(self, message):
        with self._lock:
            self._error = message

    def _begin_recording(self, frame):
        height, width = frame.shape[:2]
        self.settings.clips_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ.mp4")
        path = self.settings.clips_dir / filename
        fourcc = self.cv2.VideoWriter_fourcc(*"mp4v")
        writer = self.cv2.VideoWriter(
            str(path), fourcc, self.settings.fps, (width, height)
        )
        if not writer.isOpened():
            writer.release()
            raise RuntimeError("could not create video recording")

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "clip": filename,
        }
        with self._lock:
            self._writer = writer
            self._recording = True
            self._last_event = event["timestamp"]
            self._events.append(event)
            self._events = self._events[-100:]
        self._alerts.put(event)

    def _finish_recording(self):
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        with self._lock:
            self._recording = False

    def _run(self):
        self._capture = self.cv2.VideoCapture(self.settings.camera_index)
        if not self._capture.isOpened():
            self._set_error("could not open webcam")
            self._capture.release()
            self._capture = None
            return

        last_motion = 0.0
        try:
            while not self._stop.is_set():
                success, frame = self._capture.read()
                if not success:
                    self._set_error("could not read from webcam")
                    break

                motion = self.detector.detects_activity(frame)
                now = time.monotonic()
                with self._lock:
                    self._motion = motion

                if motion:
                    last_motion = now
                    if self._writer is None:
                        self._begin_recording(frame)
                elif self._writer is not None and now - last_motion >= self.settings.inactivity_seconds:
                    self._finish_recording()

                if self._writer is not None:
                    self._writer.write(frame)
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            self._finish_recording()
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            with self._lock:
                self._motion = False

    def _send_alerts(self):
        while True:
            event = self._alerts.get()
            if event is None:
                return
            if not self.settings.ntfy_topic:
                continue
            topic = urllib.parse.quote(self.settings.ntfy_topic, safe="")
            request = urllib.request.Request(
                f"{self.settings.ntfy_server}/{topic}",
                data=f"Activity detected at {event['timestamp']}".encode(),
                headers={"Title": "Nightwatcher activity detected"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=5):
                    pass
            except Exception as exc:
                self._set_error(f"mobile alert failed: {exc}")


def create_server(monitor, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = """<!doctype html>
<html><head><meta charset="utf-8"><title>Nightwatcher</title></head>
<body><h1>Nightwatcher</h1><p id="status">Loading status...</p>
<h2>Recent activity</h2><ul id="events"></ul>
<script>
async function refresh() {
  const status = await (await fetch('/status')).json();
  document.querySelector('#status').textContent =
    status.error || (status.recording ? 'Recording activity' : 'Monitoring');
  const events = await (await fetch('/events')).json();
  const list = document.querySelector('#events');
  list.replaceChildren(...events.map(event => {
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = '/clips/' + encodeURIComponent(event.clip);
    link.textContent = event.timestamp + ' — view recording';
    item.append(link);
    return item;
  }));
}
refresh();
setInterval(refresh, 3000);
</script></body></html>""".encode("utf-8")
                self._respond(200, body, "text/html; charset=utf-8")
            elif self.path == "/status":
                self._json(200, monitor.status())
            elif self.path == "/events":
                self._json(200, monitor.events())
            elif self.path.startswith("/clips/"):
                self._serve_clip()
            else:
                self._json(404, {"error": "not found"})

        def _serve_clip(self):
            name = urllib.parse.unquote(self.path[len("/clips/"):])
            clip_path = (monitor.settings.clips_dir / name).resolve()
            if (
                not name
                or Path(name).name != name
                or clip_path.parent != monitor.settings.clips_dir.resolve()
                or not clip_path.is_file()
            ):
                self._json(404, {"error": "clip not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(clip_path.stat().st_size))
            self.end_headers()
            with clip_path.open("rb") as clip:
                while chunk := clip.read(64 * 1024):
                    self.wfile.write(chunk)

        def _json(self, status, value):
            self._respond(
                status,
                json.dumps(value).encode(),
                "application/json; charset=utf-8",
            )

        def _respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    settings = Settings.from_environment()
    parser = argparse.ArgumentParser(description="Local webcam security monitor")
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    monitor = MotionMonitor(settings)
    monitor.start()
    server = create_server(monitor, args.port)
    print(f"Nightwatcher is available at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        monitor.stop()


if __name__ == "__main__":
    main()
