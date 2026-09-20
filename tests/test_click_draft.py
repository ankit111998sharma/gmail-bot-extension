from gmail_bot.bot import InboxBot
from gmail_bot.config import Settings
from gmail_bot.local_api import start_local_api
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
        assert "DRAFT" not in data["draftText"]
        assert "knowledge base" not in data["draftText"].lower()
        assert len(gmail.drafts) == 1
        assert gmail.send_called is False
    finally:
        server.shutdown()


def test_click_replaces_previous_bot_draft(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    result = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["id"] == result["draft_id"]
    assert gmail.send_called is False
