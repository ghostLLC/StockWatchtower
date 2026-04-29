from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header


def send_signal_email(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    mail_to = os.getenv("MAIL_TO")

    if not all([host, user, password, mail_to]):
        raise RuntimeError("邮件配置不完整，请先填写 .env")
    assert host is not None
    assert user is not None
    assert password is not None
    assert mail_to is not None

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")  # type: ignore[assignment]
    msg["From"] = user
    msg["To"] = mail_to

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.sendmail(user, [mail_to], msg.as_string())
