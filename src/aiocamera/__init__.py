"""aiocamera — camera as a service."""

from __future__ import annotations

__version__ = "0.1.0"

import numpy as np

from aiocamera.transport import CameraClient

# ── Lazy singleton client for module-level API ──────────────────────────────

_client: CameraClient | None = None


def _get_client() -> CameraClient:
    """Return (and lazily create) the module-level client singleton."""
    global _client
    if _client is None:
        _client = CameraClient()
    return _client


def connect(endpoint: str | None = None, timeout_ms: int = 3000) -> None:
    """Explicitly create the module-level client (optional — happens lazily).

    Call this if you need a custom endpoint or timeout.
    """
    global _client
    if _client is not None:
        _client.close()
    kwargs: dict = {"timeout_ms": timeout_ms}
    if endpoint is not None:
        kwargs["endpoint"] = endpoint
    _client = CameraClient(**kwargs)


def disconnect() -> None:
    """Close the module-level client."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ── Forwarded client methods ────────────────────────────────────────────────


def list_cameras() -> list[dict]:
    return _get_client().list_cameras()


def get_frame(
    camera_id: str,
    resolution: tuple[int, int] | None = None,
) -> np.ndarray:
    return _get_client().get_frame(camera_id, resolution=resolution)


def get_exposure(camera_id: str) -> dict | None:
    return _get_client().get_exposure(camera_id)


def get_intrinsics(camera_id: str) -> dict:
    return _get_client().get_intrinsics(camera_id)


def get_intrinsics_matrix(camera_id: str) -> np.ndarray:
    return _get_client().get_intrinsics_matrix(camera_id)


def set_exposure(camera_id: str, value: int | None = None) -> dict:
    return _get_client().set_exposure(camera_id, value=value)


def set_resolution(width: int, height: int) -> None:
    """Set a default resolution for all subsequent module-level get_frame() calls."""
    _get_client().set_resolution(width, height)


def set_config(
    camera_id: str,
    resolution: tuple[int, int] | None = None,
    fps: int | None = None,
    timeout_ms: int = 15000,
) -> dict:
    return _get_client().set_config(
        camera_id, resolution=resolution, fps=fps, timeout_ms=timeout_ms,
    )


def get_supported_modes(camera_id: str) -> list[dict] | None:
    return _get_client().get_supported_modes(camera_id)


def status() -> dict:
    return _get_client().status()


# ── Service lifecycle ───────────────────────────────────────────────────────


def start(
    fresh: bool = False,
    camera_configs: dict[str, dict] | None = None,
    profile: str | None = None,
) -> int | None:
    """Start the camera service in the background.

    Args:
        fresh: If True, ignore saved profiles and prompt for all settings.
        camera_configs: Explicit per-camera configs keyed by camera ID
            (e.g. ``{"rs_12345": {"resolution": [1280, 720], "fps": 30}}``).
            If provided, skips interactive prompts entirely.
        profile: Use a specific saved profile by name.

    Returns:
        The daemon PID, or None if the service was already running.
    """
    from aiocamera.daemon import is_service_running, start_daemon

    if is_service_running():
        print("aiocamera service is already running.")
        return None

    if camera_configs is None:
        camera_configs = _build_camera_configs(fresh=fresh, profile_name=profile)

    pid = start_daemon(camera_configs=camera_configs or None)
    print(f"aiocamera service started (PID {pid})")
    print("Log: /tmp/aiocamera.log")

    # Wait for cameras to come online
    import time
    time.sleep(2)

    try:
        with CameraClient() as client:
            info = client.status()
        streams = info.get("active_streams", [])
        cameras = info.get("cameras", [])
        print(f"Cameras: {len(cameras)}, Streams: {len(streams)}")
        for s in streams:
            res = info.get("streams", {}).get(s, {}).get("resolution")
            res_str = f"  {res[0]}x{res[1]}" if res else ""
            print(f"  {s}{res_str}")
    except Exception:
        pass

    return pid


def stop() -> None:
    """Stop the running camera service."""
    from aiocamera.daemon import is_service_running, stop_daemon

    if not is_service_running():
        print("aiocamera service is not running.")
        return

    stop_daemon()
    print("aiocamera service stopped.")


def _build_camera_configs(
    fresh: bool = False,
    profile_name: str | None = None,
) -> dict[str, dict]:
    """Interactively build camera configs (RealSense resolution/fps/exposure).

    Profile behavior:
    - ``profile_name`` given: load that specific profile (error if not found or
      cameras don't match).
    - ``fresh=False`` (default): find the most recent profile matching the
      current camera set. If found, use it. Otherwise prompt interactively.
    - ``fresh=True``: always prompt interactively, then offer to save.
    """
    from datetime import datetime

    from aiocamera.cli import (
        _find_matching_profile,
        _list_matching_profiles,
        _load_profiles,
        _save_profiles,
    )

    camera_configs: dict[str, dict] = {}
    try:
        from aiocamera.discovery import (
            _discover_realsense,
            query_realsense_exposure_range,
            query_realsense_resolutions,
        )

        rs_cameras = _discover_realsense()
        camera_serials = [cam.serial for cam in rs_cameras]
        profiles = _load_profiles()

        # --- Try to load a profile ---
        if profile_name is not None:
            # Explicit profile requested
            if profile_name not in profiles:
                print(f"Error: profile '{profile_name}' not found.")
                print("Available profiles:")
                for name in profiles:
                    print(f"  {name}")
                return {}
            p = profiles[profile_name]
            if sorted(p.get("cameras", [])) != sorted(camera_serials):
                print(f"Error: profile '{profile_name}' was saved for a different camera set.")
                print(f"  Profile cameras: {p.get('cameras', [])}")
                print(f"  Connected cameras: {camera_serials}")
                return {}
            print(f"Using profile '{profile_name}' (saved {p.get('created', 'unknown')})")
            for cam in rs_cameras:
                cfg = p["configs"].get(cam.serial, {})
                if cfg:
                    res = cfg.get("resolution", [0, 0])
                    exp = cfg.get("exposure")
                    exp_label = "auto" if exp is None else exp
                    print(f"  [{cam.id}] {cam.name}: {res[0]}x{res[1]} @ {cfg.get('fps', '?')}fps, exposure={exp_label}")
                    camera_configs[cam.id] = cfg
            return camera_configs

        if not fresh and camera_serials:
            match = _find_matching_profile(profiles, camera_serials)
            if match is not None:
                name, p = match
                created = p.get("created", "unknown")
                print(f"Using profile '{name}' (saved {created})")
                for cam in rs_cameras:
                    cfg = p["configs"].get(cam.serial, {})
                    if cfg:
                        res = cfg.get("resolution", [0, 0])
                        exp = cfg.get("exposure")
                        exp_label = "auto" if exp is None else exp
                        print(f"  [{cam.id}] {cam.name}: {res[0]}x{res[1]} @ {cfg.get('fps', '?')}fps, exposure={exp_label}")
                        camera_configs[cam.id] = cfg

                # Show if other profiles exist for this camera set
                all_matches = _list_matching_profiles(profiles, camera_serials)
                if len(all_matches) > 1:
                    print(f"  ({len(all_matches)} profiles available for this setup — use --profile <name> to switch)")
                return camera_configs

        # --- No matching profile or fresh mode — prompt interactively ---
        configs_by_serial: dict[str, dict] = {}

        for cam in rs_cameras:
            all_res = query_realsense_resolutions(cam.serial)
            wh_max_fps: dict[tuple[int, int], int] = {}
            for w, h, fps in all_res:
                key = (w, h)
                if key not in wh_max_fps or fps > wh_max_fps[key]:
                    wh_max_fps[key] = fps
            options = sorted(wh_max_fps.items(), key=lambda x: x[0][0] * x[0][1], reverse=True)
            if not options:
                continue

            print(f"[{cam.id}] {cam.name} (serial: {cam.serial})")
            print("  Available resolutions:")
            for i, ((w, h), max_fps) in enumerate(options, 1):
                print(f"    {i}) {w}x{h}  (up to {max_fps}fps)")

            while True:
                try:
                    choice = input(f"  Select resolution [1-{len(options)}] (default 1): ").strip()
                    res_idx = int(choice) - 1 if choice else 0
                    if 0 <= res_idx < len(options):
                        break
                except ValueError:
                    pass
                print(f"  Please enter a number between 1 and {len(options)}")
            chosen_wh = options[res_idx][0]

            available_fps = sorted(
                {fps for w, h, fps in all_res if (w, h) == chosen_wh},
                reverse=True,
            )
            if len(available_fps) > 1:
                fps_str = ", ".join(str(f) for f in available_fps)
                while True:
                    choice = input(f"  Select fps [{fps_str}] (default {available_fps[0]}): ").strip()
                    if not choice:
                        chosen_fps = available_fps[0]
                        break
                    try:
                        chosen_fps = int(choice)
                        if chosen_fps in available_fps:
                            break
                    except ValueError:
                        pass
                    print(f"  Please select one of: {fps_str}")
            else:
                chosen_fps = available_fps[0]

            chosen_exposure = None
            exp_range = query_realsense_exposure_range(cam.serial)
            if exp_range is not None:
                choice = input(
                    f"  Exposure: auto or manual ({exp_range['min']}-{exp_range['max']}, "
                    f"default={exp_range['default']}) [auto/manual] (default auto): "
                ).strip().lower()
                if choice == "manual":
                    while True:
                        val = input(
                            f"  Exposure value ({exp_range['min']}-{exp_range['max']}, "
                            f"default {exp_range['default']}): "
                        ).strip()
                        try:
                            chosen_exposure = int(val) if val else exp_range["default"]
                            if exp_range["min"] <= chosen_exposure <= exp_range["max"]:
                                break
                        except ValueError:
                            pass
                        print(f"  Please enter a value between {exp_range['min']} and {exp_range['max']}")

            cfg: dict = {"resolution": list(chosen_wh), "fps": chosen_fps}
            if chosen_exposure is not None:
                cfg["exposure"] = chosen_exposure

            camera_configs[cam.id] = cfg
            configs_by_serial[cam.serial] = cfg

            exp_label = "auto" if chosen_exposure is None else chosen_exposure
            print(f"  -> {chosen_wh[0]}x{chosen_wh[1]} @ {chosen_fps}fps, exposure={exp_label}")
            print()

        # --- Offer to save as a profile ---
        if configs_by_serial:
            save = input("Save this configuration as a profile? [y/N]: ").strip().lower()
            if save in ("y", "yes"):
                while True:
                    name = input("  Profile name: ").strip()
                    if name and name not in profiles:
                        break
                    if name in profiles:
                        overwrite = input(f"  '{name}' already exists. Overwrite? [y/N]: ").strip().lower()
                        if overwrite in ("y", "yes"):
                            break
                    if not name:
                        print("  Please enter a name.")

                profiles[name] = {
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cameras": sorted(camera_serials),
                    "configs": configs_by_serial,
                }
                _save_profiles(profiles)
                print(f"  Saved as '{name}'")

    except Exception:
        pass  # Fall back to driver defaults if discovery fails

    return camera_configs


__all__ = [
    "CameraClient",
    "connect",
    "disconnect",
    "get_exposure",
    "get_frame",
    "get_intrinsics",
    "get_intrinsics_matrix",
    "get_supported_modes",
    "list_cameras",
    "set_config",
    "set_exposure",
    "set_resolution",
    "start",
    "status",
    "stop",
    "__version__",
]
