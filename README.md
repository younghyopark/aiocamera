# pycaas

Python Camera as a Service — discover, capture, and serve camera frames over ZMQ.

A background daemon automatically detects connected cameras (Intel RealSense, Luxonis OAK, generic USB webcams), continuously captures frames, and serves the latest frame on demand via ZMQ IPC. Includes a CLI, a Python client library, and a browser-based MJPEG debug viewer.

## Install

```bash
pip install -e .                  # core only (opencv, pyzmq, numpy, click)
pip install -e ".[realsense]"     # + pyrealsense2
pip install -e ".[oak]"           # + depthai
pip install -e ".[all]"           # everything
```

## CLI

```bash
pycaas start             # start the daemon (discovers cameras, begins capturing)
pycaas status            # show detected cameras and active streams
pycaas web-debug [PORT]  # MJPEG viewer at http://localhost:PORT (default 8080)
pycaas stop              # stop the daemon
```

## Python Client

```python
from pycaas import PycaasClient

with PycaasClient() as client:
    cameras = client.list_cameras()
    status = client.status()

    # get frame at capture resolution
    frame = client.get_frame("rs0_color")  # numpy.ndarray

    # get frame at a specific resolution
    frame = client.get_frame("rs0_color", resolution=(320, 240))

    # set a default resolution for all subsequent calls
    client.set_resolution(320, 240)
    frame = client.get_frame("rs0_color")  # 320x240
    client.set_resolution(0, 0)            # clear → back to capture resolution
```

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
└── ZMQ REP socket (ipc:///tmp/pycaas.sock)

pycaas client (PycaasClient)
└── ZMQ REQ socket → get_frame / list_cameras / status / stop
```

## Files

- `/tmp/pycaas.pid` — daemon PID file
- `/tmp/pycaas.log` — daemon log output
- `/tmp/pycaas.sock` — ZMQ IPC socket
