"""MJPEG web debug server — connects to the running pycaas service and
re-serves camera frames as browser-viewable MJPEG streams."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import unquote

import cv2

from pycaas.transport import PycaasClient

# Thread-local client to avoid ZMQ socket sharing across threads
_local_clients: dict[int, PycaasClient] = {}
_lock = threading.Lock()


def _get_client() -> PycaasClient:
    tid = threading.get_ident()
    with _lock:
        if tid not in _local_clients:
            _local_clients[tid] = PycaasClient(timeout_ms=2000)
        return _local_clients[tid]


class _MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass  # silence per-request logs

    def do_GET(self) -> None:
        if self.path == "/":
            self._serve_index()
        elif self.path.startswith("/stream/"):
            self._serve_mjpeg(unquote(self.path[len("/stream/"):]))
        else:
            self.send_error(404)

    # ── index page ──────────────────────────────────────────────────────

    def _serve_index(self) -> None:
        client = _get_client()
        try:
            info = client.status()
        except Exception:
            self.send_error(502, "Cannot reach pycaas service")
            return

        streams = info.get("active_streams", [])

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>pycaas debug</title>"
            "<style>"
            "body{background:#111;color:#eee;font-family:sans-serif;text-align:center;margin:0;padding:20px}"
            ".grid{display:flex;flex-wrap:wrap;justify-content:center;gap:16px}"
            ".card{background:#222;border-radius:8px;padding:12px}"
            ".card h3{margin:0 0 8px}"
            "img{max-width:640px;border-radius:4px}"
            "</style></head><body>"
            "<h2>pycaas — live camera streams</h2>"
        )
        if not streams:
            html += "<p>No active streams yet.</p>"
            html += "<script>setTimeout(()=>location.reload(),2000)</script>"
        else:
            html += "<div class='grid'>"
            for s in streams:
                html += (
                    f"<div class='card'><h3>{s}</h3>"
                    f"<img src='/stream/{s}'></div>"
                )
            html += "</div>"
        html += "</body></html>"
        self.wfile.write(html.encode())

    # ── MJPEG stream for a single camera stream ────────────────────────

    def _serve_mjpeg(self, stream_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        client = _get_client()
        try:
            while True:
                try:
                    frame = client.get_frame(stream_id)
                except Exception:
                    time.sleep(0.1)
                    continue
                _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                data = jpg.tobytes()
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                )
                time.sleep(0.033)  # ~30 fps cap
        except (BrokenPipeError, ConnectionResetError):
            pass


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_web_debug(port: int = 8080) -> None:
    server = _ThreadedHTTPServer(("0.0.0.0", port), _MJPEGHandler)
    server.request_queue_size = 16
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
