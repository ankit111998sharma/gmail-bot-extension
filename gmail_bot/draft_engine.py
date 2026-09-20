from __future__ import annotations

import re

from gmail_bot.language import detect_language, language_name
from gmail_bot.llm import LlmPort, PlaceholderLlm
from gmail_bot.models import DraftResult, ParsedMessage, RetrievedChunk, StyleExample, short_snippet
from gmail_bot.rules import rule_suggestions

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


_COMMON = (
    (re.compile(r"\bteh\b", re.I), "the"),
    (re.compile(r"\badress\b", re.I), "address"),
    (re.compile(r"\brecieve\b", re.I), "receive"),
    (re.compile(r"\bseperate\b", re.I), "separate"),
    (re.compile(r"\bocured\b", re.I), "occurred"),
    (re.compile(r"\bdefinately\b", re.I), "definitely"),
    (re.compile(r"\bis resolve\b", re.I), "is resolved"),
    (re.compile(r"\bhas been resolve\b", re.I), "has been resolved"),
    (re.compile(r"\bdont\b", re.I), "don't"),
    (re.compile(r"\bcant\b", re.I), "can't"),
    (re.compile(r"\bwont\b", re.I), "won't"),
    (re.compile(r"\bim\b", re.I), "I'm"),
    (re.compile(r"\bive\b", re.I), "I've"),
    (re.compile(r"\bi\b"), "I"),
)
_SIGNOFF = re.compile(
    r"\n+(Best regards|Thanks & regards|Thanks|Thank you|Regards|Sincerely|धन्यवाद),?\s*\n",
    re.I,
)


def strip_quoted_reply(text: str) -> str:
    """Keep the user's draft body, not the quoted thread underneath."""
    kept: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if re.match(r"^On .+wrote:\s*$", stripped, re.I):
            break
        if stripped.startswith(">"):
            continue
        if stripped == "--":
            break
        kept.append(line)
    return "\n".join(kept).strip()


def correct_grammar(text: str) -> str:
    """Fix common English grammar and punctuation without changing meaning."""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return ""
    if detect_language(raw) != "en":
        return re.sub(r"[ \t]+", " ", raw).strip()
    parts = re.split(r"\n{2,}", raw)
    fixed: list[str] = []
    for part in parts:
        line = re.sub(r"[ \t]+", " ", part).strip()
        line = re.sub(r"\s+([,.!?;:])", r"\1", line)
        for pattern, replacement in _COMMON:
            line = pattern.sub(replacement, line)
        line = re.sub(r"([a-z])\s+(please|kindly|however|also)\b", r"\1. \2", line, flags=re.I)
        line = re.sub(r"([.!?])([A-Za-z])", r"\1 \2", line)
        sentences = re.split(r"(?<=[.!?])\s+", line)
        capped: list[str] = []
        for sentence in sentences:
            piece = sentence.strip()
            if not piece:
                continue
            capped.append(piece[0].upper() + piece[1:] if piece[0].islower() else piece)
        line = " ".join(capped)
        if line and line[-1] not in ".!?:" and not line.lower().startswith(("best regards", "thanks", "regards")):
            if len(line.split()) > 3:
                line += "."
        fixed.append(line)
    return "\n\n".join(fixed).strip()


def polite_name(sender: str | None) -> str:
    raw = (sender or "").split("<")[0].strip().strip('"')
    if not raw or "@" in raw:
        return ""
    skip = {"mr", "mrs", "ms", "miss", "dr", "sir", "madam"}
    parts = [part for part in re.split(r"\s+", raw) if part and part.lower().strip(".") not in skip]
    if not parts:
        return ""
    name = parts[0]
    if name.isupper() and len(name) > 1:
        name = name.title()
    if re.fullmatch(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.'-]{0,30}", name):
        return name
    return ""


def greeting_line(sender: str | None, language: str) -> str:
    name = polite_name(sender)
    if language == "hi":
        return f"नमस्ते {name}," if name else "नमस्ते,"
    return f"Dear {name}," if name else "Hello,"


def _strip_letter_shell(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    first = lines[0].strip() if lines else ""
    if first and re.match(r"^(hi|hello|hey|dear|नमस्ते)\b([^.]{0,40}),?\s*$", first, re.I):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    body = "\n".join(lines).strip()
    match = _SIGNOFF.search("\n" + body + "\n")
    if match:
        body = ("\n" + body + "\n")[: match.start()].strip()
    return body


def polish_professional(text: str, *, owner_name: str, sender: str = "", language: str = "en") -> str:
    """Keep the meaning, but present the draft as a short professional email."""
    cleaned = correct_grammar(strip_quoted_reply(text))
    body = _strip_letter_shell(cleaned)
    body = re.sub(r"^(hi|hello|hey)[.!]\s+", "", body, flags=re.I).strip()
    if not body:
        return cleaned
    signoff = owner_name or "Me"
    if language == "hi":
        return f"{greeting_line(sender, language)}\n\n{body}\n\nधन्यवाद,\n{signoff}"
    if not re.match(r"^(thank you|thanks|i |we |please |kindly )", body, re.I):
        if not body.lower().startswith("thank"):
            body = body[0].upper() + body[1:] if body else body
    return f"{greeting_line(sender, language)}\n\n{body}\n\nBest regards,\n{signoff}"


def relevant_rule_line(rules: list[str], hint: str) -> str:
    words = [w.lower() for w in hint.split() if len(w) > 2]
    ranked: list[tuple[int, str]] = []
    for rule in rules:
        score = sum(1 for word in words if word in rule.lower()) if words else 1
        if score:
            ranked.append((score, rule))
    if not ranked:
        return short_snippet(rules[0], 160) if rules else ""
    ranked.sort(key=lambda row: -row[0])
    return short_snippet(ranked[0][1], 160)


def _insert_before_signoff(text: str, sentence: str) -> str:
    extra = sentence.strip()
    if not extra:
        return text
    match = _SIGNOFF.search(text)
    if match:
        return text[: match.start()].rstrip() + "\n\n" + extra + text[match.start() :]
    return text.rstrip() + "\n\n" + extra


def weave_notes(text: str, notes: str) -> str:
    cleaned = short_snippet(" ".join((notes or "").split()), 220)
    if not cleaned:
        return text
    if _norm(cleaned) in _norm(text):
        return text
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return _insert_before_signoff(text, cleaned)


def weave_rule_sentence(text: str, rules: list[str], hint: str) -> str:
    rule = relevant_rule_line(rules, hint)
    if not rule:
        return text
    if _norm(rule) in _norm(text):
        return text
    return _insert_before_signoff(text, f"Please confirm this follows the published rules: {rule}")


def redraft_existing(
    existing: str,
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    examples: list[StyleExample],
    rules: list[str],
    *,
    owner_name: str,
    owner_email: str,
    language: str,
    notes: str = "",
) -> str:
    cleaned = correct_grammar(strip_quoted_reply(existing))
    if len(cleaned) < 12:
        cleaned = placeholder_reply(
            message,
            chunks,
            language,
            owner_name=owner_name,
            owner_email=owner_email,
            examples=examples,
            rules=rules,
            notes=notes,
        )
    else:
        cleaned = weave_notes(cleaned, notes)
        cleaned = weave_rule_sentence(cleaned, rules, topic_hint(message.subject))
    return polish_professional(
        cleaned, owner_name=owner_name, sender=message.sender, language=language
    )


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
    existing_draft: str = "",
    rules: list[str] | None = None,
    notes: str = "",
    standalone: bool = False,
) -> str:
    style_block = "\n\n".join(
        f"Your earlier email {i}:\nSubject: {ex.subject}\n{ex.body}" for i, ex in enumerate(examples[:8], start=1)
    ) or "(No related sent emails yet.)"
    answers = useful_answers(message, chunks)
    context_block = "\n".join(answers) or "(No matching FAQ answer.)"
    hint = topic_hint(message.subject) or "this"
    gist = short_snippet(message.body, 70)
    rules_block = "\n".join(f"- {rule}" for rule in (rules or [])[:6]) or "(No website page provided.)"
    notes_block = short_snippet(notes, 400) or "(No extra description provided.)"
    existing_block = short_snippet(strip_quoted_reply(existing_draft), 400) or "(No existing draft.)"
    if standalone:
        task = (
            "Write a short first-person email FROM you TO the other person. "
            "This is a new Gmail draft, not a reply to an incoming message."
        )
        incoming_line = f"Recipient and topic (do not quote): {message.sender or 'the recipient'}; {gist}"
    else:
        task = "Write a short first-person reply FROM you TO the other person. You are not the incoming sender."
        incoming_line = f"Incoming gist (do not quote): {gist}"
    return f"""You are {assistant_name} ({owner_email or "the inbox owner"}).
{task}
Rules:
- Write a professional business email. Polite, complete sentences, no slang.
- 2 to 4 short sentences. No subject line.
- Start with Dear <name> when you know their name, otherwise Hello.
- If an existing draft is provided, improve that draft. Keep the same request. Fix grammar and tone.
- Write as yourself. Never write on behalf of {message.sender or "the sender"}.
- Do not quote, paste, or repeat the other person's words.
- You may mention the topic in a few words, such as: {hint}
- Reuse facts and tone from YOUR earlier sent emails below. Those are messages you already wrote.
- If a website summary is provided, use it for facts. Do not paste the whole page.
- If a description is provided, follow it. Correct grammar.
- Do not paste knowledge-base text, guidelines, FAQs, or disclaimers.
- Do not write [DRAFT], "review before sending", or "knowledge base".
- Sign off with Best regards and {assistant_name} only.

{incoming_line}

Existing draft to improve:
{existing_block}

My optional description for this email:
{notes_block}

Website facts:
{rules_block}

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
    rules: list[str] | None = None,
    notes: str = "",
    standalone: bool = False,
) -> str:
    signoff = owner_name or owner_email or "Me"
    hint = topic_hint(message.subject)
    answers = useful_answers(message, chunks)
    sent_line = relevant_sent_line(examples or [], hint, message.body)
    note_line = short_snippet(" ".join((notes or "").split()), 220)
    greet = greeting_line(message.sender, language)
    thanks = "" if standalone else "Thank you for your email. "
    if language == "hi":
        if note_line:
            text = f"{greet}\n\n{note_line}\n\nधन्यवाद,\n{signoff}"
        elif answers:
            text = f"{greet}\n\n{answers[0]}\n\nधन्यवाद,\n{signoff}"
        elif sent_line:
            lead = "" if standalone else "आपके ईमेल के लिए धन्यवाद। "
            text = f"{greet}\n\n{lead}{sent_line}\n\nधन्यवाद,\n{signoff}"
        elif hint:
            if standalone:
                body = f"{hint} के संबंध में यह ईमेल लिख रहा/रही हूँ।"
            else:
                body = f"{hint} के संबंध में आपका ईमेल प्राप्त हुआ। मैं इसकी जाँच कर शीघ्र उत्तर दूँगा।"
            text = f"{greet}\n\n{body}\n\nधन्यवाद,\n{signoff}"
        else:
            body = "यह ईमेल लिख रहा/रही हूँ।" if standalone else "आपका ईमेल प्राप्त हुआ। मैं इसकी जाँच कर शीघ्र उत्तर दूँगा।"
            text = f"{greet}\n\n{body}\n\nधन्यवाद,\n{signoff}"
        return weave_rule_sentence(text, rules or [], hint)
    if note_line:
        text = f"{greet}\n\n{note_line}\n\nBest regards,\n{signoff}"
    elif answers:
        text = f"{greet}\n\n{thanks}{answers[0]}\n\nBest regards,\n{signoff}"
    elif sent_line:
        text = f"{greet}\n\n{thanks}{sent_line}\n\nBest regards,\n{signoff}"
    elif hint:
        if standalone:
            body = f"I am writing regarding {hint}."
        else:
            body = f"Thank you for your email regarding {hint}. I will review this and follow up with you shortly."
        text = f"{greet}\n\n{body}\n\nBest regards,\n{signoff}"
    else:
        if standalone:
            body = "I wanted to follow up with you on this."
        else:
            body = "Thank you for your email. I will review this and follow up with you shortly."
        text = f"{greet}\n\n{body}\n\nBest regards,\n{signoff}"
    return weave_rule_sentence(text, rules or [], hint)


def generate_reply(
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    examples: list[StyleExample],
    llm: LlmPort,
    *,
    assistant_name: str = "Gmail Bot",
    owner_email: str = "",
    existing_draft: str = "",
    rules: list[str] | None = None,
    notes: str = "",
    standalone: bool = False,
) -> DraftResult:
    rules = rules or []
    notes = (notes or "").strip()
    language = detect_language(existing_draft or notes or message.body or message.subject)
    answers = useful_answers(message, chunks)
    missing = not answers and not rules and not notes
    owner_name = assistant_name
    suggestions = rule_suggestions(rules, topic_hint(message.subject))
    is_placeholder = isinstance(llm, PlaceholderLlm) or getattr(llm, "name", "") == "placeholder"
    if not is_placeholder:
        prompt = build_prompt(
            message,
            chunks,
            examples,
            assistant_name=assistant_name,
            language=language,
            owner_email=owner_email,
            existing_draft=existing_draft,
            rules=rules,
            notes=notes,
            standalone=standalone,
        )
        try:
            text = correct_grammar(llm.generate(prompt).strip())
            text = polish_professional(
                text, owner_name=owner_name, sender=message.sender, language=language
            )
            return DraftResult(
                text=text.strip(),
                engine=getattr(llm, "name", "llm"),
                language=language,
                used_chunks=chunks,
                missing_context=missing,
                suggestions=suggestions,
            )
        except Exception:  # noqa: BLE001
            is_placeholder = True
    if existing_draft.strip():
        text = redraft_existing(
            existing_draft,
            message,
            chunks,
            examples,
            rules,
            owner_name=owner_name,
            owner_email=owner_email,
            language=language,
            notes=notes,
        )
        engine = "redraft"
    else:
        text = correct_grammar(
            placeholder_reply(
                message,
                chunks,
                language,
                owner_name=owner_name,
                owner_email=owner_email,
                examples=examples,
                rules=rules,
                notes=notes,
                standalone=standalone,
            )
        )
        engine = "placeholder"
    text = polish_professional(text, owner_name=owner_name, sender=message.sender, language=language)
    return DraftResult(
        text=text.strip(),
        engine=engine,
        language=language,
        used_chunks=chunks,
        missing_context=missing,
        suggestions=suggestions,
    )
