from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

PODSUM_PYTHON_ENV = "PODSUM_PYTHON"
APP_DIR_NAME = "Podsum"


def podsum_python() -> str:
    configured = os.environ.get(PODSUM_PYTHON_ENV)
    if configured:
        return configured
    if running_inside_virtualenv():
        return sys.executable
    candidate = platform_venv_python()
    if candidate.exists():
        return str(candidate)
    return sys.executable


def running_inside_virtualenv() -> bool:
    if os.environ.get("VIRTUAL_ENV"):
        return True
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    real_prefix = getattr(sys, "real_prefix", None)
    return sys.prefix != base_prefix or real_prefix is not None


def platform_venv_python() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / ".venv" / "bin" / "python"
    if system == "windows":
        app_data = os.environ.get("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        return base / APP_DIR_NAME / ".venv" / "Scripts" / "python.exe"
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "podsum" / ".venv" / "bin" / "python"


def runtime_diagnostics() -> dict[str, str | bool]:
    python = podsum_python()
    return {
        "python": python,
        "from_env": bool(os.environ.get(PODSUM_PYTHON_ENV)),
        "current_executable": sys.executable,
        "current_virtualenv": running_inside_virtualenv(),
        "platform_fallback": str(platform_venv_python()),
    }
