# 11: Packaging and Distribution

## 1. Package

- Build backend: `hatchling`; layout `src/clutterxp`; version from `src/clutterxp/__init__.py`.
- Python `>=3.10`. Runtime dependencies: `typer`, `pydantic>=2`, `ruamel.yaml`, `watchdog>=4`, `platformdirs`.
- Extras: `[gui]` = PySide6, `[dev]` = test and lint tooling.
- Entry points: `clutterxp` and `cxp` (console), `clutterxp-gui` (GUI, optional).
- Semver. Pre-1.0 minor bumps may break config; each break ships a `config migrate` step.

## 2. Install on Kali (and other Linux)

Kali (like Debian 12+) marks the system Python as *externally managed* (PEP 668), so do **not** `pip install` globally.

```bash
sudo apt install pipx python3-venv     # once
pipx ensurepath
pipx install .                         # from the project folder (user install, isolated)
clutterxp --version
clutterxp init
```

Developer setup:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## 3. Background service

`packaging/clutterxp.service` is the template used by `clutterxp service install`:

- `Type=simple`, `ExecStart=<abs path to clutterxp> watch --no-color`
- `Restart=on-failure`, `RestartSec=5`
- `NoNewPrivileges=yes`
- `WantedBy=default.target`

`systemctl --user enable --now clutterxp`; to keep it running after logout: `loginctl enable-linger $USER`.

## 4. Release process

1. Update `CHANGELOG.md`; bump version.
2. `ruff`, `mypy`, full `pytest` green on CI.
3. `python -m build` produces sdist and wheel; test-install the wheel with `pipx install dist/*.whl` in a clean container or VM.
4. Tag `vX.Y.Z`; attach artifacts.
5. Optional later: Debian package (`.deb`) via `dh-python`, aimed at Kali's repositories.

## 5. Completion and docs

- Shell completion via Typer (`clutterxp --install-completion`).
- `README.md` quickstart; `docs/` shipped in the sdist.
- Man page generation (optional, from `--help`) in a later release.

## 6. Files this package already contains

`pyproject.toml`, `config.example.yaml`, `packaging/clutterxp.service`, `.github/workflows/ci.yml`, `.gitignore`.
