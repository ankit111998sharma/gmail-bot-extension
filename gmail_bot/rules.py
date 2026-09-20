from __future__ import annotations

import re
from urllib.parse import urlparse

from gmail_bot.models import short_snippet
from gmail_bot.rag import scrape_url

_RULE_HINTS = re.compile(
    r"\b(rule|rules|regulation|regulations|policy|guideline|shall|must|required|"
    r"deadline|fee|eligibility|not permitted|not allowed|allowed|mandatory|"
    r"backlog|internship|payment|reopen|link)\b",
    re.I,
)
_TOPIC_STOP = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "of",
    "on",
    "the",
    "to",
    "about",
    "request",
    "regarding",
}
_BULLET = re.compile(r"^(\d+[\).]|[a-z][\).]|[-*•])\s+", re.I)


def normalize_rules_url(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    raise ValueError("Enter a full http(s) URL for the rules or regulations page.")


def extract_rules(page_text: str, topic: str = "", limit: int = 6) -> list[str]:
    """Pull short rule-like lines from a scraped page, biased to the email topic."""
    topic_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9\u0900-\u097F]+", topic or "")
        if len(w) > 2 and w.lower() not in _TOPIC_STOP
    ]
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw in re.split(r"[\n\r]+", page_text or ""):
        line = _BULLET.sub("", " ".join(raw.split())).strip(" -*\t")
        if len(line) < 24 or len(line) > 280:
            continue
        key = line.lower()
        if key in seen:
            continue
        score = 0
        if _RULE_HINTS.search(line):
            score += 2
        if _BULLET.match(raw.strip()):
            score += 1
        if topic_words:
            score += sum(1 for word in topic_words if word in key)
        if score < 2:
            continue
        seen.add(key)
        ranked.append((score, line.rstrip(" .") + "."))
    ranked.sort(key=lambda row: -row[0])
    return [line for _, line in ranked[:limit]]


def fetch_page_rules(url: str, topic: str = "") -> list[str]:
    page = scrape_url(normalize_rules_url(url))
    return extract_rules(page, topic)


def rule_suggestions(rules: list[str], topic: str = "") -> list[str]:
    suggestions = [short_snippet(rule, 180) for rule in rules[:5] if rule.strip()]
    if suggestions:
        return suggestions
    if topic.strip():
        return [f"No clear rules were found for {topic.strip()}. Check the page and try another URL."]
    return []
