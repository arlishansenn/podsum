"""SMTP email delivery adapter."""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


def send_smtp_email(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    mail_from: str,
    recipients: Iterable[str],
    subject: str,
    body: str,
    html_body: str = "",
    attachments: Iterable[Path] = (),
    timeout: float = 30.0,
    use_ssl: bool = True,
    starttls: bool = False,
    tls_verify: bool = True,
) -> str:
    recipient_list = [item.strip() for item in recipients if item.strip()]
    if not recipient_list:
        raise RuntimeError("missing SMTP recipients")
    if not host:
        raise RuntimeError("missing SMTP host")
    if not mail_from:
        raise RuntimeError("missing SMTP sender")

    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = ", ".join(recipient_list)
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    for attachment in attachments:
        path = attachment.expanduser()
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    context = ssl.create_default_context() if tls_verify else ssl._create_unverified_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if starttls:
                smtp.starttls(context=context)
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)

    return f"sent email to {len(recipient_list)} recipient(s)"
