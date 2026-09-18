from __future__ import annotations

from gmail_bot.draft_engine import generate_reply, placeholder_reply
from gmail_bot.llm import PlaceholderLlm
from gmail_bot.models import RetrievedChunk
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
    draft = generate_reply(message, hits, [], PlaceholderLlm())
    assert draft.engine == "placeholder"
    assert "9 AM" in draft.text
    assert draft.language == "en"


def test_hindi_placeholder_without_context() -> None:
    message = make_message(body="कृपया घंटे बताएं", subject="सहायता")
    text = placeholder_reply(message, [], "hi")
    assert "DRAFT" in text
    assert "धन्यवाद" in text


def test_missing_context_flag() -> None:
    message = make_message(body="What is the secret launch date?")
    draft = generate_reply(message, [], [], PlaceholderLlm())
    assert draft.missing_context is True
    assert "follow up" in draft.text.lower()
