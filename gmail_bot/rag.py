from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from gmail_bot.models import RetrievedChunk
from gmail_bot.store import Store

TOKEN_RE = re.compile(r"[A-Za-z0-9\u0900-\u097F']+")
CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + size)
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _chunk_id(source: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{index}:{text[:80]}".encode("utf-8")).hexdigest()[:16]
    return f"{digest}-{index}"


class TfidfIndex:
    """Local portable vector index (no model download). Same role as FAISS/Chroma for small KBs."""

    def __init__(self) -> None:
        self.docs: list[tuple[str, str, str]] = []  # id, source, text
        self.doc_tf: list[Counter[str]] = []
        self.idf: dict[str, float] = {}

    def build(self, docs: list[tuple[str, str, str]]) -> None:
        self.docs = docs
        self.doc_tf = [Counter(tokenize(text)) for _, _, text in docs]
        df: Counter[str] = Counter()
        for tf in self.doc_tf:
            df.update(tf.keys())
        n = max(len(docs), 1)
        self.idf = {term: math.log((1 + n) / (1 + count)) + 1.0 for term, count in df.items()}

    def query(self, text: str, k: int = 5) -> list[RetrievedChunk]:
        if not self.docs:
            return []
        qtf = Counter(tokenize(text))
        if not qtf:
            return []
        qvec = {term: tf * self.idf.get(term, 0.0) for term, tf in qtf.items()}
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        scored: list[RetrievedChunk] = []
        for (doc_id, source, body), tf in zip(self.docs, self.doc_tf, strict=True):
            dot = 0.0
            dnorm_sq = 0.0
            for term, freq in tf.items():
                weight = freq * self.idf.get(term, 0.0)
                dnorm_sq += weight * weight
                if term in qvec:
                    dot += weight * qvec[term]
            dnorm = math.sqrt(dnorm_sq) or 1.0
            score = dot / (qnorm * dnorm)
            if score > 0:
                scored.append(RetrievedChunk(source=source, text=body, score=score))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]


def scrape_url(url: str, timeout: int = 20) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {url}")
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "GmailBot/1.0 (+local-draft-assistant)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    body = soup.get_text("\n", strip=True)
    return f"{title}\n\n{body}"


def load_faqs(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: list[tuple[str, str]] = []
    if isinstance(raw, dict) and "faqs" in raw:
        raw = raw["faqs"]
    if isinstance(raw, list):
        for i, entry in enumerate(raw, start=1):
            if isinstance(entry, str):
                items.append((f"faq:{i}", entry))
            elif isinstance(entry, dict):
                question = str(entry.get("question") or entry.get("q") or "").strip()
                answer = str(entry.get("answer") or entry.get("a") or entry.get("text") or "").strip()
                if question and answer:
                    items.append((f"faq:{question[:40]}", f"Q: {question}\nA: {answer}"))
                elif answer:
                    items.append((f"faq:{i}", answer))
    return items


def load_local_documents(folder: Path) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    if not folder.is_dir():
        return docs
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue
        if path.name == "faqs.json":
            continue
        if path.suffix.lower() == ".json":
            continue
        docs.append((f"file:{path.name}", path.read_text(encoding="utf-8", errors="ignore")))
    return docs


class KnowledgeBase:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.index = TfidfIndex()
        self.reload()

    def reload(self) -> None:
        rows = self.store.list_knowledge()
        docs = [(row["id"], row["source"], row["text"]) for row in rows]
        self.index.build(docs)

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        return self.index.query(query, k=k)

    def rebuild_from_sources(
        self,
        *,
        faqs_path: Path,
        knowledge_dir: Path,
        urls: list[str] | None = None,
        extra_notes: str = "",
    ) -> int:
        collected: list[tuple[str, str, str]] = []
        for source, text in load_faqs(faqs_path):
            for i, chunk in enumerate(chunk_text(text)):
                collected.append((_chunk_id(source, i, chunk), source, chunk))
        for source, text in load_local_documents(knowledge_dir):
            for i, chunk in enumerate(chunk_text(text)):
                collected.append((_chunk_id(source, i, chunk), source, chunk))
        if extra_notes.strip():
            for i, chunk in enumerate(chunk_text(extra_notes)):
                collected.append((_chunk_id("notes", i, chunk), "notes", chunk))
        for url in urls or []:
            url = url.strip()
            if not url:
                continue
            page = scrape_url(url)
            source = f"url:{url}"
            for i, chunk in enumerate(chunk_text(page)):
                collected.append((_chunk_id(source, i, chunk), source, chunk))
        self.store.replace_knowledge(collected)
        self.reload()
        return len(collected)
