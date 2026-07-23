"""Tests for ParkPower direct SMTP delivery."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from unittest import TestCase
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pow_reporting"
package = types.ModuleType("custom_components.pow_reporting")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.pow_reporting", package)

from custom_components.pow_reporting.smtp_client import (
    SMTPSettings,
    safe_smtp_settings,
    send_smtp_email,
    smtp_error_message,
)


class FakeSMTP:
    """Record SMTP operations without opening a network connection."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_args) -> None:
        self.calls.append(("close",))

    def ehlo(self) -> None:
        self.calls.append(("ehlo",))

    def starttls(self, *, context) -> None:
        self.calls.append(("starttls", context))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message, *, from_addr: str, to_addrs: list[str]) -> None:
        self.calls.append(
            (
                "send_message",
                message["Subject"],
                message["From"],
                message["To"],
                from_addr,
                to_addrs,
            )
        )


class SMTPClientTest(TestCase):
    """Validate SMTP settings, transport selection, and redaction."""

    def _values(self, **overrides) -> dict:
        return {
            "smtp_host": "smtp.example.test",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "billing-user",
            "smtp_password": "secret-value",
            "smtp_sender_email": "billing@example.test",
            "sender_name": "ParkPower Billing",
            **overrides,
        }

    def test_settings_require_password_when_username_is_configured(self) -> None:
        with self.assertRaisesRegex(ValueError, "password"):
            SMTPSettings.from_mapping(self._values(smtp_password=""))

    def test_settings_allow_local_relay_without_credentials(self) -> None:
        settings = SMTPSettings.from_mapping(
            self._values(
                smtp_port=25,
                smtp_security="none",
                smtp_username="",
                smtp_password="",
            )
        )

        self.assertEqual(settings.port, 25)
        self.assertEqual(settings.security, "none")

    def test_starttls_login_and_message_delivery(self) -> None:
        fake = FakeSMTP()
        settings = SMTPSettings.from_mapping(self._values())

        with patch("custom_components.pow_reporting.smtp_client.smtplib.SMTP", return_value=fake):
            send_smtp_email(
                settings,
                recipient="tenant@example.test",
                subject="July statement",
                body="Statement body",
            )

        self.assertEqual([row[0] for row in fake.calls].count("ehlo"), 2)
        self.assertIn(("login", "billing-user", "secret-value"), fake.calls)
        sent = next(row for row in fake.calls if row[0] == "send_message")
        self.assertEqual(sent[1], "July statement")
        self.assertEqual(sent[4], "billing@example.test")
        self.assertEqual(sent[5], ["tenant@example.test"])

    def test_ssl_uses_implicit_tls_without_starttls(self) -> None:
        fake = FakeSMTP()
        settings = SMTPSettings.from_mapping(
            self._values(smtp_port=465, smtp_security="ssl")
        )

        with patch(
            "custom_components.pow_reporting.smtp_client.smtplib.SMTP_SSL",
            return_value=fake,
        ):
            send_smtp_email(
                settings,
                recipient="tenant@example.test",
                subject="Statement",
                body="Body",
            )

        self.assertNotIn("starttls", [row[0] for row in fake.calls])

    def test_safe_settings_never_return_password(self) -> None:
        private = safe_smtp_settings(self._values(), include_private=True)
        public = safe_smtp_settings(self._values(), include_private=False)

        self.assertNotIn("smtp_password", private)
        self.assertNotIn("secret-value", str(private))
        self.assertTrue(private["smtp_password_configured"])
        self.assertEqual(private["smtp_username"], "billing-user")
        self.assertNotIn("smtp_username", public)
        self.assertNotIn("smtp_host", public)

    def test_error_message_redacts_password(self) -> None:
        settings = SMTPSettings.from_mapping(self._values())

        message = smtp_error_message(
            RuntimeError("Login rejected for secret-value"),
            settings,
        )

        self.assertEqual(message, "Login rejected for [redacted]")


if __name__ == "__main__":
    import unittest

    unittest.main()
