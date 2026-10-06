"""CLI entry point: aiocamera {start,stop,status,web-debug,config}."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click

CONFIG_DIR = Path.home() / ".config" / "aiocamera"
CAMERA_CONFIG_FILE = CONFIG_DIR / "camera_configs.json"
# Profiles saved under aiocamera's former name, pycaas, until a profile is saved here.
PYCAAS_CONFIG_FILE = Path.home() / ".config" / "pycaas" / "camera_configs.json"


# ── Profile storage ─────────────────────────────────────────────────────────
#
# File format:
# {
#   "profiles": {
#     "my_setup": {
#       "created": "2026-03-06T10:30:00",
#       "cameras": ["serial1", "serial2"],   # sorted for deterministic matching
#       "configs": {
#         "serial1": {"resolution": [1280, 720], "fps": 30},
#         "serial2": {"resolution": [640, 480], "fps": 30, "exposure": 500}
#       }
#     }
#   }
# }


def _load_profiles() -> dict[str, dict]:
    """Load all saved profiles. Returns {name: profile_dict}."""
    path = CAMERA_CONFIG_FILE if CAMERA_CONFIG_FILE.exists() else PYCAAS_CONFIG_FILE
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    # Migrate old flat format (pre-profile)
    if "profiles" not in data:
        return {}
    return data["profiles"]


def _save_profiles(profiles: dict[str, dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CAMERA_CONFIG_FILE.write_text(json.dumps({"profiles": profiles}, indent=2))


def _find_matching_profile(
    profiles: dict[str, dict], camera_serials: list[str]
) -> tuple[str, dict] | None:
    """Find the most recent profile whose camera set matches exactly.

    Returns (profile_name, profile_dict) or None.
    """
    target = sorted(camera_serials)
    matches: list[tuple[str, dict]] = []
    for name, profile in profiles.items():
        if sorted(profile.get("cameras", [])) == target:
            matches.append((name, profile))
    if not matches:
        return None
    # Pick the most recently created
    matches.sort(key=lambda m: m[1].get("created", ""), reverse=True)
    return matches[0]


def _list_matching_profiles(
    profiles: dict[str, dict], camera_serials: list[str]
) -> list[tuple[str, dict]]:
    """Return all profiles matching the given camera set, newest first."""
    target = sorted(camera_serials)
    matches = [
        (name, p) for name, p in profiles.items()
        if sorted(p.get("cameras", [])) == target
    ]
    matches.sort(key=lambda m: m[1].get("created", ""), reverse=True)
    return matches


# ── CLI ──────────────────────────────────────────────────────────────────────


@click.group()
def main() -> None:
    """aiocamera — camera as a service."""


@main.command()
@click.option("--fresh", is_flag=True, help="Ignore saved profiles and prompt for all settings.")
@click.option("--profile", "profile_name", default=None, help="Use a specific saved profile by name.")
def start(fresh: bool, profile_name: str | None) -> None:
    """Start the camera service in the background."""
    import aiocamera

    try:
        pid = aiocamera.start(fresh=fresh, profile=profile_name)
    except RuntimeError as e:  # e.g. sudo, for a RealSense on macOS, failed
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if pid is None:
        sys.exit(0)


@main.command()
def stop() -> None:
    """Stop the running camera service."""
    import aiocamera

    try:
        aiocamera.stop()
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
def status() -> None:
    """Show status of the camera service."""
    from aiocamera.daemon import is_service_running

    if not is_service_running():
        click.echo("Service is not running.")
        sys.exit(1)

    _print_status()


@main.command("web-debug")
@click.argument("port", default=8080, type=int)
def web_debug(port: int) -> None:
    """Launch a browser-viewable MJPEG debug server (blocks until Ctrl+C)."""
    from aiocamera.daemon import is_service_running

    if not is_service_running():
        click.echo("Error: service is not running. Run `aiocamera start` first.", err=True)
        sys.exit(1)

    from aiocamera.web import run_web_debug

    click.echo(f"Web debug server at http://localhost:{port}  (Ctrl+C to stop)")
    run_web_debug(port=port)


@main.command()
@click.argument("camera_id")
@click.option("--resolution", type=str, default=None, help="WIDTHxHEIGHT (e.g. 1280x720)")
@click.option("--fps", type=int, default=None, help="Frames per second")
@click.option("--exposure", type=str, default=None, help="Exposure value or 'auto'")
def config(camera_id: str, resolution: str | None, fps: int | None, exposure: str | None) -> None:
    """Change camera settings at runtime (while service is running)."""
    from aiocamera.daemon import is_service_running
    from aiocamera.transport import CameraClient

    if not is_service_running():
        click.echo("Error: service is not running. Run `aiocamera start` first.", err=True)
        sys.exit(1)

    client = CameraClient()
    try:
        # Handle exposure change (no restart needed)
        if exposure is not None:
            if exposure.lower() == "auto":
                resp = client.set_exposure(camera_id, None)
            else:
                resp = client.set_exposure(camera_id, int(exposure))
            if "error" in resp:
                click.echo(f"Error setting exposure: {resp['error']}", err=True)
                sys.exit(1)
            click.echo(f"Exposure set to {'auto' if exposure.lower() == 'auto' else exposure}")

        # Handle resolution/fps change (blocking restart)
        if resolution is not None or fps is not None:
            res_tuple = None
            if resolution is not None:
                parts = resolution.split("x")
                if len(parts) != 2:
                    click.echo("Error: resolution must be WIDTHxHEIGHT (e.g. 1280x720)", err=True)
                    sys.exit(1)
                res_tuple = (int(parts[0]), int(parts[1]))

            click.echo("Reconfiguring (this may take a few seconds)...")
            resp = client.set_config(camera_id, resolution=res_tuple, fps=fps)
            if "error" in resp:
                click.echo(f"Error: {resp['error']}", err=True)
                sys.exit(1)
            click.echo("Reconfigured successfully.")

        # Show current status
        if resolution is None and fps is None and exposure is None:
            # No options given — show supported modes
            modes = client.get_supported_modes(camera_id)
            if modes is None:
                click.echo(f"Camera {camera_id} does not report supported modes.")
            else:
                click.echo(f"Supported modes for {camera_id}:")
                for m in modes:
                    click.echo(f"  {m['width']}x{m['height']} @ {m['fps']}fps")
    finally:
        client.close()


@main.command()
def profiles() -> None:
    """List all saved configuration profiles."""
    all_profiles = _load_profiles()
    if not all_profiles:
        click.echo("No saved profiles.")
        return
    for name, profile in sorted(all_profiles.items(), key=lambda x: x[1].get("created", ""), reverse=True):
        created = profile.get("created", "unknown")
        cameras = profile.get("cameras", [])
        click.echo(f"  {name}  ({created}, {len(cameras)} camera(s))")
        for serial, cfg in profile.get("configs", {}).items():
            res = cfg.get("resolution", [0, 0])
            fps = cfg.get("fps", "?")
            exp = cfg.get("exposure")
            exp_str = "auto" if exp is None else str(exp)
            click.echo(f"    {serial}: {res[0]}x{res[1]} @ {fps}fps, exposure={exp_str}")


# ── helpers ──────────────────────────────────────────────────────────────────

def _print_status(quiet_fail: bool = False) -> None:
    from aiocamera.transport import CameraClient

    try:
        client = CameraClient()
        info = client.status()
    except Exception:
        if quiet_fail:
            return
        click.echo("Could not connect to service.", err=True)
        sys.exit(1)

    click.echo(f"Uptime: {info['uptime']}s")
    cameras = info.get("cameras", [])
    streams = info.get("active_streams", [])
    click.echo(f"Cameras: {len(cameras)}")
    for cam in cameras:
        exp_str = ""
        try:
            exp = client.get_exposure(cam["id"])
            if exp is not None:
                exp_str = f", exposure={'auto' if exp['auto'] else exp['value']}"
        except Exception:
            pass
        click.echo(f"  [{cam['id']}] {cam['name']}  (type={cam['type']}{exp_str})")
    stream_details = info.get("streams", {})
    if streams:
        click.echo("Active streams:")
        for s in streams:
            res = stream_details.get(s, {}).get("resolution")
            res_str = f"  {res[0]}x{res[1]}" if res else ""
            click.echo(f"  {s}{res_str}")
    else:
        click.echo("Active streams: (none yet)")
    client.close()
