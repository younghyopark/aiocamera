#!/usr/bin/env python3
"""OAK camera viewer over HTTP (MJPEG stream). View at http://<host>:8081"""

import cv2
import depthai as dai
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

PORT = 8081
STREAM_W = 640   # downscale to this width for streaming
STREAM_H = 480   # downscale to this height for streaming

# Pre-encoded JPEGs (encode once in capture thread, not per-client)
latest_jpgs = {}
lock = threading.Lock()


class MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with lock:
                names = list(latest_jpgs.keys())
            html = "<html><body style='background:#111;color:#fff;font-family:sans-serif;text-align:center'>"
            html += "<h2>OAK Camera Streams</h2>"
            if not names:
                html += "<p>Waiting for cameras...</p><script>setTimeout(()=>location.reload(),1000)</script>"
            for name in names:
                html += f"<div><h3>{name}</h3><img src='/stream/{name}' style='max-width:90%'></div>"
            html += "</body></html>"
            self.wfile.write(html.encode())

        elif self.path.startswith("/stream/"):
            cam_name = self.path[len("/stream/"):]
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with lock:
                        jpg = latest_jpgs.get(cam_name)
                    if jpg is not None:
                        # Single write with pre-encoded JPEG
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                        )
                    time.sleep(0.033)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), MJPEGHandler)
    server.request_queue_size = 16
    print(f"🌐 Open http://localhost:{PORT} in your browser (or use your machine's IP)")
    server.serve_forever()


threading.Thread(target=start_server, daemon=True).start()

# OAK camera capture
device = dai.Device()
with dai.Pipeline(device) as pipeline:
    outputQueues = {}
    sockets = device.getConnectedCameras()
    for socket in sockets:
        cam = pipeline.create(dai.node.Camera).build(socket)
        # Request a smaller preview instead of full resolution
        out = cam.requestOutput((STREAM_W, STREAM_H), type=dai.ImgFrame.Type.BGR888p)
        outputQueues[str(socket)] = out.createOutputQueue(maxSize=2, blocking=False)

    print(f"📷 Found {len(sockets)} camera(s): {[str(s) for s in sockets]}")
    pipeline.start()
    while pipeline.isRunning():
        for name in outputQueues.keys():
            queue = outputQueues[name]
            videoIn = queue.tryGet()  # non-blocking — skip if no new frame
            if videoIn is None:
                continue
            assert isinstance(videoIn, dai.ImgFrame)
            frame = videoIn.getCvFrame()
            # Encode JPEG once in capture thread (not per-client)
            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            jpg_bytes = jpg.tobytes()
            with lock:
                latest_jpgs[name] = jpg_bytes
