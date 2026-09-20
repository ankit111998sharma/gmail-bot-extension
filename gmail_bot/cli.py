from __future__ import annotations

import argparse
import json
from pathlib import Path

from gmail_bot.bot import InboxBot
from gmail_bot.config import load_settings, normalize_email
from gmail_bot.gmail_adapter import GmailAdapter
from gmail_bot.logging_setup import setup_logging
from gmail_bot.rag import KnowledgeBase
from gmail_bot.store import Store


def cmd_auth(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(settings.log_path)
    adapter = GmailAdapter(settings)
    hint = normalize_email(getattr(args, "email", "") or settings.gmail_account)
    adapter.authenticate(open_browser=True, force=bool(hint), login_hint=hint)
    profile = adapter.get_profile_email()
    if hint and profile.lower() != hint:
        print(f"Signed in as {profile}, but you asked for {hint}.")
        return 1
    labels = adapter.service.users().labels().list(userId="me").execute()
    names = sorted(label.get("name", "") for label in labels.get("labels") or [])
    print(f"Gmail OAuth OK for {profile}. {len(names)} labels visible.")
    print("Token saved to", settings.token_path)
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    settings = load_settings()
    bot = InboxBot(settings=settings)
    if args.once:
        count = bot.process_once()
        print(f"Created {count} draft(s). Unread mail was left unread.")
        return 0
    print("Polling inbox. Drafts only — never sends. Ctrl+C to stop.")
    bot.start()
    try:
        while True:
            bot._stop.wait(0.5)
            if not bot._thread or not bot._thread.is_alive():
                break
    except KeyboardInterrupt:
        bot.stop()
        print("Stopped.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = Store(settings.database_path)
    kb = KnowledgeBase(store)
    urls = list(args.url or [])
    extra = Path(args.notes).read_text(encoding="utf-8") if args.notes else ""
    count = kb.rebuild_from_sources(
        faqs_path=settings.faqs_path,
        knowledge_dir=settings.knowledge_dir,
        urls=urls,
        extra_notes=extra,
    )
    print(f"Indexed {count} knowledge chunks.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    bot = InboxBot(settings=settings)
    print(json.dumps(bot.status().__dict__, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail-bot",
        description="Gmail draft assistant. Creates drafts only; never sends; never marks mail as read.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Open a browser and store Gmail OAuth tokens")
    auth.add_argument("--email", help="Gmail address to sign in as")
    auth.set_defaults(func=cmd_auth)

    inbox = sub.add_parser("inbox", help="Poll unread mail and create draft replies")
    inbox.add_argument("--once", action="store_true", help="Process current unread messages and exit")
    inbox.set_defaults(func=cmd_inbox)

    ingest = sub.add_parser("ingest", help="Rebuild the local knowledge index")
    ingest.add_argument("--url", action="append", help="Website URL to scrape (repeatable)")
    ingest.add_argument("--notes", help="Path to extra notes/markdown to index")
    ingest.set_defaults(func=cmd_ingest)

    status = sub.add_parser("status", help="Print bot status as JSON")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
