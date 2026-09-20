from __future__ import annotations

import re

from gmail_bot.language import detect_language, language_name
from gmail_bot.llm import LlmPort, PlaceholderLlm
from gmail_bot.models import DraftResult, ParsedMessage, RetrievedChunk, StyleExample, short_snippet

_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.I)
_STOP = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "of",
    "on",
    "regarding",
    "request",
    "the",
    "to",
    "about",
}


def topic_hint(subject: str | None, limit: int = 4) -> str:
    text = _PREFIX.sub("", subject or "").strip()
    words = [w for w in re.findall(r"[A-Za-z0-9\u0900-\u097F]+", text) if w.lower() not in _STOP]
    return " ".join(words[:limit])


def _is_meta_chunk(chunk: RetrievedChunk) -> bool:
    source = (chunk.source or "").lower()
    text = (chunk.text or "").lower()
    if "guideline" in source:
        return True
    return "never invent" in text or "drafts are for human review" in text or "how do i stop the bot" in text


def _faq_answer(chunk: RetrievedChunk) -> str:
    text = (chunk.text or "").strip()
    if re.search(r"\bA:", text):
        return text.split("A:", 1)[1].strip().split("\n")[0].strip()
    if "answer:" in text.lower():
        return re.split(r"answer:\s*", text, maxsplit=1, flags=re.I)[1].strip().split("\n")[0].strip()
    return ""


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def relevant_sent_line(examples: list[StyleExample], hint: str, incoming: str = "") -> str:
    """Pick one sentence from the owner's earlier sent mail on this topic."""
    incoming_n = _norm(incoming)
    words = [w.lower() for w in hint.split() if len(w) > 2]
    ranked: list[tuple[int, str]] = []
    for example in examples:
        blob = f"{example.subject}\n{example.body}"
        score = sum(1 for w in words if w in blob.lower()) if words else 1
        if score <= 0:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join((example.body or "").split())):
            line = sentence.strip()
            if len(line) < 24:
                continue
            lowered = line.lower()
            if lowered.startswith(("hi ", "hello", "dear ", "thanks", "thank you", "best ", "regards")):
                continue
            compact = _norm(line)
            if incoming_n and (compact in incoming_n or incoming_n in compact):
                continue
            if words and not any(word in lowered for word in words):
                continue
            ranked.append((score, line))
            break
    if not ranked:
        return ""
    ranked.sort(key=lambda row: -row[0])
    return short_snippet(ranked[0][1], 180)


def useful_answers(message: ParsedMessage, chunks: list[RetrievedChunk]) -> list[str]:
    blob = f"{message.subject}\n{message.body}".lower()
    answers: list[str] = []
    for chunk in chunks:
        if _is_meta_chunk(chunk):
            continue
        answer = _faq_answer(chunk)
        if not answer:
            continue
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9\u0900-\u097F]{3,}", answer)]
        if tokens and not any(token in blob for token in tokens[:8]):
            question = (chunk.text or "").split("A:")[0].lower()
            q_tokens = [t for t in re.findall(r"[A-Za-z0-9\u0900-\u097F]{4,}", question) if t not in _STOP]
            if q_tokens and not any(token in blob for token in q_tokens[:6]):
                continue
        answers.append(answer)
    return answers[:1]


def build_prompt(
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    examples: list[StyleExample],
    *,
    assistant_name: str,
    language: str,
    owner_email: str = "",
) -> str:
    style_block = "\n\n".join(
        f"Your earlier email {i}:\nSubject: {ex.subject}\n{ex.body}" for i, ex in enumerate(examples[:8], start=1)
    ) or "(No related sent emails yet.)"
    answers = useful_answers(message, chunks)
    context_block = "\n".join(answers) or "(No matching FAQ answer.)"
    hint = topic_hint(message.subject) or "this"
    gist = short_snippet(message.body, 70)
    return f"""You are {assistant_name} ({owner_email or "the inbox owner"}).
Write a short first-person reply FROM you TO the other person. You are not the incoming sender.
Rules:
- 2 to 4 short sentences. No subject line.
- Write as yourself. Never write on behalf of {message.sender or "the sender"}.
- Do not quote, paste, or repeat the incoming email.
- You may mention the topic in a few words, such as: {hint}
- Reuse facts and tone from YOUR earlier sent emails below. Those are messages you already wrote.
- Do not paste knowledge-base text, guidelines, FAQs, or disclaimers.
- If a matching fact is provided below, use it in your own words. Otherwise follow up from your earlier emails.
- Do not write [DRAFT], "review before sending", or "knowledge base".
- Sign off as {assistant_name} only.

Incoming gist (do not quote): {gist}

Your earlier sent emails on this topic:
{style_block}

Matching fact (do not paste verbatim unless it is a short answer):
{context_block}

Write the reply in {language_name(language)}.
"""


def placeholder_reply(
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    language: str,
    *,
    owner_name: str = "",
    owner_email: str = "",
    examples: list[StyleExample] | None = None,
) -> str:
    signoff = owner_name or owner_email or "Me"
    hint = topic_hint(message.subject)
    answers = useful_answers(message, chunks)
    sent_line = relevant_sent_line(examples or [], hint, message.body)
    if language == "hi":
        if answers:
            return f"नमस्ते,\n\n{answers[0]}\n\nधन्यवाद,\n{signoff}"
        if sent_line:
            return f"नमस्ते,\n\nआपके संदेश के लिए धन्यवाद। {sent_line}\n\nधन्यवाद,\n{signoff}"
        if hint:
            return (
                f"नमस्ते,\n\n"
                f"{hint} के बारे में आपका संदेश मिल गया है। मैं जाँच कर जल्द उत्तर दूँगा।\n\n"
                f"धन्यवाद,\n{signoff}"
            )
        return f"नमस्ते,\n\nआपका संदेश मिल गया है। मैं जाँच कर जल्द उत्तर दूँगा।\n\nधन्यवाद,\n{signoff}"
    if answers:
        return f"Hello,\n\n{answers[0]}\n\nBest regards,\n{signoff}"
    if sent_line:
        return f"Hello,\n\nThank you for the update. {sent_line}\n\nBest regards,\n{signoff}"
    if hint:
        return (
            f"Hello,\n\n"
            f"I have noted your message about {hint}. I will check this and get back to you.\n\n"
            f"Best regards,\n{signoff}"
        )
    return f"Hello,\n\nI have received your message. I will check this and get back to you.\n\nBest regards,\n{signoff}"


def generate_reply(
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    examples: list[StyleExample],
    llm: LlmPort,
    *,
    assistant_name: str = "Gmail Bot",
    owner_email: str = "",
) -> DraftResult:
    language = detect_language(message.body or message.subject)
    answers = useful_answers(message, chunks)
    missing = not answers
    owner_name = assistant_name
    if isinstance(llm, PlaceholderLlm) or getattr(llm, "name", "") == "placeholder":
        text = placeholder_reply(
            message,
            chunks,
            language,
            owner_name=owner_name,
            owner_email=owner_email,
            examples=examples,
        )
        return DraftResult(
            text=text,
            engine="placeholder",
            language=language,
            used_chunks=chunks,
            missing_context=missing,
        )
    prompt = build_prompt(
        message,
        chunks,
        examples,
        assistant_name=assistant_name,
        language=language,
        owner_email=owner_email,
    )
    text = llm.generate(prompt)
    return DraftResult(
        text=text.strip(),
        engine=getattr(llm, "name", "llm"),
        language=language,
        used_chunks=chunks,
        missing_context=missing,
    )
