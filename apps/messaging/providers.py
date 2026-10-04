"""Messaging providers behind one small interface, so WhatsApp gateways can be swapped later."""

import smtplib
from dataclasses import dataclass
from email.utils import formataddr, make_msgid
from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection


@dataclass
class ProviderResult:
    ok: bool
    provider_message_id: str = ""
    error: str = ""
    temporary: bool = False  # worth retrying automatically


class MessagingProvider(Protocol):
    def send(self, delivery, pdf_bytes: bytes, pdf_name: str) -> ProviderResult: ...

    def health_check(self) -> ProviderResult: ...


SECURITY_CHOICES = (("tls", "STARTTLS (587)"), ("ssl", "SSL (465)"), ("none", "بدون"))


class SmtpEmailProvider:
    def __init__(self, config: dict, display_name: str = ""):
        self.config = config
        self.display_name = display_name

    def _connection(self):
        security = self.config.get("security", "tls")
        return get_connection(
            backend=settings.MESSAGING_EMAIL_BACKEND,
            host=self.config.get("host", ""),
            port=int(self.config.get("port") or 587),
            username=self.config.get("username", ""),
            password=self.config.get("password", ""),
            use_tls=security == "tls",
            use_ssl=security == "ssl",
            timeout=20,
        )

    @property
    def from_email(self):
        address = self.config.get("from_email") or self.config.get("username", "")
        return formataddr((self.display_name, address)) if self.display_name else address

    def build_message(self, *, to, subject, body, reply_to=None, html=None, attachment=None, connection=None):
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=self.from_email,
            to=[to],
            reply_to=[reply_to] if reply_to else None,
            connection=connection,
            headers={"Message-ID": make_msgid(domain="digiclinic.local")},
        )
        if html:
            msg.attach_alternative(html, "text/html")
        if attachment:
            msg.attach(*attachment)
        return msg

    def send_test(self, to: str) -> ProviderResult:
        try:
            with self._connection() as conn:
                self.build_message(
                    to=to,
                    subject="رسالة تجربة من DigiClinic",
                    body="لو الرسالة دي وصلتك، يبقى إعدادات الإيميل شغالة ✓",
                    connection=conn,
                ).send()
            return ProviderResult(ok=True)
        except smtplib.SMTPAuthenticationError:
            return ProviderResult(ok=False, error="الإيميل أو كلمة مرور التطبيق غلط.")
        except (smtplib.SMTPException, OSError) as e:
            return ProviderResult(ok=False, error=str(e) or e.__class__.__name__)

    def health_check(self) -> ProviderResult:
        try:
            conn = self._connection()
            conn.open()
            conn.close()
            return ProviderResult(ok=True)
        except (smtplib.SMTPException, OSError) as e:
            return ProviderResult(ok=False, error=str(e) or e.__class__.__name__)
