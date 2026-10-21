import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from nightwatcher import MotionDetector, Settings, create_server


class FakeCv2:
    COLOR_BGR2GRAY = 1
    THRESH_BINARY = 2
    MORPH_OPEN = 3
    MORPH_ELLIPSE = 4
    foreground_pixels = 3000

    def createBackgroundSubtractorMOG2(self, **_kwargs):
        return self

    def apply(self, _frame):
        return object()

    def cvtColor(self, frame, _color):
        return frame

    def threshold(self, frame, *_args):
        return 0, frame

    def morphologyEx(self, frame, *_args):
        return frame

    def getStructuringElement(self, *_args):
        return object()

    def countNonZero(self, _frame):
        return self.foreground_pixels


class MotionDetectorTests(unittest.TestCase):
    def test_waits_for_background_then_applies_motion_threshold(self):
        cv2 = FakeCv2()
        detector = MotionDetector(cv2, threshold=2500)

        for _ in range(30):
            self.assertFalse(detector.detects_activity(object()))

        self.assertTrue(detector.detects_activity(object()))
        cv2.foreground_pixels = 2499
        self.assertFalse(detector.detects_activity(object()))


class StubMonitor:
    def __init__(self, clips_dir):
        self.settings = Settings(clips_dir=clips_dir)

    def status(self):
        return {"monitoring": True, "recording": False}

    def events(self):
        return [{"timestamp": "2026-01-01T00:00:00+00:00", "clip": "sample.mp4"}]


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clips_dir = Path(self.temp_dir.name)
        (self.clips_dir / "sample.mp4").write_bytes(b"sample footage")
        self.server = create_server(StubMonitor(self.clips_dir), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_port)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def get(self, path):
        self.connection.request("GET", path)
        response = self.connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()

    def test_status_and_event_endpoints_return_json(self):
        status, content_type, body = self.get("/status")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(json.loads(body), {"monitoring": True, "recording": False})

        status, _, body = self.get("/events")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["clip"], "sample.mp4")

    def test_clip_endpoint_serves_recording_and_rejects_traversal(self):
        status, content_type, body = self.get("/clips/sample.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "video/mp4")
        self.assertEqual(body, b"sample footage")

        status, _, _ = self.get("/clips/%2e%2e%2foutside.mp4")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
