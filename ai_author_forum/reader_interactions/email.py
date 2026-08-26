"""Transactional email adapter with no PII in application logs."""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_magic_link_email(*, recipient, link, purpose, expires_minutes):
    context = {
        "link": link,
        "purpose": purpose,
        "expires_minutes": expires_minutes,
    }
    text_body = render_to_string("reader_interactions/email/magic_link.txt", context)
    html_body = render_to_string("reader_interactions/email/magic_link.html", context)
    message = EmailMultiAlternatives(
        subject="Confirm your reader access",
        body=text_body,
        from_email=settings.READER_EMAIL_FROM,
        to=[recipient],
        headers={"X-Reader-Mail": "verification"},
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)
