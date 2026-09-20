from __future__ import annotations

from gmail_bot.language import detect_language, language_name
from gmail_bot.llm import LlmPort, PlaceholderLlm
from gmail_bot.models import DraftResult, ParsedMessage, RetrievedChunk, StyleExample


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
        f"Example {i}:\nSubject: {ex.subject}\n{ex.body}" for i, ex in enumerate(examples[:8], start=1)
    ) or "(No sent-mail examples yet.)"
    context_block = "\n\n".join(
        f"[{chunk.source} | score={chunk.score:.3f}]\n{chunk.text}" for chunk in chunks
    ) or "(No knowledge chunks retrieved.)"
    return f"""You are {assistant_name} ({owner_email or "the inbox owner"}).
You are the RECIPIENT of the incoming email. Write a first-person reply FROM you TO the incoming sender.
Do not write as the incoming sender. Do not write as a third-party bot, assistant, or someone else.
The draft From address is {owner_email or "the inbox owner"}. The To address is the original sender.
Write a draft reply in {language_name(language)}.
Match the tone, formatting, and language of YOUR sent-mail examples.
Answer every question in the incoming email using ONLY the knowledge context.
If the knowledge context does not contain the answer, say so politely and promise a follow-up.
Sign off as {assistant_name}. Output ONLY the email body. Do not invent prices, dates, or policies.

Sent-mail style examples:
{style_block}

Knowledge context:
{context_block}

Incoming email
From: {message.sender}
Subject: {message.subject}

{message.body}
"""


def placeholder_reply(
    message: ParsedMessage,
    chunks: list[RetrievedChunk],
    language: str,
    *,
    owner_name: str = "",
    owner_email: str = "",
) -> str:
    subject = message.subject or "(No Subject)"
    signoff = owner_name or owner_email or "Me"
    if language == "hi":
        if chunks:
            facts = "\n".join(f"- {chunk.text[:400]}" for chunk in chunks[:3])
            return (
                f"[DRAFT — भेजने से पहले जाँचें]\n\n"
                f"नमस्ते,\n\n"
                f"आपके संदेश \"{subject}\" के लिए धन्यवाद।\n\n"
                f"हमारे ज्ञान आधार से संबंधित जानकारी:\n{facts}\n\n"
                f"कृपया इस ड्राफ्ट को जाँच कर भेजें।\n\n"
                f"धन्यवाद,\n{signoff}"
            )
        return (
            f"[DRAFT — भेजने से पहले जाँचें]\n\n"
            f"नमस्ते,\n\n"
            f"आपके संदेश \"{subject}\" के लिए धन्यवाद। "
            f"हम विवरण की समीक्षा कर रहे हैं और जल्द ही अपडेट देंगे।\n\n"
            f"धन्यवाद,\n{signoff}"
        )
    if chunks:
        facts = "\n".join(f"- {chunk.text[:400]}" for chunk in chunks[:3])
        return (
            f"[DRAFT — review before sending]\n\n"
            f"Hello,\n\n"
            f'Thank you for your message about "{subject}".\n\n'
            f"Relevant information from our knowledge base:\n{facts}\n\n"
            f"Please review this draft and send it manually if it looks correct.\n\n"
            f"Best regards,\n{signoff}"
        )
    return (
        f"[DRAFT — review before sending]\n\n"
        f"Hello,\n\n"
        f'Thank you for reaching out about "{subject}". '
        f"We are reviewing the details and will follow up shortly.\n\n"
        f"Best regards,\n{signoff}"
    )


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
    missing = not chunks
    owner_name = assistant_name
    if isinstance(llm, PlaceholderLlm) or getattr(llm, "name", "") == "placeholder":
        text = placeholder_reply(
            message, chunks, language, owner_name=owner_name, owner_email=owner_email
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
