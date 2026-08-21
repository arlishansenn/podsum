from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

PODSUM_PYTHON_ENV = "PODSUM_PYTHON"
PODSUM_HOME_ENV = "PODSUM_HOME"
PODSUM_TARGET_ENV = "PODSUM_TARGET"
PODSUM_DELIVERY_ENV = "PODSUM_EMAIL_DELIVERY"
PODSUM_EMAIL_SUMMARY_ENV = "PODSUM_EMAIL_SUMMARY"
DEFAULT_DELIVERY = "hermes"
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
    raise RuntimeError(
        "Podsum requires a virtual-environment Python. Set PODSUM_PYTHON or install the application venv."
    )


def running_inside_virtualenv() -> bool:
    if os.environ.get("VIRTUAL_ENV"):
        return True
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    real_prefix = getattr(sys, "real_prefix", None)
    return sys.prefix != base_prefix or real_prefix is not None


def platform_app_dir() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if system == "windows":
        app_data = os.environ.get("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        return base / APP_DIR_NAME
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "podsum"


def podsum_home() -> Path:
    """部署根目录。所有默认路径由它派生，避免各模块各自写死一份。"""
    configured = os.environ.get(PODSUM_HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    return platform_app_dir()


def platform_venv_python() -> Path:
    venv = podsum_home() / ".venv"
    if platform.system().lower() == "windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def default_env_file() -> Path:
    return podsum_home() / ".env"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'").strip('"')
        values[key.strip()] = value
    return values


def config_value(env_file: dict[str, str], file_name: str, *env_names: str, default: str = "") -> str:
    for name in (file_name, *env_names):
        value = os.environ.get(name)
        if value:
            return value
    value = env_file.get(file_name)
    if value:
        return value
    return default


def parse_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_delivery(cli_value: str, env_file: dict[str, str]) -> str:
    """邮件投递方式：CLI > 进程环境变量 > .env，回落到 hermes。

    CLI 默认值必须是空串。给它一个非空默认值，会让 `args.x or config_value(...)`
    的右边永远不执行——和 --target 踩过的是同一个坑。
    """
    return cli_value or config_value(env_file, PODSUM_DELIVERY_ENV, default=DEFAULT_DELIVERY)


def resolve_target(cli_value: str, env_file: dict[str, str], env_path: Path) -> str:
    """投递目标：CLI > 进程环境变量 > .env 文件。没有默认值。

    刻意不给 fallback。写死一个具体频道当默认值，会让「配置没填」从报错
    变成每天静默投递到一个无人拥有的目标。
    """
    target = cli_value or config_value(env_file, PODSUM_TARGET_ENV)
    if not target:
        raise RuntimeError(
            f"投递目标未配置：设置 {PODSUM_TARGET_ENV}（环境变量或 {env_path}），或用 --target 指定。"
        )
    return target


def runtime_diagnostics() -> dict[str, str | bool]:
    python = podsum_python()
    return {
        "python": python,
        "from_env": bool(os.environ.get(PODSUM_PYTHON_ENV)),
        "current_executable": sys.executable,
        "current_virtualenv": running_inside_virtualenv(),
        "platform_fallback": str(platform_venv_python()),
    }
