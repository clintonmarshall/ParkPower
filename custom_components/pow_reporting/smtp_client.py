"""Direct SMTP delivery for ParkPower tenant statements."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import smtplib
import ssl
from typing import Any, Mapping

SMTP_SECURITY_STARTTLS = "starttls"
SMTP_SECURITY_SSL = "ssl"
SMTP_SECURITY_NONE = "none"
SMTP_SECURITY_MODES = {
    SMTP_SECURITY_STARTTLS,
    SMTP_SECURITY_SSL,
    SMTP_SECURITY_NONE,
}


@dataclass(frozen=True, slots=True)
class SMTPSettings:
    """Validated SMTP connection and sender settings."""

    host: str
    port: int
    security: str
    username: str
    password: str
    sender_email: str
    sender_name: str
    timeout: float = 20.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> SMTPSettings:
        """Build validated settings from persisted billing options."""
        host = str(values.get("smtp_host") or "").strip()
        security = str(values.get("smtp_security") or SMTP_SECURITY_STARTTLS).lower()
        username = str(values.get("smtp_username") or "").strip()
        password = str(values.get("smtp_password") or "")
        sender_email = str(values.get("smtp_sender_email") or "").strip()
        sender_name = str(values.get("sender_name") or "ParkPower").strip() or "ParkPower"

        try:
            port = int(values.get("smtp_port") or 0)
        except (TypeError, ValueError) as err:
            raise ValueError("SMTP port must be a number") from err

        if not host:
            raise ValueError("SMTP server is required")
        if not 1 <= port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if security not in SMTP_SECURITY_MODES:
            raise ValueError("SMTP security must be STARTTLS, SSL/TLS, or None")
        if not is_email_address(sender_email):
            raise ValueError("A valid SMTP sender email address is required")
        if username and not password:
            raise ValueError("SMTP password is required when a username is configured")

        return cls(
            host=host,
            port=port,
            security=security,
            username=username,
            password=password,
            sender_email=sender_email,
            sender_name=sender_name,
        )


def send_smtp_email(
    settings: SMTPSettings,
    *,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """Send one plain-text email through a validated SMTP account."""
    recipient = recipient.strip()
    if not is_email_address(recipient):
        raise ValueError("A valid recipient email address is required")

    message = EmailMessage()
    message["From"] = formataddr((settings.sender_name, settings.sender_email))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if settings.security == SMTP_SECURITY_SSL:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.host,
            settings.port,
            timeout=settings.timeout,
            context=ssl.create_default_context(),
        )
    else:
        client = smtplib.SMTP(
            settings.host,
            settings.port,
            timeout=settings.timeout,
        )

    with client:
        client.ehlo()
        if settings.security == SMTP_SECURITY_STARTTLS:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.username:
            client.login(settings.username, settings.password)
        client.send_message(
            message,
            from_addr=settings.sender_email,
            to_addrs=[recipient],
        )


def safe_smtp_settings(
    settings: Mapping[str, Any],
    *,
    include_private: bool,
) -> dict[str, Any]:
    """Return SMTP status without ever exposing the stored password."""
    password_configured = bool(settings.get("smtp_password"))
    try:
        port = int(settings.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    result = {
        "email_delivery_method": str(
            settings.get("email_delivery_method") or "notify"
        ),
        "smtp_password_configured": password_configured,
        "smtp_ready": _smtp_ready(settings),
    }
    if include_private:
        result.update(
            {
                "notify_service": str(settings.get("notify_service") or ""),
                "smtp_host": str(settings.get("smtp_host") or ""),
                "smtp_port": port,
                "smtp_security": str(
                    settings.get("smtp_security") or SMTP_SECURITY_STARTTLS
                ),
                "smtp_username": str(settings.get("smtp_username") or ""),
                "smtp_sender_email": str(settings.get("smtp_sender_email") or ""),
            }
        )
    return result


def smtp_error_message(error: Exception, settings: SMTPSettings | None = None) -> str:
    """Return an admin-facing SMTP error with credentials redacted."""
    message = str(error).strip() or error.__class__.__name__
    if settings and settings.password:
        message = message.replace(settings.password, "[redacted]")
    return message


def _smtp_ready(settings: Mapping[str, Any]) -> bool:
    """Return whether the persisted settings are complete enough to send."""
    try:
        SMTPSettings.from_mapping(settings)
    except ValueError:
        return False
    return True


def is_email_address(value: str) -> bool:
    """Perform a conservative single-address validation."""
    display_name, address = parseaddr(value)
    return (
        not display_name
        and address == value
        and "@" in address
        and not address.startswith("@")
        and not address.endswith("@")
    )
