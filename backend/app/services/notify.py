"""Notifications (Lot 4) — ntfy, Discord webhook, SMTP email.

Configured via env (.env): any of NTFY_URL / DISCORD_WEBHOOK_URL / SMTP_*.
Every configured channel gets every notification; sending is best-effort and
never raises into the caller (a dead webhook must not fail a download job).

Events: job completed (opt-in via NOTIFY_ON_SUCCESS), job failed/cancelled
(NOTIFY_ON_FAILURE, default on), and `needs_2fa` when the scheduler finds the
iCloud session expired — the critical one for unattended syncs.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import requests

LOGGER = logging.getLogger(__name__)

_TIMEOUT = 10  # s — notification HTTP calls must never hang a worker


class Notifier:
    """Fan-out notifier. Construct via `build_notifier()` (reads settings) or
    directly in tests. `http` is injectable (requests-compatible `post`)."""

    def __init__(
        self,
        *,
        ntfy_url: str | None = None,
        discord_webhook_url: str | None = None,
        smtp: dict | None = None,  # {host, port, user, password, from, to, starttls}
        http=requests,
        smtp_factory=smtplib.SMTP,
    ) -> None:
        self.ntfy_url = ntfy_url
        self.discord_webhook_url = discord_webhook_url
        self.smtp = smtp
        self.http = http
        self.smtp_factory = smtp_factory

    @property
    def configured(self) -> bool:
        return bool(self.ntfy_url or self.discord_webhook_url or self.smtp)

    def send(self, title: str, message: str, level: str = "info") -> None:
        """Send to every configured channel; failures are logged, not raised."""
        if self.ntfy_url:
            self._ntfy(title, message, level)
        if self.discord_webhook_url:
            self._discord(title, message)
        if self.smtp:
            self._email(title, message)

    # ------------------------------------------------------------- channels
    def _ntfy(self, title: str, message: str, level: str) -> None:
        try:
            self.http.post(
                self.ntfy_url,
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "high" if level == "error" else "default",
                    "Tags": "warning" if level == "error" else "arrow_down",
                },
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            LOGGER.warning("ntfy notification failed: %s", exc)

    def _discord(self, title: str, message: str) -> None:
        try:
            self.http.post(
                self.discord_webhook_url,
                json={"content": f"**{title}**\n{message}"},
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            LOGGER.warning("Discord notification failed: %s", exc)

    def _email(self, title: str, message: str) -> None:
        cfg = self.smtp or {}
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[iCloud Sync] {title}"
            msg["From"] = cfg["from"]
            msg["To"] = cfg["to"]
            msg.set_content(message)
            with self.smtp_factory(cfg["host"], int(cfg.get("port", 587)), timeout=_TIMEOUT) as s:
                if cfg.get("starttls", True):
                    s.starttls()
                if cfg.get("user"):
                    s.login(cfg["user"], cfg.get("password", ""))
                s.send_message(msg)
        except Exception as exc:
            LOGGER.warning("Email notification failed: %s", exc)


def build_notifier() -> Notifier:
    """Notifier from app settings (env)."""
    from app.core.config import get_settings

    s = get_settings()
    smtp = None
    if s.smtp_host and s.smtp_to:
        smtp = {
            "host": s.smtp_host,
            "port": s.smtp_port,
            "user": s.smtp_user,
            "password": s.smtp_password,
            "from": s.smtp_from or s.smtp_user or "icloud-sync@localhost",
            "to": s.smtp_to,
        }
    return Notifier(
        ntfy_url=s.ntfy_url,
        discord_webhook_url=s.discord_webhook_url,
        smtp=smtp,
    )


def notify_job_result(notifier: Notifier, job_id: int, status: str,
                      counts: dict | None = None, *,
                      on_success: bool, on_failure: bool) -> None:
    """Job-end hook used by the worker."""
    if not notifier.configured:
        return
    ok = status == "completed"
    if ok and not on_success:
        return
    if not ok and not on_failure:
        return
    c = counts or {}
    detail = (
        f"downloaded {c.get('downloaded', 0)}, skipped {c.get('skipped', 0)}, "
        f"failed {c.get('failed', 0)}"
    )
    notifier.send(
        f"Job #{job_id} {status}",
        f"Download job #{job_id} finished with status {status} ({detail}).",
        level="info" if ok else "error",
    )
