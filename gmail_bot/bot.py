from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from gmail_bot.config import Settings, load_settings, normalize_email, same_email
from gmail_bot.draft_engine import generate_reply, topic_hint
from gmail_bot.gmail_adapter import GmailAdapter, GmailPort, reply_recipient
from gmail_bot.guardian import ProjectGuardian, is_retryable_draft_error
from gmail_bot.llm import LlmPort, build_llm
from gmail_bot.logging_setup import setup_logging
from gmail_bot.models import BotStatus, ParsedMessage, RetrievedChunk, StyleExample, short_snippet
from gmail_bot.rag import KnowledgeBase
from gmail_bot.resilience import backoff_seconds
from gmail_bot.rules import fetch_page_rules, normalize_rules_url
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


def strip_reply_prefix(subject: str | None) -> str:
    value = (subject or "").strip()
    while value.lower().startswith("re:"):
        value = value[3:].strip()
    return value


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
        self.guardian = ProjectGuardian(self)
        self._guardian_stop = threading.Event()
        self._guardian_thread: threading.Thread | None = None
        saved_account = normalize_email(self.store.get_setting("gmail_account"))
        if saved_account:
            self.settings.gmail_account = saved_account

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
            target_email=normalize_email(self.settings.gmail_account),
            connected_email=normalize_email(self.store.get_setting("connected_email")),
        )

    def set_target_email(self, email: str) -> str:
        cleaned = normalize_email(email)
        if not cleaned:
            raise ValueError("Enter a valid email address, for example you@gmail.com.")
        previous = normalize_email(self.settings.gmail_account)
        self.settings.gmail_account = cleaned
        self.store.set_setting("gmail_account", cleaned)
        if previous and previous != cleaned:
            self._style_loaded = False
            self._style_cache = []
            self.store.replace_style_examples([])
            self.store.set_setting("connected_email", "")
        return cleaned

    def connect_account(self, email: str) -> str:
        cleaned = self.set_target_email(email)
        current = ""
        if self.gmail.oauth_ready():
            try:
                current = normalize_email(self.gmail.get_profile_email())
            except Exception:  # noqa: BLE001
                current = ""
        self.gmail.authenticate(open_browser=True, force=current != cleaned, login_hint=cleaned)
        profile = normalize_email(self.gmail.get_profile_email())
        if profile != cleaned:
            raise RuntimeError(
                f"Signed in as {profile or 'unknown'}, but you asked for {cleaned}. "
                "Choose that same Gmail in the browser window."
            )
        self.store.set_setting("connected_email", profile)
        self.last_activity = f"connected {profile} at {_utcnow()}"
        return profile

    def _assert_account(self) -> None:
        target = normalize_email(self.settings.gmail_account)
        if not target:
            return
        if not self.gmail.oauth_ready() and not getattr(self.gmail, "_service", None):
            raise RuntimeError(f"Connect {target} first, then start the bot.")
        getter = getattr(self.gmail, "get_profile_email", None)
        if not callable(getter):
            return
        try:
            profile = normalize_email(getter())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not read the signed-in Gmail account: {exc}") from exc
        if profile and profile != target:
            raise RuntimeError(
                f"This bot is set to {target}, but Gmail is signed in as {profile}. "
                "Enter that address or click Connect this Gmail."
            )
        if profile:
            self.store.set_setting("connected_email", profile)

    def start_guardian(self) -> None:
        if not getattr(self.settings, "guardian_enabled", True):
            return
        with self._lock:
            if self._guardian_thread and self._guardian_thread.is_alive():
                return
            self._guardian_stop = threading.Event()
            self._guardian_thread = threading.Thread(
                target=self._guardian_loop, name="project-guardian", daemon=True
            )
            self._guardian_thread.start()
            logger.info("Project guardian started", extra={"event": "guardian_start"})

    def stop_guardian(self) -> None:
        self._guardian_stop.set()

    def _guardian_loop(self) -> None:
        interval = max(15, int(getattr(self.settings, "guardian_seconds", 45) or 45))
        while not self._guardian_stop.wait(interval):
            try:
                self.guardian.scan_and_repair()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Guardian scan failed: %s", exc, extra={"event": "guardian_scan_failed"})

    def retry_failed_drafts(self, limit: int = 3) -> int:
        recovered = 0
        for item in self.store.list_failed_queue(limit=limit):
            error = item.get("last_error") or ""
            if error and not is_retryable_draft_error(str(error)):
                continue
            try:
                self.draft_from_open_mail(
                    item.get("sender") or "",
                    item.get("subject") or "",
                    item.get("snippet") or "",
                    existing_draft=item.get("draft_preview") or "",
                )
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not retry draft: %s", exc, extra={"event": "guardian_retry_item_failed"})
        return recovered

    def set_rules_url(self, url: str) -> str:
        cleaned = (url or "").strip()
        if cleaned:
            cleaned = normalize_rules_url(cleaned)
        self.store.set_setting("rules_url", cleaned)
        return cleaned

    def _load_rules(self, rules_url: str, topic: str) -> tuple[list[str], list[str]]:
        raw = (rules_url or "").strip()
        if not raw:
            return [], []
        try:
            url = normalize_rules_url(raw)
        except ValueError as exc:
            return [], [str(exc)]
        self.store.set_setting("rules_url", url)
        try:
            return fetch_page_rules(url, topic), []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read rules URL: %s", exc, extra={"event": "rules_url_failed"})
            return [], [f"Could not read the rules URL: {exc}"]

    def _owner_identity(self) -> tuple[str, str]:
        email = ""
        getter = getattr(self.gmail, "get_profile_email", None)
        if callable(getter):
            try:
                email = normalize_email(getter())
            except Exception:  # noqa: BLE001
                email = ""
        if email:
            stored = normalize_email(self.store.get_setting("connected_email"))
            if stored != email:
                self.store.set_setting("connected_email", email)
        else:
            email = normalize_email(self.store.get_setting("connected_email") or self.settings.gmail_account)
        name = (self.settings.assistant_name or "").strip()
        if not name or name.lower() in {"gmail bot", "gmailbot", "automated assistant"}:
            name = email.split("@")[0] if email else "Me"
        return name, email

    def start(self) -> None:
        self._assert_account()
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
            raise RuntimeError("Gmail OAuth is not ready. Connect the Gmail address from the app.")
        self._assert_account()
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

    def draft_from_open_mail(
        self,
        sender: str,
        subject: str,
        body: str = "",
        existing_draft: str = "",
        rules_url: str = "",
        notes: str = "",
        thread_id: str = "",
        gmail_draft_id: str = "",
        page_email: str = "",
    ) -> dict[str, Any]:
        """Create or redraft a short reply for the open Gmail message. Click-only."""
        if not self.gmail.oauth_ready() and not getattr(self.gmail, "_service", None):
            raise RuntimeError("Gmail is not connected. Keep this app running and click Connect this Gmail.")
        self._assert_account()
        self._refresh_style_examples()
        found = self._find_open_message(sender, subject, body, thread_id=thread_id)
        if not (existing_draft or "").strip():
            queued = self.store.get_queue_item(found.message_id)
            if queued:
                existing_draft = queued.get("draft_preview") or existing_draft
        _, owner_email = self._owner_identity()
        page = normalize_email(page_email)
        if page and owner_email and page != owner_email:
            raise RuntimeError(
                f"This Gmail tab is {page}, but the OAuth token is for {owner_email}. "
                "Open that inbox or click Connect this Gmail so drafts are created on your behalf."
            )
        reply_sender = found.sender
        if same_email(reply_sender, owner_email) or same_email(sender, owner_email):
            reply_sender = reply_recipient(found, owner_email) or found.sender
        elif sender:
            reply_sender = sender
        reply_body = found.body
        if body and not same_email(sender, owner_email):
            reply_body = body
        message = ParsedMessage(
            message_id=found.message_id,
            thread_id=found.thread_id,
            sender=reply_sender or found.sender,
            subject=found.subject or subject,
            body=reply_body,
            snippet=found.snippet,
            message_id_header=found.message_id_header,
            references=found.references,
            reply_to=found.reply_to,
            to_header=found.to_header,
            cc_header=found.cc_header,
        )
        rules, fetch_notes = self._load_rules(rules_url, f"{message.subject}\n{topic_hint(message.subject)}")
        payload = self._write_draft(
            message,
            replace_existing=True,
            existing_draft=existing_draft,
            rules=rules,
            notes=notes,
            gmail_draft_id=gmail_draft_id,
        )
        suggestions = list(payload.get("suggestions") or []) + fetch_notes
        self.last_activity = f"drafted open mail at {_utcnow()}"
        return {
            "draft_id": payload["draft_id"],
            "message_id": message.message_id,
            "subject": message.subject,
            "sender": message.sender,
            "text": payload["text"],
            "draftText": payload["text"],
            "suggestions": suggestions,
            "rulesUrl": self.store.get_setting("rules_url"),
            "mode": "reply",
        }

    def draft_compose_mail(
        self,
        to: str,
        subject: str = "",
        existing_draft: str = "",
        rules_url: str = "",
        notes: str = "",
        gmail_draft_id: str = "",
        thread_id: str = "",
        page_email: str = "",
    ) -> dict[str, Any]:
        """Create or update a standalone Gmail draft (not an in-thread reply)."""
        if not self.gmail.oauth_ready() and not getattr(self.gmail, "_service", None):
            raise RuntimeError("Gmail is not connected. Keep this app running and click Connect this Gmail.")
        self._assert_account()
        self._refresh_style_examples()
        _, owner_email = self._owner_identity()
        page = normalize_email(page_email)
        if page and owner_email and page != owner_email:
            raise RuntimeError(
                f"This Gmail tab is {page}, but the OAuth token is for {owner_email}. "
                "Open that inbox or click Connect this Gmail so drafts are created on your behalf."
            )
        draft_id_hint = (gmail_draft_id or "").strip()
        live_to = (to or "").strip()
        live_subject = (subject or "").strip()
        live_body = (existing_draft or "").strip()
        live_thread = (thread_id or "").strip()
        if draft_id_hint:
            getter = getattr(self.gmail, "get_draft", None)
            if callable(getter):
                try:
                    meta = getter(draft_id_hint) or {}
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not load the open Gmail draft: %s", exc, extra={"event": "draft_get_failed"})
                    meta = {}
                live_thread = live_thread or str(meta.get("thread_id") or "")
                live_to = live_to or str(meta.get("to") or "")
                live_subject = live_subject or str(meta.get("subject") or "")
                if not live_body:
                    live_body = str(meta.get("text") or "")
        if not reply_recipient(
            ParsedMessage(
                message_id="",
                thread_id="",
                sender=live_to,
                subject=live_subject,
                body="",
                snippet="",
                to_header=live_to,
            ),
            owner_email,
        ):
            raise RuntimeError("Enter who this Gmail draft is To, or open a draft that already has a recipient.")
        key = f"draft:{draft_id_hint}" if draft_id_hint else f"compose:{normalize_email(live_to)}:{(live_subject or 'new')[:80]}"
        message = ParsedMessage(
            message_id=key,
            thread_id=live_thread,
            sender=live_to,
            subject=live_subject or topic_hint(notes) or "(No Subject)",
            body=live_body or notes,
            snippet=short_snippet(live_body or notes),
            to_header=live_to,
        )
        rules, fetch_notes = self._load_rules(rules_url, f"{message.subject}\n{topic_hint(message.subject)}")
        payload = self._write_draft(
            message,
            replace_existing=True,
            existing_draft=live_body,
            rules=rules,
            notes=notes,
            gmail_draft_id=draft_id_hint,
            standalone=True,
        )
        suggestions = list(payload.get("suggestions") or []) + fetch_notes
        self.last_activity = f"wrote Gmail draft at {_utcnow()}"
        return {
            "draft_id": payload["draft_id"],
            "message_id": message.message_id,
            "subject": message.subject,
            "sender": message.sender,
            "text": payload["text"],
            "draftText": payload["text"],
            "suggestions": suggestions,
            "rulesUrl": self.store.get_setting("rules_url"),
            "mode": "compose",
        }

    def redraft_existing(
        self, message_id: str, draft_text: str = "", rules_url: str = "", notes: str = ""
    ) -> dict[str, Any]:
        item = self.store.get_queue_item(message_id)
        if not item:
            raise RuntimeError("That draft was not found. Open the email in Gmail and draft it first.")
        sender = item.get("sender") or ""
        subject = item.get("subject") or ""
        body = item.get("snippet") or ""
        getter = getattr(self.gmail, "get_message", None)
        if callable(getter):
            try:
                found = getter(message_id)
                sender = found.sender or sender
                subject = found.subject or subject
                body = found.body or body
            except Exception:  # noqa: BLE001
                logger.warning("Could not reload message for redraft", extra={"event": "redraft_lookup_failed"})
        existing = draft_text or item.get("draft_preview") or ""
        return self.draft_from_open_mail(
            sender,
            subject,
            body,
            existing_draft=existing,
            rules_url=rules_url,
            notes=notes,
        )

    def _reply_llm(self, notes: str, rules: list[str] | None) -> LlmPort:
        if not ((notes or "").strip() or (rules or [])):
            return self.llm
        if getattr(self.llm, "name", "") != "placeholder":
            return self.llm
        try:
            return build_llm(self.settings, prefer_ai=True)
        except Exception:  # noqa: BLE001
            return self.llm

    def _find_open_message(
        self, sender: str, subject: str, body: str = "", thread_id: str = ""
    ) -> ParsedMessage:
        _, owner_email = self._owner_identity()
        lister = getattr(self.gmail, "list_message_ids", None)
        tid = (thread_id or "").strip()
        if tid and callable(lister):
            try:
                thread_ids = lister(f"thread:{tid}", 30)
            except Exception:  # noqa: BLE001
                thread_ids = []
            chosen: ParsedMessage | None = None
            for message_id in thread_ids:
                try:
                    message = self.gmail.get_message(message_id)
                except Exception:  # noqa: BLE001
                    continue
                if same_email(message.sender, owner_email):
                    if chosen is None:
                        chosen = message
                    continue
                chosen = message
            if chosen is not None:
                return chosen
        other = "" if same_email(sender, owner_email) else normalize_email(sender)
        want = strip_reply_prefix(subject).lower()
        want_q = want.replace('"', "")
        queries: list[str] = []
        if other and want_q:
            queries.append(f'in:inbox from:{other} subject:"{want_q}"')
        if other:
            queries.append(f"in:inbox from:{other}")
        if want_q:
            queries.append(f'in:inbox -from:me subject:"{want_q}"')
        queries.append("in:inbox -from:me")
        seen: list[str] = []
        for query in queries:
            ids = lister(query, 10) if callable(lister) else self.gmail.list_unread_ids(query)
            for message_id in ids:
                if message_id not in seen:
                    seen.append(message_id)
        fallback: ParsedMessage | None = None
        for message_id in seen:
            message = self.gmail.get_message(message_id)
            if same_email(message.sender, owner_email):
                continue
            have = strip_reply_prefix(message.subject).lower()
            subject_ok = (not want) or want in have or have in want
            sender_ok = (not other) or other in (message.sender or "").lower()
            if subject_ok and sender_ok:
                return message
            if fallback is None and (subject_ok or sender_ok):
                fallback = message
        if fallback is not None:
            return fallback
        raise RuntimeError("Could not find this email in the connected Gmail. Open the other person's message and try again.")

    def _style_examples_for(self, message: ParsedMessage) -> list[StyleExample]:
        related: list[StyleExample] = []
        fetcher = getattr(self.gmail, "fetch_related_sent", None)
        if callable(fetcher):
            try:
                related = list(
                    fetcher(message.subject, 8, normalize_email(message.sender)) or []
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not load related sent mail: %s",
                    exc,
                    extra={"event": "related_sent_failed"},
                )
        merged: list[StyleExample] = []
        seen: set[tuple[str, str]] = set()
        for example in related + list(self._style_cache):
            key = (example.subject, example.body)
            if key in seen or not (example.body or "").strip():
                continue
            seen.add(key)
            merged.append(example)
        return merged

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
            self._style_loaded = True
            return
        self._style_cache = examples
        self._style_loaded = True
        self.store.replace_style_examples([(ex.subject, ex.body, ex.language) for ex in examples])

    def _write_draft(
        self,
        message: ParsedMessage,
        *,
        replace_existing: bool = False,
        existing_draft: str = "",
        rules: list[str] | None = None,
        notes: str = "",
        gmail_draft_id: str = "",
        standalone: bool = False,
    ) -> dict[str, Any]:
        if not standalone and not matches_filters(message, self.settings):
            raise RuntimeError("This email did not match the saved sender/keyword filters.")
        _, owner_email = self._owner_identity()
        if not reply_recipient(message, owner_email):
            raise RuntimeError(
                "Enter who this Gmail draft is To."
                if standalone
                else "Could not find who to reply to. Open the other person's message."
            )
        existing = self.store.get_queue_item(message.message_id)
        live_id, gmail_draft_text = self._lookup_thread_draft(message.thread_id)
        if not (existing_draft or "").strip():
            existing_draft = gmail_draft_text or ((existing or {}).get("draft_preview") or "")
        open_id = (gmail_draft_id or "").strip()
        stored_id = str((existing or {}).get("draft_id") or "")
        draft_id_to_update = open_id or live_id or stored_id
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
        query = f"{message.subject}\n{message.body}\n{existing_draft}\n{notes}"
        chunks = [
            RetrievedChunk(source="url-rules", text=rule, score=1.0) for rule in (rules or []) if rule.strip()
        ] + self.knowledge.retrieve(query, k=self.settings.retrieve_k)
        owner_name, owner_email = self._owner_identity()
        draft = generate_reply(
            message,
            chunks,
            self._style_examples_for(message),
            self._reply_llm(notes, rules),
            assistant_name=owner_name,
            owner_email=owner_email,
            existing_draft=existing_draft,
            rules=rules or [],
            notes=notes,
            standalone=standalone,
        )
        draft_id = self._save_draft_reply(
            message, draft.text, owner_email, draft_id_to_update, owner_name, standalone=standalone
        )
        if not standalone:
            try:
                self.gmail.apply_label(message.message_id, self.settings.label_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not label drafted mail: %s", exc, extra={"event": "label_apply_failed"})
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
        return {"text": draft.text, "draft_id": draft_id, "suggestions": draft.suggestions}

    def _lookup_thread_draft(self, thread_id: str) -> tuple[str, str]:
        finder = getattr(self.gmail, "find_thread_draft", None)
        if not callable(finder) or not (thread_id or "").strip():
            return "", ""
        try:
            found = finder(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not look up an existing Gmail draft: %s", exc, extra={"event": "draft_lookup_failed"})
            return "", ""
        if not found:
            return "", ""
        draft_id = str(found[0] or "")
        text = str(found[1] or "") if len(found) > 1 else ""
        return draft_id, text

    def _save_draft_reply(
        self,
        message: ParsedMessage,
        text: str,
        owner_email: str,
        draft_id: str,
        owner_name: str = "",
        standalone: bool = False,
    ) -> str:
        updater = getattr(self.gmail, "update_draft_reply", None)
        tried: set[str] = set()

        def try_update(candidate: str) -> str:
            want = (candidate or "").strip()
            if not want or want in tried or not callable(updater):
                return ""
            tried.add(want)
            try:
                return updater(
                    want,
                    message,
                    text,
                    from_email=owner_email,
                    from_name=owner_name,
                    standalone=standalone,
                )
            except TypeError:
                return updater(want, message, text, from_email=owner_email)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not update existing draft: %s", exc, extra={"event": "draft_update_failed"})
                return ""

        updated = try_update(draft_id)
        if updated:
            return updated
        if not standalone:
            live_id, _ = self._lookup_thread_draft(message.thread_id)
            updated = try_update(live_id)
            if updated:
                return updated
        try:
            return self.gmail.create_draft_reply(
                message,
                text,
                from_email=owner_email,
                from_name=owner_name,
                standalone=standalone,
            )
        except TypeError:
            return self.gmail.create_draft_reply(message, text, from_email=owner_email)

    def _process_message(self, message_id: str) -> bool:
        message = self.gmail.get_message(message_id)
        _, owner_email = self._owner_identity()
        if same_email(message.sender, owner_email):
            return False
        if not matches_filters(message, self.settings):
            return False
        try:
            self._write_draft(message)
            return True
        except Exception as exc:  # noqa: BLE001
            existing = self.store.get_queue_item(message_id)
            attempts = int(existing["attempts"]) + 1 if existing else 1
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
            _CONTROLLER.start_guardian()
        return _CONTROLLER
