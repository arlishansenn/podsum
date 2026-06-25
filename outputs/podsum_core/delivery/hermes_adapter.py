"""Hermes 子进程适配器。"""

from __future__ import annotations

import subprocess


def run_hermes_prompt(hermes_bin: str, prompt: str, *, cwd: str, timeout: float) -> tuple[bool, str]:
    command: list[str] = [hermes_bin, "-z", prompt]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, str(exc).strip()

    output: str = (result.stdout or "").strip()
    if result.returncode != 0:
        detail: str = (result.stderr or output or "unknown Hermes error").strip()
        return False, detail
    return True, output


def send_hermes_file(hermes_bin: str, target: str, subject: str, message: str) -> str:
    command: list[str] = [
        hermes_bin,
        "send",
        "--to",
        target,
        "--subject",
        subject,
        message,
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "hermes send failed").strip())
    return (result.stdout or "sent").strip()
