from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gmail_bot.models import short_snippet
from gmail_bot.rules import normalize_rules_url

logger = logging.getLogger("gmail_bot")

_EXTENSION_FILES = (
    "manifest.json",
    "background.js",
    "content.js",
    "content.css",
    "popup.html",
    "popup.js",
    "popup.css",
)
_RETRYABLE = (
    "labelid",
    "label id",
    "quota",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "backend error",
    "internal error",
)
_EMPTY_FAQS = '{\n  "faqs": []\n}\n'


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectGuardian:
    """Background scanner that repairs runtime problems in this project only.

    It does not copy other people's extensions or rewrite source files.
    Optional websites are pages you list, used as knowledge — not scraped bots.
    """

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.last_report: dict[str, Any] = {}
        self._lock = threading.Lock()

    def summary(self) -> dict[str, Any]:
        report = dict(self.last_report)
        report.setdefault("ok", True)
        report.setdefault("fixed", [])
        report.setdefault("suggestions", [])
        report.setdefault("scanned_at", "")
        return report

    def scan_and_repair(self, *, learn_urls: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            fixed: list[str] = []
            suggestions: list[str] = []
            settings = self.bot.settings
            settings.ensure_dirs()
            fixed.extend(self._repair_faqs(settings.faqs_path))
            fixed.extend(self._repair_knowledge())
            suggestions.extend(self._scan_extension(settings.project_root / "extension"))
            suggestions.extend(self._scan_oauth())
            suggestions.extend(self._scan_safety(settings.project_root / "gmail_bot"))
            fixed.extend(self._repair_gmail_cache())
            fixed.extend(self._retry_failed_drafts())
            learned = self._learn_from_urls(learn_urls)
            if learned:
                fixed.append(learned)
            report = {
                "ok": True,
                "scanned_at": _utcnow(),
                "fixed": fixed,
                "suggestions": suggestions[:12],
            }
            self.last_report = report
            self.bot.store.set_setting("guardian_last_report", json.dumps(report))
            if fixed:
                self.bot.store.add_job_log("INFO", "guardian_fixed", "; ".join(fixed)[:400])
            logger.info(
                "Guardian scan finished",
                extra={"event": "guardian_scan", "snippet": f"{len(fixed)} fix(es)"},
            )
            return report

    def _repair_faqs(self, path: Path) -> list[str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.stat().st_size == 0:
            path.write_text(_EMPTY_FAQS, encoding="utf-8")
            return ["Restored an empty FAQs file."]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = path.with_suffix(".json.bak")
            try:
                backup.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            except OSError:
                pass
            path.write_text(_EMPTY_FAQS, encoding="utf-8")
            return ["Repaired invalid FAQs JSON."]
        if not isinstance(parsed, dict) or "faqs" not in parsed:
            path.write_text(_EMPTY_FAQS, encoding="utf-8")
            return ["Repaired FAQs JSON that was missing a faqs list."]
        return []

    def _repair_knowledge(self) -> list[str]:
        if self.bot.store.list_knowledge():
            return []
        try:
            count = self.bot.knowledge.rebuild_from_sources(
                faqs_path=self.bot.settings.faqs_path,
                knowledge_dir=self.bot.settings.knowledge_dir,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Guardian could not rebuild knowledge: %s", exc, extra={"event": "guardian_kb_failed"})
            return []
        if count:
            return [f"Rebuilt the knowledge index ({count} pieces)."]
        return []

    def _scan_extension(self, folder: Path) -> list[str]:
        if not folder.is_dir():
            return ["The Chrome extension folder is missing. Keep the extension directory in this project."]
        missing = [name for name in _EXTENSION_FILES if not (folder / name).is_file()]
        if missing:
            return [f"Extension file missing: {', '.join(missing)}. Reload the unpacked extension after restoring it."]
        manifest = (folder / "manifest.json").read_text(encoding="utf-8")
        notes: list[str] = []
        if "popup.html" not in manifest:
            notes.append("The extension manifest should show popup.html so URL and notes appear in Chrome.")
        if '"scripting"' not in manifest:
            notes.append("The extension should include the scripting permission so it can recover a stale Gmail tab.")
        return notes

    def _scan_oauth(self) -> list[str]:
        notes: list[str] = []
        if not self.bot.settings.credentials_path.is_file():
            notes.append("Add data/gmail/credentials.json, then click Connect this Gmail.")
        elif not self.bot.gmail.oauth_ready():
            notes.append("Gmail is not signed in. Click Connect this Gmail on Home.")
        return notes

    def _scan_safety(self, folder: Path) -> list[str]:
        if not folder.is_dir():
            return []
        notes: list[str] = []
        for path in folder.glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "users().messages().send" in text:
                notes.append(f"{path.name} must not send mail. Drafts only.")
            if 'removeLabelIds' in text and "UNREAD" in text:
                notes.append(f"{path.name} must not mark messages read.")
        return notes

    def _repair_gmail_cache(self) -> list[str]:
        cache = getattr(self.bot.gmail, "_label_ids", None)
        if isinstance(cache, dict) and cache:
            cache.clear()
            return ["Cleared a stale Gmail label cache."]
        return []

    def _retry_failed_drafts(self) -> list[str]:
        retry = getattr(self.bot, "retry_failed_drafts", None)
        if not callable(retry):
            return []
        try:
            count = retry(limit=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Guardian retry failed: %s", exc, extra={"event": "guardian_retry_failed"})
            return []
        if count:
            return [f"Retried {count} failed draft(s)."]
        return []

    def _learn_from_urls(self, urls: list[str] | None) -> str:
        if not urls:
            return ""
        cleaned: list[str] = []
        for item in urls:
            try:
                cleaned.append(normalize_rules_url(item))
            except ValueError:
                continue
        if not cleaned:
            return ""
        try:
            count = self.bot.knowledge.rebuild_from_sources(
                faqs_path=self.bot.settings.faqs_path,
                knowledge_dir=self.bot.settings.knowledge_dir,
                urls=cleaned,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Guardian could not learn from URLs: %s", exc, extra={"event": "guardian_learn_failed"})
            return ""
        self.bot.store.set_setting("guardian_learn_urls", "\n".join(cleaned))
        return f"Indexed {count} pieces from {len(cleaned)} website(s) you listed."


def is_retryable_draft_error(text: str) -> bool:
    blob = (text or "").lower()
    return any(token in blob for token in _RETRYABLE)


def short_report(report: dict[str, Any]) -> str:
    when = report.get("scanned_at") or ""
    fixed = report.get("fixed") or []
    if fixed:
        return f"{when} · {short_snippet('; '.join(fixed), 180)}"
    return f"{when} · no repairs needed" if when else "No scan yet."
