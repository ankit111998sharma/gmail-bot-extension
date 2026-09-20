from __future__ import annotations

from gmail_bot.draft_engine import (
    correct_grammar,
    generate_reply,
    placeholder_reply,
    strip_quoted_reply,
    topic_hint,
    usable_existing_draft,
)
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
    assert "follow up" in draft.text.lower() or "review this" in draft.text.lower()
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


def test_correct_grammar_and_strip_quoted_thread() -> None:
    quoted = (
        "hello i hope the issue is resolve  please confirm the fee link\n\n"
        "On Mon, 20 Sep 2026 Rachana wrote:\n"
        "> As discussed on call, I hope the issue has been resolved.\n"
    )
    cleaned = strip_quoted_reply(quoted)
    assert "As discussed" not in cleaned
    text = correct_grammar(cleaned)
    assert "I hope" in text
    assert "resolved" in text
    assert "Please confirm" in text


def test_strip_gmail_angle_quote_thread() -> None:
    blob = (
        "Dear Rachana,\n\nPlease reopen the fee payment link.\n\nBest regards,\nAnkit\n\n"
        "> > > Thanks & regards,\n"
        "> Support Team\n"
        ">\n"
        "> On Mon, 14 Sep at 2:39 PM, University Learner <\n"
        "> support@onlinedegree. Freshdesk. Com> wrote:\n"
        "> Please help with the fee link.\n"
    )
    cleaned = strip_quoted_reply(blob)
    assert "Support Team" not in cleaned
    assert "Freshdesk" not in cleaned
    assert "wrote:" not in cleaned.lower()
    assert ">" not in cleaned
    assert "Please reopen the fee payment link." in cleaned

    quoted_only = (
        "> > > Thanks & regards,\n"
        "> Support Team\n"
        ">\n"
        "> On Mon, 14 Sep at 2:39 PM, University Learner <\n"
        "> support@onlinedegree. Freshdesk. Com> wrote:\n"
    )
    assert strip_quoted_reply(quoted_only) == ""
    drafted = generate_reply(
        make_message(
            sender="Support Team <support@onlinedegree.freshdesk.com>",
            subject="Re: Request to Reopen Fee Payment Link",
            body="As discussed on call, I hope the issue has been resolved.",
        ),
        [],
        [],
        PlaceholderLlm(),
        assistant_name="Ankit",
        owner_email="me@gmail.com",
        existing_draft=quoted_only,
    )
    text = drafted.text
    assert ">" not in text
    assert "wrote:" not in text.lower()
    assert "support@onlinedegree" not in text.lower()
    assert "Freshdesk" not in text
    assert "Support Team" not in text
    assert "Best regards" in text


def test_usable_existing_draft_ignores_incoming_and_quoted_thread() -> None:
    incoming = "As discussed on call, I hope the issue has been resolved."
    assert usable_existing_draft(incoming, incoming) == ""
    quoted = "\n\nOn Mon, 20 Sep 2026 Rachana wrote:\n> As discussed on call\n"
    assert usable_existing_draft(quoted, incoming) == ""
    pasted = (
        "Hi Asharma111998,\n\nAs discussed on call, I hope the issue has been resolved.\n\n"
        "Thanks & regards,\nSupport Team"
    )
    assert usable_existing_draft(pasted, incoming) == ""
    own = "hello please confirm the fee payment link"
    assert "please confirm" in usable_existing_draft(own, incoming).lower()
    assert usable_existing_draft(own, "") == "hello please confirm the fee payment link"


def test_redraft_uses_rules_without_copying_incoming() -> None:
    message = make_message(
        sender="Rachana Shrotri <esupport@kuk.ac.in>",
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
    )
    rules = ["Students must pay the semester fee before the notified deadline."]
    draft = generate_reply(
        message,
        [],
        [],
        PlaceholderLlm(),
        assistant_name="asharma111998",
        owner_email="me@gmail.com",
        existing_draft="hello i need the fee link please reopen it",
        rules=rules,
    )
    assert draft.engine == "redraft"
    assert "I " in draft.text or "Dear" in draft.text or draft.text.startswith("Hello")
    assert "as discussed on call" not in draft.text.lower()
    assert "deadline" in " ".join(draft.suggestions).lower() or "deadline" in draft.text.lower()
    assert "asharma111998" in draft.text or "Me" in draft.text or "I need" in draft.text


def test_placeholder_uses_optional_notes_and_skips_when_blank() -> None:
    message = make_message(
        sender="Rachana <esupport@kuk.ac.in>",
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
    )
    with_notes = placeholder_reply(
        message, [], "en", owner_name="asharma111998", notes="Please ask them to reopen the fee payment link."
    )
    without = placeholder_reply(message, [], "en", owner_name="asharma111998")
    assert "fee payment link" in with_notes.lower()
    assert "as discussed on call" not in with_notes.lower()
    assert "Please ask them to reopen" not in without


class _FakeGemini:
    name = "gemini"

    def generate(self, prompt: str) -> str:
        assert "reopen the fee payment link" in prompt.lower()
        return "Hello,\n\nPlease reopen the fee payment link.\n\nBest regards,\nme"


def test_ai_llm_uses_notes_when_provided() -> None:
    message = make_message(
        sender="Rachana <esupport@kuk.ac.in>",
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
    )
    draft = generate_reply(
        message,
        [],
        [],
        _FakeGemini(),
        assistant_name="me",
        owner_email="me@gmail.com",
        notes="Ask them to reopen the fee payment link",
    )
    assert draft.engine == "gemini"
    assert "reopen the fee payment link" in draft.text.lower()
    assert "as discussed on call" not in draft.text.lower()


def test_professional_greeting_uses_addressee_name() -> None:
    message = make_message(sender="Ada Lovelace <ada@example.com>")
    text = placeholder_reply(message, [], "en", owner_name="asharma111998")
    assert text.startswith("Dear Ada,")
    assert "Thank you for your email" in text
    assert "Best regards" in text
