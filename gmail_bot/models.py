from __future__ import annotations

from dataclasses import dataclass, field


def short_snippet(text: str | None, limit: int = 120) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    snippet: str
    message_id_header: str = ""
    references: str = ""
    reply_to: str = ""
    to_header: str = ""
    cc_header: str = ""

    @property
    def draft_to(self) -> str:
        return self.reply_to or self.sender

    @property
    def log_snippet(self) -> str:
        return short_snippet(self.snippet or self.body)


@dataclass
class StyleExample:
    subject: str
    body: str
    language: str = "en"


@dataclass
class RetrievedChunk:
    source: str
    text: str
    score: float = 0.0


@dataclass
class DraftResult:
    text: str
    engine: str
    language: str
    used_chunks: list[RetrievedChunk] = field(default_factory=list)
    missing_context: bool = False


@dataclass
class BotStatus:
    running: bool
    paused: bool
    oauth_ready: bool
    last_poll: str | None
    last_error: str | None
    last_activity: str | None
    processed_count: int
    queue_pending: int
    queue_failed: int
    llm: str
    poll_seconds: int
    gmail_query: str
    label_name: str
    target_email: str = ""
    connected_email: str = ""
