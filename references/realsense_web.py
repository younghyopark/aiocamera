#!/usr/bin/env python3
"""RealSense camera viewer over HTTP (MJPEG stream). View at http://<host>:8082"""

import cv2
import numpy as np
import pyrealsense2 as rs
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

PORT = 8082
STREAM_W = 640
STREAM_H = 480
FPS = 30

# Pre-encoded JPEGs (encode once in capture thread, not per-client)
latest_jpgs = {}
lock = threading.Lock()


def colorize_depth(depth_frame):
    """Convert depth frame to a colorized image for visualization."""
    depth_image = np.asanyarray(depth_frame.get_data())
    depth_colormap = cv2.applyColorMap(
        cv2.convertScaleAbs(depth_image, alpha=0.03), cv2.COLORMAP_JET
    )
    return depth_colormap


class MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with lock:
                names = sorted(latest_jpgs.keys())
            html = (
                "<html><body style='background:#111;color:#fff;font-family:sans-serif;text-align:center'>"
                "<h2>RealSense Camera Streams</h2>"
            )
            if not names:
                html += "<p>Waiting for cameras...</p><script>setTimeout(()=>location.reload(),1000)</script>"
            html += "<div style='display:flex;flex-wrap:wrap;justify-content:center;gap:16px'>"
            for name in names:
                html += (
                    f"<div><h3>{name}</h3>"
                    f"<img src='/stream/{name}' style='max-width:640px'></div>"
                )
            html += "</div></body></html>"
            self.wfile.write(html.encode())

        elif self.path.startswith("/stream/"):
            stream_name = self.path[len("/stream/"):]
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with lock:
                        jpg = latest_jpgs.get(stream_name)
                    if jpg is not None:
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


def capture_device(serial, index):
    """Capture loop for a single RealSense device."""
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, STREAM_W, STREAM_H, rs.format.bgr8, FPS)
    config.enable_stream(rs.stream.depth, STREAM_W, STREAM_H, rs.format.z16, FPS)

    pipeline = rs.pipeline()
    try:
        profile = pipeline.start(config)
        device = profile.get_device()
        name = device.get_info(rs.camera_info.name)
        print(f"  📷 [{index}] {name} (serial: {serial})")

        # Let auto-exposure settle
        for _ in range(30):
            pipeline.wait_for_frames()

        align = rs.align(rs.stream.color)

        while True:
            frames = pipeline.wait_for_frames()
            aligned = align.process(frames)

            color_frame = aligned.get_color_frame()
            depth_frame = aligned.get_depth_frame()

            if color_frame:
                color_img = np.asanyarray(color_frame.get_data())
                _, jpg = cv2.imencode(".jpg", color_img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                with lock:
                    latest_jpgs[f"cam{index}_color"] = jpg.tobytes()

            if depth_frame:
                depth_img = colorize_depth(depth_frame)
                _, jpg = cv2.imencode(".jpg", depth_img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                with lock:
                    latest_jpgs[f"cam{index}_depth"] = jpg.tobytes()

    except Exception as e:
        print(f"  ❌ Device {serial} error: {e}")
    finally:
        pipeline.stop()


def main():
    # Detect all connected RealSense devices
    ctx = rs.context()
    devices = ctx.query_devices()

    if len(devices) == 0:
        print("❌ No RealSense devices found!")
        return

    print(f"🔍 Found {len(devices)} RealSense device(s):")
    serials = []
    for i, dev in enumerate(devices):
        serial = dev.get_info(rs.camera_info.serial_number)
        name = dev.get_info(rs.camera_info.name)
        print(f"  [{i}] {name} — serial: {serial}")
        serials.append(serial)

    # Start HTTP server
    threading.Thread(target=start_server, daemon=True).start()

    # Start a capture thread per device
    threads = []
    for i, serial in enumerate(serials):
        t = threading.Thread(target=capture_device, args=(serial, i), daemon=True)
        t.start()
        threads.append(t)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Shutting down")


if __name__ == "__main__":
    main()
