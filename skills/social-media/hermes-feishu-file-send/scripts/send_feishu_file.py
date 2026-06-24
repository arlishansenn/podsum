#!/usr/bin/env python3
"""Send a local file to Feishu/Lark chat via official OpenAPI.

Usage:
  set -a && . ~/.hermes/.env && set +a && \
  python3 send_feishu_file.py --file /abs/path/book.epub --chat-id oc_xxx
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path

import requests


def _api_base() -> str:
    domain = (os.getenv("FEISHU_DOMAIN") or "feishu").strip().lower()
    if domain == "lark":
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


def _tenant_token(app_id: str, app_secret: str, base: str) -> str:
    s = requests.Session()
    s.trust_env = False  # avoid broken local proxy env
    r = s.post(
        f"{base}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"token failed: {data}")
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"missing tenant_access_token: {data}")
    return token


def _upload_file(token: str, file_path: Path, base: str) -> str:
    s = requests.Session()
    s.trust_env = False
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    with file_path.open("rb") as f:
        files = {
            "file": (file_path.name, f, mime),
        }
        data = {
            "file_type": "stream",
            "file_name": file_path.name,
        }
        r = s.post(
            f"{base}/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data=data,
            files=files,
            timeout=120,
        )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"upload failed: {body}")
    file_key = (((body or {}).get("data") or {}).get("file_key"))
    if not file_key:
        raise RuntimeError(f"missing file_key: {body}")
    return file_key


def _send_file_message(token: str, file_key: str, *, base: str, chat_id: str | None, user_id: str | None) -> str:
    if bool(chat_id) == bool(user_id):
        raise ValueError("provide exactly one of --chat-id or --user-id")

    receive_id_type = "chat_id" if chat_id else "open_id"
    receive_id = chat_id if chat_id else user_id

    payload = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
    }

    s = requests.Session()
    s.trust_env = False
    r = s.post(
        f"{base}/open-apis/im/v1/messages",
        params={"receive_id_type": receive_id_type},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"send failed: {body}")
    message_id = (((body or {}).get("data") or {}).get("message_id"))
    if not message_id:
        raise RuntimeError(f"missing message_id: {body}")
    return message_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Absolute file path")
    ap.add_argument("--chat-id", help="Feishu chat_id (oc_...)")
    ap.add_argument("--user-id", help="Feishu open_id (ou_...)")
    args = ap.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        print(json.dumps({"ok": False, "error": f"file not found: {file_path}"}, ensure_ascii=False))
        return 2

    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        print(json.dumps({"ok": False, "error": "missing FEISHU_APP_ID/FEISHU_APP_SECRET"}, ensure_ascii=False))
        return 2

    try:
        base = _api_base()
        token = _tenant_token(app_id, app_secret, base)
        file_key = _upload_file(token, file_path, base)
        message_id = _send_file_message(
            token,
            file_key,
            base=base,
            chat_id=args.chat_id,
            user_id=args.user_id,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "base": base,
                    "file": str(file_path),
                    "file_key": file_key,
                    "message_id": message_id,
                    "target": args.chat_id or args.user_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
