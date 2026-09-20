from gmail_bot.bot import InboxBot
from gmail_bot.config import Settings
from gmail_bot.local_api import start_local_api
from gmail_bot.models import StyleExample
from tests.conftest import FakeGmail, make_message


def test_click_drafts_only_matching_open_email(settings: Settings, store) -> None:
    other = make_message(message_id="m2", thread_id="t2", sender="Bob <bob@example.com>", subject="Invoice")
    target = make_message()
    gmail = FakeGmail([other, target])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    assert result["message_id"] == "m1"
    assert "draftText" in result
    assert "DRAFT" not in result["draftText"]
    assert "knowledge base" not in result["draftText"].lower()
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["thread_id"] == "t1"
    assert gmail.drafts[0]["from"] == "me@gmail.com"
    assert "ada@example.com" in gmail.drafts[0]["to"].lower()
    assert gmail.send_called is False


def test_click_draft_requires_a_matching_message(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    try:
        bot.draft_from_open_mail("nobody@example.com", "Hello")
        raise AssertionError("expected missing-email error")
    except RuntimeError as exc:
        assert "find this email" in str(exc).lower()
    assert gmail.drafts == []


def test_local_api_draft_open_endpoint(settings: Settings, store) -> None:
    import http.client
    import json

    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    server = start_local_api(port=0, bot=bot)
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        payload = json.dumps({"sender": "ada@example.com", "subject": "Office hours?", "body": "hours?"})
        conn.request("POST", "/api/draft-open", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 200
        assert data["ok"] is True
        assert "draftText" in data
        assert "suggestions" in data
        assert "DRAFT" not in data["draftText"]
        assert "knowledge base" not in data["draftText"].lower()
        assert len(gmail.drafts) == 1
        assert gmail.send_called is False
    finally:
        server.shutdown()


def test_click_drafts_as_owner_using_related_sent_mail(settings: Settings, store) -> None:
    mine = make_message(
        message_id="m-me",
        thread_id="t-fee",
        sender="Me <me@gmail.com>",
        to_header="Rachana Shrotri <esupport@kuk.ac.in>",
        subject="Request to Reopen Fee Payment Link for Semester-III Internship Backlog",
        body="Please reopen the fee payment link for the internship backlog.",
        message_id_header="<me@mail>",
    )
    theirs = make_message(
        message_id="m-them",
        thread_id="t-fee",
        sender="Rachana Shrotri <esupport@kuk.ac.in>",
        to_header="Me <me@gmail.com>",
        subject="Re: Request to Reopen Fee Payment Link for Semester-III Internship Backlog",
        body="As discussed on call, I hope the issue has been resolved.",
        message_id_header="<them@kuk>",
    )
    gmail = FakeGmail([mine, theirs], profile_email="me@gmail.com")
    gmail.sent_examples = [
        StyleExample(
            subject=mine.subject,
            body="Please reopen the fee payment link for the internship backlog.",
        ),
        StyleExample(
            subject="Internship attendance",
            body="Please mark my internship attendance for this week.",
        ),
    ]
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    store.set_setting("connected_email", "me@gmail.com")
    result = bot.draft_from_open_mail("Me <me@gmail.com>", mine.subject, mine.body)
    assert result["message_id"] == "m-them"
    assert gmail.drafts[0]["from"] == "me@gmail.com"
    assert "esupport@kuk.ac.in" in gmail.drafts[0]["to"].lower()
    assert "me@gmail.com" not in gmail.drafts[0]["to"].lower()
    text = result["draftText"].lower()
    assert "as discussed on call" not in text
    assert "issue has been resolved" not in text
    assert "fee payment" in text
    assert "me" in result["draftText"].split("Best regards")[-1].lower()


def test_click_redrafts_existing_draft_with_rules_url(settings: Settings, store, monkeypatch) -> None:
    monkeypatch.setattr(
        "gmail_bot.bot.fetch_page_rules",
        lambda url, topic="": ["Students must pay the semester fee before the notified deadline."],
    )
    message = make_message(
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
        sender="Rachana <esupport@kuk.ac.in>",
    )
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail(
        "Rachana <esupport@kuk.ac.in>",
        "Re: Request to Reopen Fee Payment Link",
        "As discussed on call, I hope the issue has been resolved.",
        existing_draft="hello i hope the issue is resolve  please confirm",
        rules_url="https://kuk.ac.in/fee-rules",
    )
    text = result["draftText"]
    assert "I hope" in text
    assert "resolved" in text.lower()
    assert "as discussed on call" not in text.lower()
    assert result["suggestions"]
    assert "deadline" in " ".join(result["suggestions"]).lower() or "deadline" in text.lower()
    assert gmail.drafts[0]["from"] == "me@gmail.com"
    assert gmail.send_called is False
    assert bot.store.get_setting("rules_url") == "https://kuk.ac.in/fee-rules"


def test_click_replaces_previous_bot_draft(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    result = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["id"] == result["draft_id"]
    assert gmail.send_called is False
