"""Send HTML email through Gmail SMTP using an App Password."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send(subject: str, html: str, to: str | None = None, dry_run: bool = False) -> bool:
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    to = to or sender
    if dry_run or not (sender and password):
        if not dry_run:
            print("[email] GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — printing instead of sending.")
        print(f"\n===== EMAIL: {subject} =====\n{_strip(html)}\n")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.attach(MIMEText(_strip(html), "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(sender, password.replace(" ", ""))
        s.sendmail(sender, [to], msg.as_string())
    print(f"[email] sent: {subject}")
    return True


def _strip(html: str) -> str:
    import re
    text = re.sub(r"<(br|/p|/tr|/h\d|/li)>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
