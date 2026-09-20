from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_EMAIL_RE = re.compile(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", re.I)


def normalize_email(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = _EMAIL_RE.search(text)
    return (match.group(1) if match else "").lower()


def same_email(left: str | None, right: str | None) -> bool:
    a = normalize_email(left)
    b = normalize_email(right)
    return bool(a and b and a == b)


def _as_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass
class Settings:
    project_root: Path = PROJECT_ROOT
    credentials_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "gmail" / "credentials.json")
    token_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "gmail" / "token.json")
    database_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "gmail_bot.db")
    log_path: Path = field(default_factory=lambda: PROJECT_ROOT / "logs" / "assistant.log")
    knowledge_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "knowledge")
    gmail_query: str = "is:unread label:INBOX"
    poll_seconds: int = 60
    label_name: str = "GmailBot-Drafted"
    inbox_llm: str = "placeholder"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3"
    sender_filter: str = ""
    keyword_filter: str = ""
    sent_example_limit: int = 20
    retrieve_k: int = 5
    gmail_min_interval: float = 0.25
    assistant_name: str = "Gmail Bot"
    gmail_account: str = ""
    local_api_port: int = 8787

    def ensure_dirs(self) -> None:
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    @property
    def faqs_path(self) -> Path:
        return self.knowledge_dir / "faqs.json"

    @property
    def oauth_ready(self) -> bool:
        return self.credentials_path.is_file() and self.token_path.is_file()


def _resolve(root: Path, raw: str | None, default: Path) -> Path:
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or PROJECT_ROOT).resolve()
    load_dotenv(root / ".env", override=False)
    settings = Settings(
        project_root=root,
        credentials_path=_resolve(
            root, os.getenv("GMAIL_CREDENTIALS_PATH"), root / "data" / "gmail" / "credentials.json"
        ),
        token_path=_resolve(root, os.getenv("GMAIL_TOKEN_PATH"), root / "data" / "gmail" / "token.json"),
        database_path=_resolve(root, os.getenv("DATABASE_PATH"), root / "data" / "gmail_bot.db"),
        log_path=_resolve(root, os.getenv("LOG_PATH"), root / "logs" / "assistant.log"),
        knowledge_dir=_resolve(root, os.getenv("KNOWLEDGE_DIR"), root / "data" / "knowledge"),
        gmail_query=os.getenv("GMAIL_QUERY", "is:unread label:INBOX"),
        poll_seconds=max(5, _as_int(os.getenv("GMAIL_POLL_SECONDS"), 60)),
        label_name=os.getenv("GMAIL_LABEL", "GmailBot-Drafted"),
        inbox_llm=(os.getenv("INBOX_LLM", "placeholder") or "placeholder").strip().lower(),
        ollama_host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3"),
        sender_filter=os.getenv("GMAIL_SENDER_FILTER", "").strip(),
        keyword_filter=os.getenv("GMAIL_KEYWORD_FILTER", "").strip(),
        sent_example_limit=_as_int(os.getenv("SENT_EXAMPLE_LIMIT"), 20),
        retrieve_k=_as_int(os.getenv("RETRIEVE_K"), 5),
        assistant_name=os.getenv("ASSISTANT_NAME", "Gmail Bot"),
        gmail_account=normalize_email(os.getenv("GMAIL_ACCOUNT", "")),
        local_api_port=max(1024, _as_int(os.getenv("LOCAL_API_PORT"), 8787)),
    )
    settings.ensure_dirs()
    return settings
