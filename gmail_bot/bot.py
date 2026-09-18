from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from gmail_bot.config import Settings, load_settings
from gmail_bot.draft_engine import generate_reply
from gmail_bot.gmail_adapter import GmailAdapter, GmailPort
from gmail_bot.llm import LlmPort, build_llm
from gmail_bot.logging_setup import setup_logging
from gmail_bot.models import BotStatus, ParsedMessage, StyleExample, short_snippet
from gmail_bot.rag import KnowledgeBase
from gmail_bot.resilience import backoff_seconds
from gmail_bot.store import Store

logger = logging.getLogger("gmail_bot")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def matches_filters(message: ParsedMessage, settings: Settings) -> bool:
    sender = (message.sender or "").lower()
    blob = f"{message.subject}\n{message.body}".lower()
    if settings.sender_filter and settings.sender_filter.lower() not in sender:
        return False
    if settings.keyword_filter and settings.keyword_filter.lower() not in blob:
        return False
    return True


class InboxBot:
    def __init__(
        self,
        settings: Settings | None = None,
        store: Store | None = None,
        gmail: GmailPort | None = None,
        llm: LlmPort | None = None,
        knowledge: KnowledgeBase | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        setup_logging(self.settings.log_path)
        self.store = store or Store(self.settings.database_path)
        self.gmail = gmail or GmailAdapter(self.settings)
        self.llm = llm or build_llm(self.settings)
        self.knowledge = knowledge or KnowledgeBase(self.store)
        if not self.store.list_knowledge():
            try:
                self.knowledge.rebuild_from_sources(
                    faqs_path=self.settings.faqs_path,
                    knowledge_dir=self.settings.knowledge_dir,
                )
            except Exception:  # noqa: BLE001
                logger.warning("Could not seed knowledge base", extra={"event": "kb_seed_failed"})
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_poll: str | None = None
        self.last_error: str | None = None
        self.last_activity: str | None = None
        self._fail_streak = 0
        self._style_cache: list[StyleExample] = []
        self._style_loaded = False

    def status(self) -> BotStatus:
        counts = self.store.queue_counts()
        return BotStatus(
            running=self._thread is not None and self._thread.is_alive() and not self._stop.is_set(),
            paused=self._paused.is_set(),
            oauth_ready=self.gmail.oauth_ready(),
            last_poll=self.last_poll,
            last_error=self.last_error,
            last_activity=self.last_activity,
            processed_count=self.store.processed_count(),
            queue_pending=counts.get("pending", 0) + counts.get("processing", 0),
            queue_failed=counts.get("failed", 0),
            llm=self.settings.inbox_llm,
            poll_seconds=self.settings.poll_seconds,
            gmail_query=self.settings.gmail_query,
            label_name=self.settings.label_name,
        )

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                self._paused.clear()
                return
            self._stop = threading.Event()
            self._paused.clear()
            self._thread = threading.Thread(target=self._run_loop, name="inbox-bot", daemon=True)
            self._thread.start()
            self.last_activity = f"started at {_utcnow()}"
            logger.info("Bot started", extra={"event": "bot_start"})

    def pause(self) -> None:
        self._paused.set()
        self.last_activity = f"paused at {_utcnow()}"
        logger.info("Bot paused", extra={"event": "bot_pause"})

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        self._paused.clear()
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=timeout)
        self.last_activity = f"stopped at {_utcnow()}"
        logger.info("Bot stopped", extra={"event": "bot_stop"})

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.4)
                continue
            try:
                self.process_once()
                self._fail_streak = 0
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 — worker must stay alive
                self._fail_streak += 1
                self.last_error = str(exc)
                wait = backoff_seconds(self._fail_streak, base=2.0, cap=60.0)
                logger.exception("Poll failed; backing off", extra={"event": "poll_error", "attempts": self._fail_streak})
                self.store.add_job_log("ERROR", "poll_error", str(exc)[:400])
                self._stop.wait(wait)
                continue
            self._stop.wait(self.settings.poll_seconds)

    def process_once(self) -> int:
        drafted = 0
        if not self.gmail.oauth_ready() and not getattr(self.gmail, "_service", None):
            raise RuntimeError("Gmail OAuth is not ready. Run python -m gmail_bot auth")
        self._refresh_style_examples()
        ids = self.gmail.list_unread_ids(self.settings.gmail_query)
        self.last_poll = _utcnow()
        logger.info("Polled inbox", extra={"event": "poll", "snippet": f"{len(ids)} messages"})
        for message_id in ids:
            if self._stop.is_set():
                break
            if self.store.already_processed(message_id):
                continue
            existing = self.store.get_queue_item(message_id)
            if existing and existing.get("status") == "failed" and int(existing.get("attempts") or 0) >= 5:
                continue
            if self._process_message(message_id):
                drafted += 1
        self.last_activity = f"processed {drafted} draft(s) at {self.last_poll}"
        return drafted

    def _refresh_style_examples(self) -> None:
        if self._style_loaded:
            return
        stored = self.store.list_style_examples(self.settings.sent_example_limit)
        if stored:
            self._style_cache = [
                StyleExample(subject=row["subject"] or "", body=row["body"], language=row.get("language") or "en")
                for row in stored
            ]
            self._style_loaded = True
            return
        try:
            examples = self.gmail.fetch_sent_examples(self.settings.sent_example_limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load sent-mail style examples: %s", exc, extra={"event": "style_fetch_failed"})
            return
        self._style_cache = examples
        self._style_loaded = True
        self.store.replace_style_examples([(ex.subject, ex.body, ex.language) for ex in examples])

    def _process_message(self, message_id: str) -> bool:
        message = self.gmail.get_message(message_id)
        if not matches_filters(message, self.settings):
            return False
        existing = self.store.get_queue_item(message_id)
        attempts = int(existing["attempts"]) + 1 if existing else 1
        self.store.upsert_queue(
            {
                "message_id": message.message_id,
                "thread_id": message.thread_id,
                "sender": message.sender,
                "subject": message.subject,
                "snippet": message.log_snippet,
                "status": "processing",
                "attempts": attempts,
            }
        )
        try:
            query = f"{message.subject}\n{message.body}"
            chunks = self.knowledge.retrieve(query, k=self.settings.retrieve_k)
            draft = generate_reply(
                message,
                chunks,
                self._style_cache,
                self.llm,
                assistant_name=self.settings.assistant_name,
            )
            draft_id = self.gmail.create_draft_reply(message, draft.text)
            self.gmail.apply_label(message.message_id, self.settings.label_name)
            self.store.mark_processed(message.message_id, message.thread_id, draft_id)
            self.store.upsert_queue(
                {
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "sender": message.sender,
                    "subject": message.subject,
                    "snippet": message.log_snippet,
                    "status": "drafted",
                    "attempts": attempts,
                    "draft_preview": draft.text,
                    "draft_id": draft_id,
                    "last_error": None,
                }
            )
            self.store.add_job_log(
                "INFO",
                "drafted",
                f"{message.sender} | {message.subject} | draft={draft_id} | {message.log_snippet}",
            )
            logger.info(
                "Drafted reply",
                extra={
                    "event": "drafted",
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "draft_id": draft_id,
                    "sender": message.sender,
                    "subject": message.subject,
                    "snippet": message.log_snippet,
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.store.upsert_queue(
                {
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "sender": message.sender,
                    "subject": message.subject,
                    "snippet": message.log_snippet,
                    "status": "failed",
                    "attempts": attempts,
                    "last_error": str(exc)[:400],
                }
            )
            self.store.add_job_log("ERROR", "draft_failed", f"{message.message_id}: {str(exc)[:300]}")
            logger.exception(
                "Failed to draft",
                extra={"event": "draft_failed", "message_id": message.message_id, "snippet": short_snippet(str(exc))},
            )
            return False


_CONTROLLER: InboxBot | None = None
_CONTROLLER_LOCK = threading.Lock()


def get_controller(settings: Settings | None = None) -> InboxBot:
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = InboxBot(settings=settings)
        return _CONTROLLER
