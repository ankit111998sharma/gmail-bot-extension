from __future__ import annotations

from pathlib import Path

import pytest

from gmail_bot.config import Settings
from gmail_bot.models import ParsedMessage, StyleExample
from gmail_bot.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path
    s = Settings(
        project_root=root,
        credentials_path=root / "data" / "gmail" / "credentials.json",
        token_path=root / "data" / "gmail" / "token.json",
        database_path=root / "data" / "gmail_bot.db",
        log_path=root / "logs" / "assistant.log",
        knowledge_dir=root / "data" / "knowledge",
        poll_seconds=5,
        inbox_llm="placeholder",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings.database_path)


def make_message(**kwargs) -> ParsedMessage:
    data = dict(
        message_id="m1",
        thread_id="t1",
        sender="Ada <ada@example.com>",
        subject="Office hours?",
        body="Hi, what are your support hours?",
        snippet="Hi, what are your support hours?",
        message_id_header="<id-1@mail.example.com>",
        references="",
        reply_to="",
    )
    data.update(kwargs)
    return ParsedMessage(**data)


class FakeGmail:
    def __init__(self, messages: list[ParsedMessage] | None = None, profile_email: str = "me@gmail.com") -> None:
        self.messages = {m.message_id: m for m in messages or []}
        self.drafts: list[dict] = []
        self.labels: list[tuple[str, str]] = []
        self.sent_examples: list[StyleExample] = []
        self.send_called = False
        self.profile_email = profile_email
        self.auth_hints: list[str] = []

    def oauth_ready(self) -> bool:
        return True

    def authenticate(self, open_browser: bool = True, force: bool = False, login_hint: str = "") -> None:
        self.auth_hints.append(login_hint)
        if login_hint:
            self.profile_email = login_hint

    def get_profile_email(self) -> str:
        return self.profile_email

    def list_unread_ids(self, query: str) -> list[str]:
        return self.list_message_ids(query, limit=500)

    def list_message_ids(self, query: str, limit: int = 10) -> list[str]:
        import re

        q = (query or "").lower()
        mine = (self.profile_email or "").lower()
        skip_me = bool(re.search(r"(^|\s)-from:me(\s|$)", q))
        sent_only = "in:sent" in q
        from_match = re.search(r"(?<!-)from:([^\s\"']+)", q)
        from_token = (from_match.group(1) if from_match else "").strip("\"'")
        subject_token = ""
        if "subject:" in q:
            subject_token = q.split("subject:", 1)[1].strip().strip("\"'")
        ids: list[str] = []
        for message in self.messages.values():
            sender = (message.sender or "").lower()
            is_mine = bool(mine and mine in sender)
            if skip_me and is_mine:
                continue
            if sent_only and not is_mine:
                continue
            if from_token and from_token not in sender:
                continue
            if subject_token:
                subject = (message.subject or "").lower()
                if subject_token not in subject and subject not in subject_token:
                    continue
            ids.append(message.message_id)
            if len(ids) >= limit:
                break
        return ids

    def get_message(self, message_id: str) -> ParsedMessage:
        return self.messages[message_id]

    def create_draft_reply(self, message: ParsedMessage, reply_text: str, from_email: str = "") -> str:
        from gmail_bot.gmail_adapter import reply_recipient

        draft_id = f"draft-{len(self.drafts) + 1}"
        self.drafts.append(
            {
                "id": draft_id,
                "thread_id": message.thread_id,
                "to": reply_recipient(message, from_email),
                "from": from_email,
                "text": reply_text,
                "message_id_header": message.message_id_header,
            }
        )
        return draft_id

    def delete_draft(self, draft_id: str) -> None:
        if not draft_id:
            return
        self.drafts[:] = [row for row in self.drafts if row["id"] != draft_id]

    def apply_label(self, message_id: str, label_name: str) -> None:
        self.labels.append((message_id, label_name))

    def fetch_sent_examples(self, limit: int = 20) -> list[StyleExample]:
        return self.sent_examples[:limit]

    def fetch_related_sent(self, subject: str = "", limit: int = 8, correspondent: str = "") -> list[StyleExample]:
        from gmail_bot.draft_engine import topic_hint

        words = [w.lower() for w in topic_hint(subject, limit=6).split() if len(w) > 2]
        if not words:
            return self.sent_examples[:limit]
        scored: list[tuple[int, StyleExample]] = []
        for example in self.sent_examples:
            blob = f"{example.subject} {example.body}".lower()
            score = sum(1 for word in words if word in blob)
            if score:
                scored.append((score, example))
        scored.sort(key=lambda row: -row[0])
        picked = [example for _, example in scored[:limit]]
        return picked or self.sent_examples[:limit]

    def send(self, *args, **kwargs):  # safety: tests fail if bot ever calls this
        self.send_called = True
        raise AssertionError("send() must never be called")

    def mark_as_read(self, *args, **kwargs):
        raise AssertionError("mark_as_read() must never be called")
