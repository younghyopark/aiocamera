"""MJPEG web debug server — connects to the running pycaas service and
re-serves camera frames as browser-viewable MJPEG streams."""

from __future__ import annotations

import json
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
        elif self.path == "/markers":
            self._serve_marker_3d()
        elif self.path == "/api/marker_poses":
            self._serve_marker_poses_json()
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

        # Check if calibration is loaded (marker tracking available)
        has_calibration = False
        try:
            markers = client.get_marker_poses(marker_size_m=0.05)
            has_calibration = True
        except Exception:
            pass

        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>pycaas debug</title>"
            "<style>"
            "body{background:#111;color:#eee;font-family:sans-serif;text-align:center;margin:0;padding:20px}"
            ".grid{display:flex;flex-wrap:wrap;justify-content:center;gap:16px}"
            ".card{background:#222;border-radius:8px;padding:12px}"
            ".card h3{margin:0 0 8px}"
            "img{max-width:640px;border-radius:4px}"
            "a{color:#4caf50}"
            "</style></head><body>"
            "<h2>pycaas — live camera streams</h2>"
        )
        if has_calibration:
            html += "<p><a href='/markers'>3D Marker Tracking Viewer</a></p>"
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
                # Frames are RGB from drivers; imencode expects BGR
                if frame.ndim == 3 and frame.shape[2] == 3:
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                data = jpg.tobytes()
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                )
                time.sleep(0.033)  # ~30 fps cap
        except (BrokenPipeError, ConnectionResetError):
            pass


    # ── Marker poses JSON API ──────────────────────────────────────────

    def _serve_marker_poses_json(self) -> None:
        client = _get_client()
        try:
            markers = client.get_marker_poses(marker_size_m=0.05)
            body = json.dumps({"markers": markers}).encode()
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── 3D Marker viewer ─────────────────────────────────────────────────

    def _serve_marker_3d(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>pycaas - 3D Marker Tracking</title>
<style>
body{margin:0;background:#111;color:#eee;font-family:sans-serif}
#info{position:absolute;top:10px;left:10px;z-index:10}
a{color:#4caf50}
#markerList{position:absolute;top:10px;right:10px;z-index:10;background:#222;
            padding:12px;border-radius:8px;min-width:200px;max-height:80vh;overflow-y:auto}
.marker-entry{margin:4px 0;padding:4px 8px;border-radius:4px;background:#333;font-size:0.85em}
</style>
</head><body>
<div id="info">
<a href="/">Back to streams</a>
<h3>Live 3D Marker Tracking</h3>
</div>
<div id="markerList"><b>Detected Markers</b><div id="markers">None yet</div></div>
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/"
  }
}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x111111);

const camera = new THREE.PerspectiveCamera(60, window.innerWidth/window.innerHeight, 0.01, 100);
camera.position.set(0.5, 0.5, 0.5);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.GridHelper(2, 20, 0x444444, 0x333333));
scene.add(new THREE.AxesHelper(0.3));
scene.add(new THREE.AmbientLight(0xffffff, 0.6));

const markerMeshes = {};

function updateMarkers(markers) {
    // Remove old
    for (const id of Object.keys(markerMeshes)) {
        if (!markers.find(m => m.marker_id == id)) {
            scene.remove(markerMeshes[id]);
            delete markerMeshes[id];
        }
    }

    const listEl = document.getElementById('markers');
    let listHtml = '';

    for (const m of markers) {
        const id = m.marker_id;
        const pos = m.position;

        if (!markerMeshes[id]) {
            const group = new THREE.Group();
            const geo = new THREE.BoxGeometry(0.03, 0.03, 0.003);
            const mat = new THREE.MeshLambertMaterial({ color: 0x4caf50 });
            group.add(new THREE.Mesh(geo, mat));
            group.add(new THREE.AxesHelper(0.04));

            // Label
            const canvas = document.createElement('canvas');
            canvas.width = 128; canvas.height = 32;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 20px sans-serif';
            ctx.fillText('ID:' + id, 4, 22);
            const tex = new THREE.CanvasTexture(canvas);
            const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex }));
            sprite.position.set(0, 0.04, 0);
            sprite.scale.set(0.08, 0.02, 1);
            group.add(sprite);

            scene.add(group);
            markerMeshes[id] = group;
        }

        const group = markerMeshes[id];
        group.position.set(pos[0], pos[1], pos[2]);

        // Apply rotation
        const R = m.rotation_matrix;
        const mat4 = new THREE.Matrix4();
        mat4.set(
            R[0][0], R[0][1], R[0][2], pos[0],
            R[1][0], R[1][1], R[1][2], pos[1],
            R[2][0], R[2][1], R[2][2], pos[2],
            0, 0, 0, 1
        );
        group.matrixAutoUpdate = false;
        group.matrix.copy(mat4);

        listHtml += `<div class="marker-entry">
            ID: ${id} | pos: [${pos[0].toFixed(3)}, ${pos[1].toFixed(3)}, ${pos[2].toFixed(3)}]
            | conf: ${m.confidence.toFixed(2)} | seen: ${m.seen_by.join(', ')}
        </div>`;
    }

    listEl.innerHTML = listHtml || 'None detected';
}

async function pollMarkers() {
    try {
        const resp = await fetch('/api/marker_poses');
        const data = await resp.json();
        if (data.markers) updateMarkers(data.markers);
    } catch(e) {}
}

setInterval(pollMarkers, 100);

function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}
animate();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});
</script>
</body></html>"""

        self.wfile.write(html.encode())


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
