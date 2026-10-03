# pycaas

Python Camera as a Service — discover, capture, and serve camera frames over ZMQ.

A background daemon automatically detects connected cameras (Intel RealSense, Luxonis OAK, generic USB webcams), continuously captures frames, and serves the latest frame on demand via ZMQ IPC. Includes a CLI, a Python client library, and a browser-based MJPEG debug viewer.

## Install

```bash
pip install pycaas                # core only (opencv, pyzmq, numpy, click)
pip install "pycaas[realsense]"   # + pyrealsense2
pip install "pycaas[oak]"         # + depthai
pip install "pycaas[all]"         # everything
```

pycaas uses OpenCV's contrib build (`opencv-contrib-python`), as aprilcube does, so both
install together. Do not add `opencv-python` to the same environment: both wheels provide
`cv2`. For development, `pip install -e ".[all]"`.

## CLI

```bash
pycaas start                        # uses latest matching profile, or prompts
pycaas start --fresh                # ignore saved profiles, prompt and optionally save
pycaas start --profile my_setup     # use a specific saved profile
pycaas status                       # show detected cameras and active streams
pycaas stop                         # stop the daemon
pycaas profiles                     # list all saved configuration profiles
pycaas web-debug [PORT]             # MJPEG viewer at http://localhost:PORT (default 8080)

# Runtime reconfiguration (while service is running)
pycaas config rs_234322071225 --resolution 1280x720 --fps 30
pycaas config rs_234322071225 --exposure 500
pycaas config rs_234322071225 --exposure auto
pycaas config rs_234322071225   # list supported modes
```

## Python API

### Start / Stop

```python
import pycaas

# Start the daemon
pycaas.start()                          # uses latest matching profile, or prompts
pycaas.start(fresh=True)                # ignore profiles, prompt, offer to save
pycaas.start(profile="my_setup")        # use a specific saved profile
pycaas.start(camera_configs={           # explicit configs, no prompts or profiles
    "rs_234322071225": {"resolution": [1280, 720], "fps": 30, "exposure": 500},
})
# Returns PID, or None if already running

pycaas.stop()                           # stop the daemon
```

### Client

```python
from pycaas import PycaasClient

with PycaasClient() as client:
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
pycaas daemon
├── discovery: RealSense → OAK → webcam (avoids double-counting)
├── per-camera capture threads (as fast as possible)
├── frame store (dict: stream_id → latest numpy frame)
├── reconfigure coordination (per-driver threading events)
└── ZMQ REP socket (ipc:///tmp/pycaas.sock)

pycaas client (PycaasClient)
└── ZMQ REQ socket → get_frame / set_config / set_exposure / list_cameras / status / stop
```

## Files

- `~/.config/pycaas/camera_configs.json` — saved configuration profiles (matched by camera serial set)
- `/tmp/pycaas.pid` — daemon PID file
- `/tmp/pycaas.log` — daemon log output
- `/tmp/pycaas.sock` — ZMQ IPC socket
