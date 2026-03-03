"""Example: grab frames from the running pycaas service and save to disk."""

import cv2
from pycaas import PycaasClient

client = PycaasClient()

# List available cameras
cameras = client.list_cameras()
print("Cameras:", cameras)

# List active streams
status = client.status()
print("Streams:", status["active_streams"])

# Grab full-resolution frame from each stream
for stream_id in status["active_streams"]:
    frame = client.get_frame(stream_id)
    filename = f"{stream_id}_full.jpg"
    cv2.imwrite(filename, frame)
    print(f"{stream_id}: {frame.shape} -> {filename}")

# Grab at a specific resolution (per-request override)
for stream_id in status["active_streams"]:
    frame = client.get_frame(stream_id, resolution=(320, 240))
    filename = f"{stream_id}_320x240.jpg"
    cv2.imwrite(filename, frame)
    print(f"{stream_id}: {frame.shape} -> {filename}")

# Or set a default resolution for all subsequent calls
client.set_resolution(640, 480)
for stream_id in status["active_streams"]:
    frame = client.get_frame(stream_id)
    filename = f"{stream_id}_640x480.jpg"
    cv2.imwrite(filename, frame)
    print(f"{stream_id}: {frame.shape} -> {filename}")

client.close()
