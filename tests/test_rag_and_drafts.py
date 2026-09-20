from __future__ import annotations

from gmail_bot.draft_engine import generate_reply, placeholder_reply, topic_hint
from gmail_bot.llm import PlaceholderLlm
from gmail_bot.models import RetrievedChunk, StyleExample
from gmail_bot.rag import KnowledgeBase, chunk_text, tokenize
from tests.conftest import make_message


def test_chunk_and_tokenize() -> None:
    text = "alpha " * 400
    chunks = chunk_text(text, size=80, overlap=10)
    assert len(chunks) > 1
    assert "alpha" in tokenize(text)


def test_faq_retrieval_and_placeholder_uses_answer(store, settings) -> None:
    faqs = settings.faqs_path
    faqs.write_text(
        '{"faqs":[{"question":"What are your support hours?","answer":"Support hours are 9 AM to 6 PM IST."}]}',
        encoding="utf-8",
    )
    kb = KnowledgeBase(store)
    count = kb.rebuild_from_sources(faqs_path=faqs, knowledge_dir=settings.knowledge_dir)
    assert count >= 1
    hits = kb.retrieve("what are your support hours?", k=3)
    assert hits
    assert "9 AM" in hits[0].text

    message = make_message()
    draft = generate_reply(
        message, hits, [], PlaceholderLlm(), assistant_name="asharma", owner_email="me@gmail.com"
    )
    assert draft.engine == "placeholder"
    assert "9 AM" in draft.text
    assert draft.language == "en"
    assert "asharma" in draft.text
    assert "Ada" not in draft.text.split("Best regards")[-1]
    assert "DRAFT" not in draft.text
    assert "knowledge base" not in draft.text.lower()
    assert "Office hours?" not in draft.text


def test_hindi_placeholder_without_context() -> None:
    message = make_message(body="कृपया घंटे बताएं", subject="सहायता")
    text = placeholder_reply(message, [], "hi")
    assert "नमस्ते" in text
    assert "धन्यवाद" in text
    assert "DRAFT" not in text


def test_missing_context_flag() -> None:
    message = make_message(body="What is the secret launch date?")
    draft = generate_reply(message, [], [], PlaceholderLlm())
    assert draft.missing_context is True
    assert "get back to you" in draft.text.lower()
    assert "secret launch date" not in draft.text.lower()
    assert "DRAFT" not in draft.text


def test_placeholder_does_not_quote_sender_or_guidelines() -> None:
    message = make_message(
        subject="Re: Request to Reopen Fee Payment Link for Semester-III Internship Backlog",
        body="Please reopen the fee payment link for my internship backlog.",
    )
    guidelines = RetrievedChunk(
        source="guidelines.md",
        text="# Reply guidelines\n- Never invent prices\n- Drafts are for human review.",
        score=1.0,
    )
    stop_faq = RetrievedChunk(
        source="faqs.json",
        text="Q: How do I stop the bot?\nA: Use Stop bot in the dashboard.",
        score=0.9,
    )
    text = placeholder_reply(message, [guidelines, stop_faq], "en", owner_name="asharma111998")
    assert "DRAFT" not in text
    assert "knowledge base" not in text.lower()
    assert "Semester-III" not in text
    assert "Please reopen the fee payment" not in text
    assert "Stop bot" not in text
    assert "Never invent" not in text
    assert "Fee Payment" in text
    assert topic_hint(message.subject) == "Reopen Fee Payment Link"


def test_placeholder_uses_related_sent_mail_not_incoming_wording() -> None:
    message = make_message(
        sender="Rachana Shrotri <esupport@kuk.ac.in>",
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
    )
    examples = [
        StyleExample(
            subject="Request to Reopen Fee Payment Link",
            body="Please reopen the fee payment link for the internship backlog.",
        )
    ]
    text = placeholder_reply(
        message, [], "en", owner_name="asharma111998", examples=examples
    )
    assert "As discussed on call" not in text
    assert "issue has been resolved" not in text
    assert "fee payment" in text.lower()
    assert "asharma111998" in text.split("Best regards")[-1]
