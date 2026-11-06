"""Tests for systemd service installation, unit template, wheel metadata, and CI."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from clutterxp.service_install import (
    ServiceError,
    _load_template,
    find_clutterxp_exec,
    get_unit_path,
    install,
    logs,
    render_unit,
    status,
    uninstall,
)


# --------------------------------------------------------------------------- Acceptance Tests
def test_A66_service_install_creates_user_unit(tmp_path: Path) -> None:
    """A66: service install/uninstall/status/logs manage the systemd user unit."""
    systemd_dir = tmp_path / ".config" / "systemd" / "user"
    mock_systemctl = "/bin/systemctl"
    mock_journalctl = "/bin/journalctl"

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        check: bool = False,
        capture_output: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="active (running)\n")

    def fake_which(cmd: str) -> str | None:
        if cmd == "systemctl":
            return mock_systemctl
        if cmd == "journalctl":
            return mock_journalctl
        if cmd == "clutterxp":
            return "/usr/local/bin/clutterxp"
        return None

    # 1. Install
    with (
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run", side_effect=fake_run),
    ):
        install(systemd_dir=systemd_dir)

    unit_file = systemd_dir / "clutterxp.service"
    assert unit_file.is_file(), "clutterxp.service unit file must exist"

    # Verify file permissions: 0644
    mode = stat.S_IMODE(unit_file.stat().st_mode)
    assert mode == 0o644, f"Unit file permissions should be 0644, got {oct(mode)}"

    content = unit_file.read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/clutterxp watch --no-color" in content
    assert "NoNewPrivileges=yes" in content
    assert "Restart=on-failure" in content

    # Verify subprocess calls
    assert [mock_systemctl, "--user", "daemon-reload"] in calls
    assert [mock_systemctl, "--user", "enable", "--now", "clutterxp.service"] in calls

    # 2. Status
    calls.clear()
    with (
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run", side_effect=fake_run),
    ):
        st = status()
        assert "active (running)" in st
        assert calls == [[mock_systemctl, "--user", "status", "clutterxp.service"]]

    # 3. Logs
    calls.clear()
    with (
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run", side_effect=fake_run),
    ):
        lg = logs(lines=25)
        assert "active (running)" in lg
        assert calls == [[mock_journalctl, "--user", "-u", "clutterxp", "-n", "25", "--no-pager"]]

    # 4. Uninstall
    calls.clear()
    with (
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run", side_effect=fake_run),
    ):
        uninstall(systemd_dir=systemd_dir)

    assert not unit_file.exists(), "Unit file must be unlinked after uninstall"
    assert [mock_systemctl, "--user", "disable", "--now", "clutterxp.service"] in calls
    assert [mock_systemctl, "--user", "daemon-reload"] in calls


def test_A68_cli_entry_points_installed() -> None:
    """A68: The wheel installs and clutterxp and cxp entry points are configured."""
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_file = repo_root / "pyproject.toml"
    assert pyproject_file.is_file(), "pyproject.toml must exist"

    content = pyproject_file.read_text(encoding="utf-8")
    assert 'clutterxp = "clutterxp.cli.app:main"' in content
    assert 'cxp = "clutterxp.cli.app:main"' in content


def test_A70_systemd_unit_valid() -> None:
    """A70: Systemd unit file template is valid and contains required security options."""
    repo_root = Path(__file__).resolve().parent.parent
    service_template = repo_root / "packaging" / "clutterxp.service"
    assert service_template.is_file(), "packaging/clutterxp.service must exist"

    text = service_template.read_text(encoding="utf-8")
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "NoNewPrivileges=yes" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "WantedBy=default.target" in text

    # Render unit with custom executable
    rendered = render_unit(exec_cmd="/opt/clutterxp/bin/clutterxp")
    assert "ExecStart=/opt/clutterxp/bin/clutterxp watch --no-color" in rendered
    assert "NoNewPrivileges=yes" in rendered


def test_A77_ci_matrix_configured() -> None:
    """A77: CI matrix tests Python 3.10 through 3.13 and includes wheel-install verification."""
    repo_root = Path(__file__).resolve().parent.parent
    ci_file = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_file.is_file(), ".github/workflows/ci.yml must exist"

    yaml = YAML(typ="safe")
    ci_data = yaml.load(ci_file.read_text(encoding="utf-8"))

    # Verify python matrix
    test_matrix = ci_data["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    assert "3.10" in test_matrix
    assert "3.11" in test_matrix
    assert "3.12" in test_matrix
    assert "3.13" in test_matrix

    # Verify wheel-install job exists
    assert "wheel-install" in ci_data["jobs"]
    wheel_steps = ci_data["jobs"]["wheel-install"]["steps"]
    step_commands = " ".join(s.get("run", "") for s in wheel_steps)
    assert "build" in step_commands
    assert "smoke_readme.sh" in step_commands


def test_A78_smoke_test_script_passes() -> None:
    """A78: README smoke test script exists, is executable, and runs cleanly."""
    repo_root = Path(__file__).resolve().parent.parent
    smoke_script = repo_root / "scripts" / "smoke_readme.sh"
    assert smoke_script.is_file(), "scripts/smoke_readme.sh must exist"

    # Must be executable
    is_exec = os.access(smoke_script, os.X_OK)
    assert is_exec, "scripts/smoke_readme.sh must have executable bit (+x)"

    # Execute smoke test script
    proc = subprocess.run(
        [str(smoke_script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"smoke_readme.sh failed ({proc.returncode}):\n{proc.stderr}"
    assert "Running ClutterXP README quickstart smoke test" in proc.stdout


# --------------------------------------------------------------------------- Safety Invariant Tests
def test_S4_service_uninstall_only_deletes_unit_file(tmp_path: Path) -> None:
    """S4: Uninstall unlinks only clutterxp.service, preserving user and sibling files."""
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    unit_file = systemd_dir / "clutterxp.service"
    unit_file.write_text("[Service]\n", encoding="utf-8")

    sibling_file = systemd_dir / "other.service"
    sibling_file.write_text("[Unit]\nDescription=Other\n", encoding="utf-8")

    with patch("shutil.which", return_value=None):
        uninstall(systemd_dir=systemd_dir)

    assert not unit_file.exists()
    assert sibling_file.exists(), "Sibling service files must not be touched"


def test_S10_systemd_unit_drops_privileges() -> None:
    """S10: Systemd service unit enforces NoNewPrivileges=yes and targets user directory."""
    default_path = get_unit_path()
    assert not str(default_path).startswith("/etc/systemd"), "Service must not install in /etc"
    assert ".config/systemd/user" in str(default_path)

    rendered = render_unit(exec_cmd="/bin/clutterxp")
    assert "NoNewPrivileges=yes" in rendered


def test_S12_unit_file_permissions(tmp_path: Path) -> None:
    """S12: Unit file is written with 0644 mode."""
    unit_path = tmp_path / "clutterxp.service"
    with (
        patch("shutil.which", return_value="/bin/systemctl"),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        install(systemd_dir=tmp_path)

    st = unit_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o644


def test_no_shell_true_ast() -> None:
    """Invariant: shell=True is NEVER used anywhere in service_install.py."""
    repo = Path(__file__).resolve().parent.parent
    service_file = repo / "src" / "clutterxp" / "service_install.py"
    assert service_file.is_file()

    tree = ast.parse(service_file.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant):
                        assert kw.value.value is not True, (
                            f"shell=True found at line {node.lineno} in service_install.py"
                        )
                    else:
                        pytest.fail(f"Dynamic shell argument found at line {node.lineno}")


# --------------------------------------------------------------------------- Error Handling
def test_missing_systemctl_raises_service_error(tmp_path: Path) -> None:
    """install and status raise ServiceError when systemctl is missing."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(ServiceError, match="systemctl not found in PATH"):
            install(systemd_dir=tmp_path)

        with pytest.raises(ServiceError, match="systemctl not found in PATH"):
            status()


def test_missing_journalctl_raises_service_error() -> None:
    """logs raises ServiceError when journalctl is missing."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(ServiceError, match="journalctl not found in PATH"),
    ):
        logs()


def test_systemctl_failure_raises_service_error(tmp_path: Path) -> None:
    """Failed systemctl execution raises ServiceError with command output."""
    with (
        patch("shutil.which", return_value="/bin/systemctl"),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd=["systemctl"],
                stderr="Failed to connect to bus: No such file or directory",
            ),
        ),
        pytest.raises(ServiceError, match="Failed to connect to bus"),
    ):
        install(systemd_dir=tmp_path)


def test_find_clutterxp_exec_fallbacks(tmp_path: Path) -> None:
    """find_clutterxp_exec falls back to sys.argv or sys.executable -m clutterxp."""
    # 1. When which succeeds
    with patch("shutil.which", return_value="/usr/bin/clutterxp"):
        assert find_clutterxp_exec() == str(Path("/usr/bin/clutterxp").resolve())

    # 2. When which is None, but sys.argv[0] is executable
    fake_exe = tmp_path / "clutterxp"
    fake_exe.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_exe.chmod(0o755)
    with (
        patch("shutil.which", return_value=None),
        patch("sys.argv", [str(fake_exe)]),
    ):
        cmd = find_clutterxp_exec()
        assert cmd == str(fake_exe.resolve())

    # 3. When which is None and sys.argv[0] is not executable but named 'cxp'
    fake_script = tmp_path / "cxp"
    with (
        patch("shutil.which", return_value=None),
        patch("sys.argv", [str(fake_script)]),
    ):
        cmd = find_clutterxp_exec()
        assert cmd == str(fake_script.resolve())

    # 4. When which is None and sys.argv[0] is unrelated
    with (
        patch("shutil.which", return_value=None),
        patch("sys.argv", ["pytest"]),
    ):
        cmd = find_clutterxp_exec()
        assert cmd == f"{sys.executable} -m clutterxp"


def test_render_unit_edge_cases(tmp_path: Path) -> None:
    """render_unit handles templates without ExecStart or NoNewPrivileges."""
    # 1. Custom template file
    custom_tpl = tmp_path / "custom.service"
    custom_tpl.write_text(
        "[Unit]\nDescription=Test\n\n[Service]\nType=simple\n\n[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    rendered = render_unit(exec_cmd="/bin/custom", template_path=custom_tpl)
    assert "ExecStart=/bin/custom watch --no-color" in rendered
    assert "NoNewPrivileges=yes" in rendered

    # 2. Template already has NoNewPrivileges
    tpl_with_priv = tmp_path / "priv.service"
    tpl_with_priv.write_text(
        "[Unit]\nDescription=Test\n\n[Service]\nNoNewPrivileges=yes\nExecStart=old\n",
        encoding="utf-8",
    )
    rendered_priv = render_unit(exec_cmd="/bin/test", template_path=tpl_with_priv)
    assert rendered_priv.count("NoNewPrivileges=yes") == 1
    assert "ExecStart=/bin/test watch --no-color" in rendered_priv


def test_uninstall_nonexistent_unit_is_noop(tmp_path: Path) -> None:
    """uninstall cleanly succeeds when unit file does not exist."""
    with (
        patch("shutil.which", return_value="/bin/systemctl"),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        # Should not raise
        uninstall(systemd_dir=tmp_path)


def test_render_unit_fallback_template() -> None:
    """render_unit uses DEFAULT_UNIT_TEMPLATE if no file template exists."""
    non_existent = Path("/nonexistent/clutterxp.service")
    rendered = render_unit(exec_cmd="/custom/cxp", template_path=non_existent)
    assert "ExecStart=/custom/cxp watch --no-color" in rendered
    assert "NoNewPrivileges=yes" in rendered
    assert "WantedBy=default.target" in rendered


def test_load_template_fallback_search(tmp_path: Path) -> None:
    """_load_template traverses fallback paths when cwd has no template."""
    real_is_file = Path.is_file

    def mock_is_file(self: Path) -> bool:
        if str(self) == "packaging/clutterxp.service":
            return False
        return real_is_file(self)

    with patch.object(Path, "is_file", mock_is_file):
        tpl = _load_template(None)
        assert "[Service]" in tpl


def test_wheel_package_data_included(tmp_path: Path) -> None:
    """Wheel packaging includes py.typed, schema.sql, config.example.yaml, and clutterxp.service."""
    import zipfile

    dist_dir = tmp_path / "dist"
    repo_root = Path(__file__).resolve().parent.parent

    # Build wheel into tmp_path
    cmd = [
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--outdir",
        str(dist_dir),
    ]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Wheel build failed:\n{proc.stderr}\n{proc.stdout}"

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, "Exactly one wheel should have been produced"

    with zipfile.ZipFile(wheels[0]) as zf:
        namelist = zf.namelist()
        assert "clutterxp/py.typed" in namelist
        assert "clutterxp/history/schema.sql" in namelist
        assert "clutterxp/config.example.yaml" in namelist
        assert "clutterxp/packaging/clutterxp.service" in namelist

        # Verify entry points in wheel
        entry_points = [n for n in namelist if n.endswith("entry_points.txt")]
        assert len(entry_points) == 1
        ep_text = zf.read(entry_points[0]).decode("utf-8")
        assert "clutterxp = clutterxp.cli.app:main" in ep_text
        assert "cxp = clutterxp.cli.app:main" in ep_text

        # Verify metadata has ruamel.yaml.clib
        metadata_files = [n for n in namelist if n.endswith("METADATA")]
        assert len(metadata_files) == 1
        meta_text = zf.read(metadata_files[0]).decode("utf-8")
        assert "ruamel-yaml-clib" in meta_text
