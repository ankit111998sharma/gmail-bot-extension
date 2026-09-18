from __future__ import annotations

import base64
import inspect
from email import message_from_bytes
from email import policy as email_policy

from gmail_bot.gmail_adapter import (
    GmailAdapter,
    build_draft_payload,
    extract_body,
    parse_gmail_message,
    reply_subject,
)
from tests.conftest import make_message


def test_extract_plain_nested_parts() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Hello nested").decode("ascii")},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": base64.urlsafe_b64encode(b"<b>Hello nested</b>").decode("ascii")},
                    },
                ],
            }
        ],
    }
    assert extract_body(payload) == "Hello nested"


def test_extract_html_fallback() -> None:
    html = "<html><body><p>Quoted hours <b>9 AM</b></p></body></html>"
    payload = {
        "mimeType": "text/html",
        "body": {"data": base64.urlsafe_b64encode(html.encode()).decode("ascii")},
    }
    assert "9 AM" in extract_body(payload)


def test_draft_payload_threads_and_headers() -> None:
    message = make_message(references="<prev@mail.example.com>")
    payload = build_draft_payload(message, "Thanks, we are open 9 to 6.")
    assert payload["message"]["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(payload["message"]["raw"].encode("ascii"))
    parsed = message_from_bytes(raw, policy=email_policy.default)
    assert parsed["To"] == "Ada <ada@example.com>"
    assert parsed["Subject"].lower().startswith("re:")
    assert parsed["In-Reply-To"] == "<id-1@mail.example.com>"
    assert "<prev@mail.example.com>" in parsed["References"]
    assert "<id-1@mail.example.com>" in parsed["References"]
    assert "9 to 6" in parsed.get_content()


def test_reply_subject_keeps_existing_re() -> None:
    assert reply_subject("Re: Hello") == "Re: Hello"
    assert reply_subject("Hello").startswith("Re:")


def test_parse_gmail_message_uses_reply_to() -> None:
    raw = {
        "id": "abc",
        "threadId": "thr",
        "snippet": "hi",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Ada <ada@example.com>"},
                {"name": "Reply-To", "value": "desk@example.com"},
                {"name": "Subject", "value": "Help"},
                {"name": "Message-ID", "value": "<mid@example.com>"},
            ],
            "body": {"data": base64.urlsafe_b64encode(b"Need help").decode("ascii")},
        },
    }
    parsed = parse_gmail_message(raw)
    assert parsed.draft_to == "desk@example.com"
    assert parsed.body == "Need help"


def test_adapter_has_no_send_or_mark_read() -> None:
    source = inspect.getsource(GmailAdapter)
    assert "users().messages().send" not in source
    assert "UNREAD" not in source
    assert "def send(" not in source
    assert not hasattr(GmailAdapter, "mark_as_read")
