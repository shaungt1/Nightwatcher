# Nightwatcher

Nightwatcher turns a computer and its webcam into a local security monitor. It
detects movement in the camera feed, saves MP4 clips while activity is present,
and can send mobile push alerts through ntfy.

## Run

Use Python 3.9 or newer and install the camera dependency:

```sh
python -m pip install -r requirements.txt
python nightwatcher.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) to see monitor status and
recent recordings. Clips are saved in `recordings/` by default.

To receive mobile alerts, install the ntfy app, subscribe to a private topic,
and start Nightwatcher with that topic configured:

```sh
NIGHTWATCHER_NTFY_TOPIC=your-long-random-topic python nightwatcher.py
```

The topic name is the authorization for receiving alerts on ntfy.sh; keep it
private. Self-hosted ntfy servers can be selected with
`NIGHTWATCHER_NTFY_SERVER`.

Configuration is available through environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NIGHTWATCHER_CAMERA` | `0` | Webcam device index |
| `NIGHTWATCHER_CLIPS` | `recordings` | Directory for video clips |
| `NIGHTWATCHER_PORT` | `8765` | Local web server port |
| `NIGHTWATCHER_MOTION_THRESHOLD` | `2500` | Foreground pixels required to trigger activity |
| `NIGHTWATCHER_INACTIVITY_SECONDS` | `2` | Quiet time before a recording ends |
| `NIGHTWATCHER_FPS` | `20` | Playback frame rate for saved clips |
| `NIGHTWATCHER_NTFY_TOPIC` | unset | ntfy topic for mobile push alerts |
| `NIGHTWATCHER_NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL |

The server binds to `127.0.0.1` and is accessible only from the local computer.
The first 30 camera frames are used to establish a background before motion
alerts and recordings begin.

## Test

```sh
python -m unittest
```
