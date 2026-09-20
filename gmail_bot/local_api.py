from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from gmail_bot.bot import InboxBot, get_controller

logger = logging.getLogger("gmail_bot")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def make_handler(bot: InboxBot) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            logger.info("local-api " + format, *args, extra={"event": "local_api"})

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _write(self, code: int, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/health", "/api/health"}:
                status = bot.status()
                self._write(
                    200,
                    {
                        "ok": True,
                        "oauth_ready": status.oauth_ready,
                        "connected_email": status.connected_email,
                        "target_email": status.target_email,
                        "rules_url": bot.store.get_setting("rules_url"),
                        "ai_ready": bool(getattr(bot.settings, "gemini_api_key", ""))
                        or bot.settings.inbox_llm in {"gemini", "ollama"},
                        "guardian": getattr(bot, "guardian", None).summary() if getattr(bot, "guardian", None) else {},
                    },
                )
                return
            self._write(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/draft-open":
                self._write(404, {"ok": False, "error": "Not found"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._write(400, {"ok": False, "error": "Invalid JSON"})
                return
            sender = str(data.get("sender") or "")
            subject = str(data.get("subject") or "")
            body = str(data.get("body") or "")
            existing_draft = str(data.get("existingDraft") or data.get("existing_draft") or "")
            rules_url = str(data.get("rulesUrl") or data.get("rules_url") or data.get("websiteUrl") or "")
            notes = str(data.get("notes") or data.get("description") or "")
            thread_id = str(data.get("threadId") or data.get("thread_id") or "")
            gmail_draft_id = str(
                data.get("gmailDraftId") or data.get("gmail_draft_id") or data.get("draftId") or ""
            )
            page_email = str(data.get("pageEmail") or data.get("page_email") or "")
            if not sender and not subject:
                self._write(400, {"ok": False, "error": "Open an email in Gmail first."})
                return
            try:
                result = bot.draft_from_open_mail(
                    sender,
                    subject,
                    body,
                    existing_draft=existing_draft,
                    rules_url=rules_url,
                    notes=notes,
                    thread_id=thread_id,
                    gmail_draft_id=gmail_draft_id,
                    page_email=page_email,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Click-to-draft failed: %s", exc, extra={"event": "click_draft_failed"})
                self._write(400, {"ok": False, "error": str(exc)})
                return
            self._write(200, {"ok": True, **result})

    return Handler


def start_local_api(port: int = 8787, bot: InboxBot | None = None) -> ThreadingHTTPServer:
    worker = bot or get_controller()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(worker))
    thread = threading.Thread(target=server.serve_forever, name="gmail-local-api", daemon=True)
    thread.start()
    logger.info("Local click-to-draft API on 127.0.0.1:%s", port, extra={"event": "local_api_start"})
    return server
