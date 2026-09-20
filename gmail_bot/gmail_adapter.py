from __future__ import annotations

import base64
import logging
import re
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr
from typing import Any, Protocol

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_bot.config import Settings, normalize_email, same_email
from gmail_bot.language import detect_language
from gmail_bot.models import ParsedMessage, StyleExample, short_snippet
from gmail_bot.resilience import RateLimiter, retry_call, is_retryable_error

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
    def create_draft_reply(
        self,
        message: ParsedMessage,
        reply_text: str,
        from_email: str = "",
        from_name: str = "",
        standalone: bool = False,
    ) -> str: ...
    def update_draft_reply(
        self,
        draft_id: str,
        message: ParsedMessage,
        reply_text: str,
        from_email: str = "",
        from_name: str = "",
        standalone: bool = False,
    ) -> str: ...
    def get_draft(self, draft_id: str) -> dict[str, str]: ...
    def find_thread_draft(self, thread_id: str) -> tuple[str, str]: ...
    def delete_draft(self, draft_id: str) -> None: ...
    def apply_label(self, message_id: str, label_name: str) -> None: ...
    def fetch_sent_examples(self, limit: int = 20) -> list[StyleExample]: ...
    def fetch_related_sent(
        self, subject: str, limit: int = 8, correspondent: str = ""
    ) -> list[StyleExample]: ...


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


def address_candidates(*values: str | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            candidate = part.strip()
            email = normalize_email(candidate)
            if not email or email in seen:
                continue
            seen.add(email)
            out.append(candidate)
    return out


def reply_recipient(message: ParsedMessage, from_email: str = "") -> str:
    """Address the other person, never the inbox owner."""
    mine = normalize_email(from_email)
    for candidate in address_candidates(
        message.reply_to,
        message.sender,
        getattr(message, "to_header", ""),
        getattr(message, "cc_header", ""),
    ):
        if not same_email(candidate, mine):
            return candidate
    return ""


def format_from_header(from_email: str, from_name: str = "") -> str:
    email = (from_email or "").strip()
    name = (from_name or "").strip()
    if name and email and "@" not in name:
        return formataddr((name, email))
    return email


def encode_rfc2822_raw(raw_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def build_draft_payload(
    message: ParsedMessage,
    reply_text: str,
    from_email: str = "",
    from_name: str = "",
    standalone: bool = False,
) -> dict[str, Any]:
    """RFC 2822 draft: explicit From, CRLF raw. Replies keep threadId + In-Reply-To/References."""
    owner = (from_email or "").strip()
    if not owner:
        raise ValueError("Gmail drafts need an explicit From address from the signed-in account.")
    to_addr = reply_recipient(message, owner)
    if not to_addr:
        raise ValueError("Drafts need a To address (the other person).")
    msg = EmailMessage(policy=SMTP)
    msg["From"] = format_from_header(owner, from_name)
    msg["To"] = to_addr
    if standalone:
        msg["Subject"] = (message.subject or "").strip() or "(No Subject)"
    else:
        msg["Subject"] = reply_subject(message.subject)
        if message.message_id_header:
            msg["In-Reply-To"] = message.message_id_header
            references = (message.references or "").strip()
            msg["References"] = (
                f"{references} {message.message_id_header}".strip() if references else message.message_id_header
            )
    msg.set_content(reply_text or "", subtype="plain", charset="utf-8")
    payload_message: dict[str, Any] = {"raw": encode_rfc2822_raw(msg.as_bytes(policy=SMTP))}
    thread_id = (message.thread_id or "").strip()
    if thread_id:
        payload_message["threadId"] = thread_id
    return {"message": payload_message}


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
        to_header=headers.get("to", ""),
        cc_header=headers.get("cc", ""),
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
            return retry_call(
                _execute,
                attempts=4,
                base=1.0,
                cap=20.0,
                retry_on=(HttpError, OSError, ConnectionError),
                should_retry=is_retryable_error,
            )
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

    def _sender_identity(self, from_email: str = "", from_name: str = "") -> tuple[str, str]:
        email = normalize_email(from_email)
        if not email:
            try:
                email = normalize_email(self.get_profile_email())
            except Exception:  # noqa: BLE001
                email = ""
        return (from_name or "").strip(), email

    def create_draft_reply(
        self,
        message: ParsedMessage,
        reply_text: str,
        from_email: str = "",
        from_name: str = "",
        standalone: bool = False,
    ) -> str:
        name, email = self._sender_identity(from_email, from_name)
        if not email:
            raise RuntimeError("Gmail drafts need the signed-in account in the From header.")
        body = build_draft_payload(
            message, reply_text, from_email=email, from_name=name, standalone=standalone
        )
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
                "snippet": f"from={email} | {message.log_snippet}",
            },
        )
        return draft_id

    def update_draft_reply(
        self,
        draft_id: str,
        message: ParsedMessage,
        reply_text: str,
        from_email: str = "",
        from_name: str = "",
        standalone: bool = False,
    ) -> str:
        if not draft_id:
            return self.create_draft_reply(
                message, reply_text, from_email=from_email, from_name=from_name, standalone=standalone
            )
        name, email = self._sender_identity(from_email, from_name)
        if not email:
            raise RuntimeError("Gmail drafts need the signed-in account in the From header.")
        body = build_draft_payload(
            message, reply_text, from_email=email, from_name=name, standalone=standalone
        )
        body["id"] = draft_id
        request = self.service.users().drafts().update(userId=USER, id=draft_id, body=body)
        result = self._call(request)
        updated = result.get("id") or draft_id
        logger.info(
            "Updated draft",
            extra={
                "event": "draft_updated",
                "message_id": message.message_id,
                "thread_id": message.thread_id,
                "draft_id": updated,
                "sender": message.sender,
                "subject": message.subject,
                "snippet": message.log_snippet,
            },
        )
        return updated

    def get_draft(self, draft_id: str) -> dict[str, str]:
        want = (draft_id or "").strip()
        if not want:
            return {}
        full = self._call(self.service.users().drafts().get(userId=USER, id=want, format="full"))
        message = full.get("message") or {}
        payload = message.get("payload") or {}
        headers = header_map(payload.get("headers"))
        return {
            "id": str(full.get("id") or want),
            "thread_id": str(message.get("threadId") or ""),
            "text": extract_body(payload),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
        }

    def find_thread_draft(self, thread_id: str) -> tuple[str, str]:
        want = (thread_id or "").strip()
        if not want:
            return "", ""
        if not self._thread_has_draft(want):
            return "", ""
        draft_id = self._draft_id_for_thread(want)
        if not draft_id:
            return "", ""
        full = self._call(self.service.users().drafts().get(userId=USER, id=draft_id, format="full"))
        payload = (full.get("message") or {}).get("payload") or {}
        return draft_id, extract_body(payload)

    def _thread_has_draft(self, thread_id: str) -> bool:
        try:
            thread = self._call(self.service.users().threads().get(userId=USER, id=thread_id, format="minimal"))
        except HttpError:
            return True
        messages = thread.get("messages") or []
        if not messages:
            return True
        saw_labels = False
        for item in messages:
            labels = item.get("labelIds") or []
            if labels:
                saw_labels = True
            if "DRAFT" in labels:
                return True
        return not saw_labels

    def _draft_id_for_thread(self, thread_id: str) -> str:
        page_token = None
        for _ in range(10):
            kwargs: dict[str, Any] = {"userId": USER, "maxResults": 100}
            if page_token:
                kwargs["pageToken"] = page_token
            result = self._call(self.service.users().drafts().list(**kwargs))
            for item in result.get("drafts") or []:
                message = item.get("message") or {}
                if (message.get("threadId") or "") == thread_id and item.get("id"):
                    return str(item["id"])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return ""

    def delete_draft(self, draft_id: str) -> None:
        if not draft_id:
            return
        request = self.service.users().drafts().delete(userId=USER, id=draft_id)
        self._call(request)

    def apply_label(self, message_id: str, label_name: str) -> None:
        name = (label_name or "").strip()
        if not message_id or not name:
            return
        try:
            self._apply_label_once(message_id, name)
            return
        except HttpError as exc:
            logger.warning("Gmail label failed once: %s", exc, extra={"event": "label_apply_retry"})
            self._label_ids.pop(name, None)
        try:
            self._apply_label_once(message_id, name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not apply Gmail label: %s", exc, extra={"event": "label_apply_failed"})

    def _apply_label_once(self, message_id: str, label_name: str) -> None:
        label_id = self._ensure_label(label_name, force=True)
        if not label_id:
            return
        request = self.service.users().messages().modify(
            userId=USER,
            id=message_id,
            body={"addLabelIds": [label_id]},
        )
        self._call(request)

    def fetch_sent_examples(self, limit: int = 20) -> list[StyleExample]:
        return self._examples_from_ids(self.list_message_ids("in:sent", limit), limit)

    def fetch_related_sent(self, subject: str, limit: int = 8, correspondent: str = "") -> list[StyleExample]:
        from gmail_bot.draft_engine import topic_hint

        words = [w for w in topic_hint(subject, limit=6).split() if len(w) > 2][:6]
        other = normalize_email(correspondent)
        or_clause = " OR ".join(words)
        queries: list[str] = []
        if other and or_clause:
            queries.append(f"in:sent to:{other} ({or_clause})")
        if or_clause:
            queries.append(f"in:sent ({or_clause})")
        if other:
            queries.append(f"in:sent to:{other}")
        queries.append("in:sent")
        seen: list[str] = []
        for query in queries:
            try:
                ids = self.list_message_ids(query, max(limit, 8))
            except Exception:  # noqa: BLE001
                logger.warning("Related sent search failed", extra={"event": "related_sent_query_failed"})
                continue
            for message_id in ids:
                if message_id not in seen:
                    seen.append(message_id)
            if len(seen) >= limit:
                break
        return self._examples_from_ids(seen, limit)

    def _examples_from_ids(self, ids: list[str], limit: int) -> list[StyleExample]:
        examples: list[StyleExample] = []
        for message_id in ids:
            msg = self.get_message(message_id)
            body = short_snippet(msg.body, 800)
            if not body:
                continue
            examples.append(StyleExample(subject=msg.subject, body=body, language=detect_language(body)))
            if len(examples) >= limit:
                break
        return examples

    def _ensure_label(self, name: str, *, force: bool = False) -> str:
        want = (name or "").strip()
        if not want:
            return ""
        if not force and want in self._label_ids:
            return self._label_ids[want]
        request = self.service.users().labels().list(userId=USER)
        result = self._call(request)
        found = self._find_label_id(result.get("labels") or [], want)
        if found:
            self._label_ids[want] = found
            return found
        try:
            created = self._call(
                self.service.users().labels().create(
                    userId=USER,
                    body={
                        "name": want,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
            )
        except HttpError:
            relisted = self._call(self.service.users().labels().list(userId=USER))
            found = self._find_label_id(relisted.get("labels") or [], want)
            if found:
                self._label_ids[want] = found
                return found
            raise
        label_id = str(created.get("id") or "")
        if label_id:
            self._label_ids[want] = label_id
        return label_id

    def _find_label_id(self, labels: list[dict[str, Any]], name: str) -> str:
        want = name.strip().lower()
        for label in labels:
            if (label.get("name") or "").strip().lower() == want:
                return str(label.get("id") or "")
        return ""
