"""Systemd user service installation, management, status, and logging."""

from __future__ import annotations

import contextlib
import importlib.resources as pkg_resources
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .models import ClutterError, ErrorCode

DEFAULT_UNIT_TEMPLATE = """[Unit]
Description=ClutterXP folder watcher (automatic file sorting)
Documentation=https://example.invalid/clutterxp
After=default.target

[Service]
Type=simple
# `clutterxp service install` replaces the path below with the real executable path.
ExecStart=%h/.local/bin/clutterxp watch --no-color
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=default.target
"""


class ServiceError(ClutterError):
    """Errors encountered during systemd user service management."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.UNKNOWN)


def get_unit_path(systemd_dir: Path | None = None) -> Path:
    """Return destination path for user unit file."""
    if systemd_dir is not None:
        return systemd_dir / "clutterxp.service"
    return Path.home() / ".config" / "systemd" / "user" / "clutterxp.service"


def find_clutterxp_exec() -> str:
    """Find absolute path to clutterxp executable or fallback command."""
    which_cxp = shutil.which("clutterxp")
    if which_cxp:
        return str(Path(which_cxp).resolve())

    if sys.argv and sys.argv[0]:
        argv_p = Path(sys.argv[0])
        if argv_p.is_file() and os.access(argv_p, os.X_OK):
            return str(argv_p.resolve())
        if argv_p.name in ("clutterxp", "cxp"):
            return str(argv_p.resolve())

    return f"{sys.executable} -m clutterxp"


def _load_template(template_path: Path | None = None) -> str:
    """Load the service unit template from disk or package resources."""
    if template_path is not None and template_path.is_file():
        return template_path.read_text(encoding="utf-8")

    # Check relative to working directory or repo root
    cwd_template = Path("packaging/clutterxp.service")
    if cwd_template.is_file():
        return cwd_template.read_text(encoding="utf-8")

    # Check relative to module location
    mod_template = Path(__file__).resolve().parent.parent.parent / "packaging" / "clutterxp.service"
    if mod_template.is_file():
        return mod_template.read_text(encoding="utf-8")

    # Check package resources
    with contextlib.suppress(Exception):
        res = pkg_resources.files("clutterxp").joinpath("packaging/clutterxp.service")
        if res.is_file():
            return res.read_text(encoding="utf-8")

    return DEFAULT_UNIT_TEMPLATE


def render_unit(*, exec_cmd: str | None = None, template_path: Path | None = None) -> str:
    """Render clutterxp.service content with the target executable command."""
    if exec_cmd is None:
        exec_cmd = find_clutterxp_exec()

    raw = _load_template(template_path)

    # Replace ExecStart line
    if re.search(r"^ExecStart=.*$", raw, flags=re.MULTILINE):
        rendered = re.sub(
            r"^ExecStart=.*$",
            f"ExecStart={exec_cmd} watch --no-color",
            raw,
            flags=re.MULTILINE,
        )
    else:
        rendered = re.sub(
            r"(\[Service\]\n)",
            f"\\1ExecStart={exec_cmd} watch --no-color\n",
            raw,
        )

    # Ensure NoNewPrivileges=yes is present (Safety Invariant S10)
    if "NoNewPrivileges=yes" not in rendered:
        rendered = re.sub(
            r"(\[Service\]\n)",
            r"\1NoNewPrivileges=yes\n",
            rendered,
        )

    return rendered


def install(*, systemd_dir: Path | None = None) -> None:
    """Install and enable the systemd user service."""
    unit_path = get_unit_path(systemd_dir)
    unit_path.parent.mkdir(parents=True, exist_ok=True)

    content = render_unit()
    unit_path.write_text(content, encoding="utf-8")
    unit_path.chmod(0o644)

    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise ServiceError("systemctl not found in PATH; cannot enable user service")

    try:
        subprocess.run(  # noqa: S603
            [systemctl, "--user", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(  # noqa: S603
            [systemctl, "--user", "enable", "--now", "clutterxp.service"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err_msg = exc.stderr.strip() if exc.stderr else str(exc)
        raise ServiceError(f"systemctl command failed ({exc.returncode}): {err_msg}") from exc

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "$USER"
    print(
        f"Installed and started clutterxp.service at {unit_path}\n"
        f"Note: To keep the service running after logout, run: loginctl enable-linger {user}"
    )


def uninstall(*, systemd_dir: Path | None = None) -> None:
    """Stop, disable, and remove the systemd user service."""
    unit_path = get_unit_path(systemd_dir)
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run(  # noqa: S603
            [systemctl, "--user", "disable", "--now", "clutterxp.service"],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(  # noqa: S603
            [systemctl, "--user", "daemon-reload"],
            check=False,
            capture_output=True,
            text=True,
        )

    if unit_path.exists():
        unit_path.unlink()
        print(f"Removed unit file at {unit_path}")


def status() -> str:
    """Return status of the clutterxp user service."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise ServiceError("systemctl not found in PATH")

    proc = subprocess.run(  # noqa: S603
        [systemctl, "--user", "status", "clutterxp.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout or proc.stderr


def logs(lines: int = 50) -> str:
    """Return recent journal logs for clutterxp user service."""
    journalctl = shutil.which("journalctl")
    if not journalctl:
        raise ServiceError("journalctl not found in PATH")

    proc = subprocess.run(  # noqa: S603
        [journalctl, "--user", "-u", "clutterxp", "-n", str(lines), "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout or proc.stderr
