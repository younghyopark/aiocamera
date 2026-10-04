# aiocamera

Camera as a service — discover, capture, and serve camera frames over ZMQ.

A background daemon automatically detects connected cameras (Intel RealSense, Luxonis OAK, generic USB webcams), continuously captures frames, and serves the latest frame on demand via ZMQ IPC. Includes a CLI, a Python client library, and a browser-based MJPEG debug viewer.

## Install

```bash
pip install aiocamera                # core only (opencv, pyzmq, numpy, click)
pip install "aiocamera[realsense]"   # + pyrealsense2
pip install "aiocamera[oak]"         # + depthai
pip install "aiocamera[all]"         # everything
```

aiocamera uses OpenCV's contrib build (`opencv-contrib-python`), as aprilcube does, so both
install together. Do not add `opencv-python` to the same environment: both wheels provide
`cv2`. For development, `pip install -e ".[all]"`.

## CLI

```bash
aiocamera start                        # uses latest matching profile, or prompts
aiocamera start --fresh                # ignore saved profiles, prompt and optionally save
aiocamera start --profile my_setup     # use a specific saved profile
aiocamera status                       # show detected cameras and active streams
aiocamera stop                         # stop the daemon
aiocamera profiles                     # list all saved configuration profiles
aiocamera web-debug [PORT]             # MJPEG viewer at http://localhost:PORT (default 8080)

# Runtime reconfiguration (while service is running)
aiocamera config rs_234322071225 --resolution 1280x720 --fps 30
aiocamera config rs_234322071225 --exposure 500
aiocamera config rs_234322071225 --exposure auto
aiocamera config rs_234322071225   # list supported modes
```

## Python API

### Start / Stop

```python
import aiocamera

# Start the daemon
aiocamera.start()                          # uses latest matching profile, or prompts
aiocamera.start(fresh=True)                # ignore profiles, prompt, offer to save
aiocamera.start(profile="my_setup")        # use a specific saved profile
aiocamera.start(camera_configs={           # explicit configs, no prompts or profiles
    "rs_234322071225": {"resolution": [1280, 720], "fps": 30, "exposure": 500},
})
# Returns PID, or None if already running

aiocamera.stop()                           # stop the daemon
```

A camera's config, in a profile or `camera_configs`, may also choose its streams and
whether it opens at all. A config keyed by a camera type (`realsense`, `oak`, `webcam`)
applies to every camera of that type without one of its own:

```python
aiocamera.start(camera_configs={
    "rs_234322071225": {"resolution": [640, 480], "fps": 60, "streams": ["color"]},  # no depth
    "webcam": {"enabled": False},                                                   # no webcams
})
```

A RealSense opens color and depth by default, and aligns depth to color when both run.
`aiocamera start --fresh` asks for both and saves them in the profile.

### Client

```python
from aiocamera import CameraClient

with CameraClient() as client:
    cameras = client.list_cameras()
    status  = client.status()

    # Get frame at capture resolution
    frame = client.get_frame("rs_234322071225_color")  # numpy.ndarray

    # Get frame at a specific resolution (resized server-side)
    frame = client.get_frame("rs_234322071225_color", resolution=(320, 240))

    # Set a default resolution for all subsequent get_frame() calls
    client.set_resolution(320, 240)
    frame = client.get_frame("rs_234322071225_color")  # 320x240
    client.set_resolution(0, 0)                        # clear → back to capture resolution

    # Runtime reconfiguration
    modes = client.get_supported_modes("rs_234322071225")  # valid (w,h,fps) combos
    client.set_config("rs_234322071225", resolution=(1280, 720), fps=30)  # blocking
    client.set_exposure("rs_234322071225", 500)     # immediate, no restart
    client.set_exposure("rs_234322071225", None)     # auto exposure

    # Intrinsics
    intrinsics = client.get_intrinsics("rs_234322071225")
    K = client.get_intrinsics_matrix("rs_234322071225")  # 3x3 numpy array
```

## Camera IDs

Camera IDs are based on serial numbers for stability across plug/unplug cycles:

| Type | ID format | Example |
|------|-----------|---------|
| RealSense | `rs_{serial}` | `rs_234322071225` |
| OAK | `oak_{mxid}` | `oak_18443010A1B2C3D4` |
| Webcam | `webcam{n}` | `webcam0` |

Stream names are `{camera_id}_{stream}` — e.g. `rs_234322071225_color`, `rs_234322071225_depth`, `oak_18443010A1B2C3D4_CAM_A`.

## Supported Cameras

| Type | SDK | Streams | Install extra |
|------|-----|---------|---------------|
| Intel RealSense | pyrealsense2 | color, depth | `[realsense]` |
| Luxonis OAK | depthai | per-socket (CAM_A, CAM_B, ...) | `[oak]` |
| USB Webcam | OpenCV | color | (included) |

## Architecture

```
aiocamera daemon
├── discovery: RealSense → OAK → webcam (avoids double-counting)
├── per-camera capture threads (as fast as possible)
├── frame store (dict: stream_id → latest numpy frame)
├── reconfigure coordination (per-driver threading events)
└── ZMQ REP socket (ipc:///tmp/aiocamera.sock)

aiocamera client (CameraClient)
└── ZMQ REQ socket → get_frame / set_config / set_exposure / list_cameras / status / stop
```

## Files

- `~/.config/aiocamera/camera_configs.json` — saved configuration profiles (matched by camera serial set)
- `/tmp/aiocamera.pid` — daemon PID file
- `/tmp/aiocamera.log` — daemon log output
- `/tmp/aiocamera.sock` — ZMQ IPC socket

aiocamera was called pycaas before its first release. Profiles saved by pycaas
(`~/.config/pycaas/camera_configs.json`) are used until a profile is saved under
aiocamera. A running pycaas daemon holds the cameras: stop it (`pycaas stop`)
before `aiocamera start`.
