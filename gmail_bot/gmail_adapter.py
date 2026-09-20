from __future__ import annotations

import base64
import logging
import re
from email.message import EmailMessage
from typing import Any, Protocol

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_bot.config import Settings, normalize_email
from gmail_bot.language import detect_language
from gmail_bot.models import ParsedMessage, StyleExample, short_snippet
from gmail_bot.resilience import RateLimiter, retry_call

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
USER = "me"

logger = logging.getLogger("gmail_bot")


class GmailPort(Protocol):
    def oauth_ready(self) -> bool: ...
    def authenticate(self, open_browser: bool = True, force: bool = False, login_hint: str = "") -> None: ...
    def get_profile_email(self) -> str: ...
    def list_unread_ids(self, query: str) -> list[str]: ...
    def list_message_ids(self, query: str, limit: int = 10) -> list[str]: ...
    def get_message(self, message_id: str) -> ParsedMessage: ...
    def create_draft_reply(self, message: ParsedMessage, reply_text: str, from_email: str = "") -> str: ...
    def apply_label(self, message_id: str, label_name: str) -> None: ...
    def fetch_sent_examples(self, limit: int = 20) -> list[StyleExample]: ...


def header_map(headers: list[dict[str, str]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in headers or []:
        name = (header.get("name") or "").lower()
        if name and name not in result:
            result[name] = header.get("value") or ""
    return result


def _decode_b64(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


def _strip_html(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split())


def extract_body(payload: dict[str, Any] | None) -> str:
    """Pull plain text from a Gmail MIME payload; fall back to stripped HTML."""
    if not payload:
        return ""
    plain = _extract_mime(payload, "text/plain")
    if plain.strip():
        return plain.strip()
    html = _extract_mime(payload, "text/html")
    if html.strip():
        return _strip_html(html)
    body = payload.get("body") or {}
    data = body.get("data")
    if data:
        return _decode_b64(data).strip()
    return ""


def _extract_mime(payload: dict[str, Any], mime: str) -> str:
    if payload.get("mimeType") == mime:
        data = (payload.get("body") or {}).get("data")
        if data:
            return _decode_b64(data)
    for part in payload.get("parts") or []:
        found = _extract_mime(part, mime)
        if found:
            return found
    return ""


def reply_subject(subject: str | None) -> str:
    value = subject or "(No Subject)"
    return value if value.lower().startswith("re:") else f"Re: {value}"


def reply_recipient(message: ParsedMessage, from_email: str = "") -> str:
    """Address the original sender, never the inbox owner."""
    mine = normalize_email(from_email)
    for candidate in (message.reply_to, message.sender):
        email = normalize_email(candidate)
        if email and email != mine:
            return candidate.strip()
    return (message.sender or message.reply_to or "").strip()


def build_draft_payload(message: ParsedMessage, reply_text: str, from_email: str = "") -> dict[str, Any]:
    msg = EmailMessage()
    msg.set_content(reply_text)
    owner = (from_email or "").strip()
    msg["To"] = reply_recipient(message, owner)
    if owner:
        msg["From"] = owner
    msg["Subject"] = reply_subject(message.subject)
    if message.message_id_header:
        msg["In-Reply-To"] = message.message_id_header
        references = message.references.strip()
        msg["References"] = (
            f"{references} {message.message_id_header}".strip()
            if references
            else message.message_id_header
        )
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"message": {"threadId": message.thread_id, "raw": raw}}


def parse_gmail_message(raw: dict[str, Any]) -> ParsedMessage:
    payload = raw.get("payload") or {}
    headers = header_map(payload.get("headers"))
    body = extract_body(payload)
    snippet = raw.get("snippet") or short_snippet(body)
    return ParsedMessage(
        message_id=raw.get("id") or "",
        thread_id=raw.get("threadId") or "",
        sender=headers.get("from", ""),
        subject=headers.get("subject", "(No Subject)"),
        body=body,
        snippet=snippet,
        message_id_header=headers.get("message-id", ""),
        references=headers.get("references", ""),
        reply_to=headers.get("reply-to", ""),
    )


class GmailAdapter:
    """Gmail API adapter. Creates drafts and labels only — never sends, never marks read."""

    def __init__(self, settings: Settings, service: Any | None = None, limiter: RateLimiter | None = None) -> None:
        self.settings = settings
        self._service = service
        self.limiter = limiter or RateLimiter(settings.gmail_min_interval)
        self._label_ids: dict[str, str] = {}

    def oauth_ready(self) -> bool:
        return self.settings.token_path.is_file() and self.settings.credentials_path.is_file()

    def authenticate(self, open_browser: bool = True, force: bool = False, login_hint: str = "") -> None:
        self.settings.ensure_dirs()
        if force and self.settings.token_path.is_file():
            self.settings.token_path.unlink()
            self._service = None
        creds = None
        if self.settings.token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(self.settings.token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token and not force:
                creds.refresh(Request())
            else:
                if not self.settings.credentials_path.is_file():
                    raise FileNotFoundError(
                        f"Missing OAuth client file: {self.settings.credentials_path}. "
                        "Download a Desktop app client JSON from Google Cloud and save it there."
                    )
                if not open_browser:
                    raise RuntimeError("Gmail token missing or expired. Connect the Gmail address from the app.")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.settings.credentials_path), SCOPES)
                kwargs: dict[str, Any] = {"port": 0}
                if login_hint:
                    kwargs["login_hint"] = login_hint
                creds = flow.run_local_server(**kwargs)
            self.settings.token_path.write_text(creds.to_json(), encoding="utf-8")
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def get_profile_email(self) -> str:
        result = self._call(self.service.users().getProfile(userId=USER))
        return (result.get("emailAddress") or "").strip()

    @property
    def service(self) -> Any:
        if self._service is None:
            self.authenticate(open_browser=False)
        return self._service

    def _call(self, fn: Any) -> Any:
        self.limiter.wait()

        def _execute() -> Any:
            return fn.execute()

        try:
            return retry_call(_execute, attempts=4, base=1.0, cap=20.0, retry_on=(HttpError, OSError, ConnectionError))
        except HttpError:
            raise

    def list_unread_ids(self, query: str) -> list[str]:
        ids: list[str] = []
        page_token = None
        while True:
            request = self.service.users().messages().list(userId=USER, q=query, pageToken=page_token)
            result = self._call(request)
            for item in result.get("messages") or []:
                if item.get("id"):
                    ids.append(item["id"])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return ids

    def list_message_ids(self, query: str, limit: int = 10) -> list[str]:
        request = self.service.users().messages().list(
            userId=USER, q=query, maxResults=max(1, min(int(limit), 50))
        )
        result = self._call(request)
        return [item["id"] for item in result.get("messages") or [] if item.get("id")]

    def get_message(self, message_id: str) -> ParsedMessage:
        request = self.service.users().messages().get(userId=USER, id=message_id, format="full")
        raw = self._call(request)
        return parse_gmail_message(raw)

    def create_draft_reply(self, message: ParsedMessage, reply_text: str, from_email: str = "") -> str:
        body = build_draft_payload(message, reply_text, from_email=from_email)
        request = self.service.users().drafts().create(userId=USER, body=body)
        result = self._call(request)
        draft_id = result.get("id") or ""
        logger.info(
            "Created draft",
            extra={
                "event": "draft_created",
                "message_id": message.message_id,
                "thread_id": message.thread_id,
                "draft_id": draft_id,
                "sender": message.sender,
                "subject": message.subject,
                "snippet": message.log_snippet,
            },
        )
        return draft_id

    def apply_label(self, message_id: str, label_name: str) -> None:
        label_id = self._ensure_label(label_name)
        request = self.service.users().messages().modify(
            userId=USER,
            id=message_id,
            body={"addLabelIds": [label_id]},
        )
        self._call(request)

    def fetch_sent_examples(self, limit: int = 20) -> list[StyleExample]:
        request = self.service.users().messages().list(userId=USER, q="in:sent", maxResults=limit)
        result = self._call(request)
        examples: list[StyleExample] = []
        for item in result.get("messages") or []:
            msg = self.get_message(item["id"])
            body = short_snippet(msg.body, 800)
            if not body:
                continue
            examples.append(StyleExample(subject=msg.subject, body=body, language=detect_language(body)))
        return examples

    def _ensure_label(self, name: str) -> str:
        if name in self._label_ids:
            return self._label_ids[name]
        request = self.service.users().labels().list(userId=USER)
        result = self._call(request)
        for label in result.get("labels") or []:
            if label.get("name") == name:
                self._label_ids[name] = label["id"]
                return label["id"]
        created = self._call(
            self.service.users().labels().create(
                userId=USER,
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
        )
        self._label_ids[name] = created["id"]
        return created["id"]
