"""CLI entry point: pycaas {start,stop,status,web-debug}."""

from __future__ import annotations

import sys
import time

import click


@click.group()
def main() -> None:
    """pycaas — Python Camera as a Service."""


@main.command()
def start() -> None:
    """Start the camera service in the background."""
    from pycaas.daemon import start_daemon

    try:
        pid = start_daemon()
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Service started (PID {pid})")
    click.echo("Log: /tmp/pycaas.log")

    # Give the service a moment to discover cameras, then show status
    time.sleep(2)
    _print_status(quiet_fail=True)


@main.command()
def stop() -> None:
    """Stop the running camera service."""
    from pycaas.daemon import stop_daemon

    try:
        stop_daemon()
        click.echo("Service stopped.")
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command()
def status() -> None:
    """Show status of the camera service."""
    from pycaas.daemon import is_service_running

    if not is_service_running():
        click.echo("Service is not running.")
        sys.exit(1)

    _print_status()


@main.command("web-debug")
@click.argument("port", default=8080, type=int)
def web_debug(port: int) -> None:
    """Launch a browser-viewable MJPEG debug server (blocks until Ctrl+C)."""
    from pycaas.daemon import is_service_running

    if not is_service_running():
        click.echo("Error: service is not running. Run `pycaas start` first.", err=True)
        sys.exit(1)

    from pycaas.web import run_web_debug

    click.echo(f"Web debug server at http://localhost:{port}  (Ctrl+C to stop)")
    run_web_debug(port=port)


# ── helpers ──────────────────────────────────────────────────────────────────

def _print_status(quiet_fail: bool = False) -> None:
    from pycaas.transport import PycaasClient

    try:
        with PycaasClient() as client:
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
        click.echo(f"  [{cam['id']}] {cam['name']}  (type={cam['type']})")
    click.echo(f"Active streams: {', '.join(streams) if streams else '(none yet)'}")
