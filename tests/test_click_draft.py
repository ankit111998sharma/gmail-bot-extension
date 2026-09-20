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

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/health")
        health = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        assert health["ok"] is True
        assert "ai_ready" in health
    finally:
        server.shutdown()


def test_local_api_optional_notes_without_url(settings: Settings, store, monkeypatch) -> None:
    import http.client
    import json

    def boom(*_args, **_kwargs):
        raise AssertionError("blank URL should not fetch a website")

    monkeypatch.setattr("gmail_bot.bot.fetch_page_rules", boom)
    message = make_message(
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
        sender="Rachana <esupport@kuk.ac.in>",
    )
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    server = start_local_api(port=0, bot=bot)
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        payload = json.dumps(
            {
                "sender": "Rachana <esupport@kuk.ac.in>",
                "subject": "Re: Request to Reopen Fee Payment Link",
                "body": "As discussed on call, I hope the issue has been resolved.",
                "websiteUrl": "",
                "notes": "Please ask them to reopen the fee payment link.",
            }
        )
        conn.request("POST", "/api/draft-open", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 200
        assert data["ok"] is True
        text = data["draftText"].lower()
        assert "fee payment" in text
        assert "as discussed on call" not in text
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
    assert "Dear Rachana" in text
    assert "I hope" in text or "Please confirm" in text
    assert "resolved" in text.lower() or "deadline" in text.lower()
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


def test_blank_url_and_notes_bypass_internet(settings: Settings, store, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("blank URL should not fetch a website")

    monkeypatch.setattr("gmail_bot.bot.fetch_page_rules", boom)
    store.set_setting("rules_url", "https://kuk.ac.in/fee-rules")
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    assert "draftText" in result
    assert gmail.send_called is False


def test_optional_notes_shape_the_draft(settings: Settings, store, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("notes-only draft should not fetch a website")

    monkeypatch.setattr("gmail_bot.bot.fetch_page_rules", boom)
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
        notes="Please ask them to reopen the fee payment link.",
    )
    text = result["draftText"].lower()
    assert "fee payment" in text
    assert "as discussed on call" not in text
    assert gmail.send_called is False


def test_label_failure_does_not_block_click_draft(settings: Settings, store) -> None:
    class BoomGmail(FakeGmail):
        def apply_label(self, message_id: str, label_name: str) -> None:
            raise RuntimeError("labelId not found")

    message = make_message()
    gmail = BoomGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    assert result["draftText"]
    assert len(gmail.drafts) == 1
    assert gmail.send_called is False


def test_redraft_uses_saved_preview_when_compose_empty(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "hours?")
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Office hours?",
        "hours?",
        existing_draft="",
        notes="Please mention support hours are 9 AM to 6 PM.",
    )
    text = result["draftText"].lower()
    assert result["draft_id"] != first["draft_id"] or len(gmail.drafts) == 1
    assert "9" in text or "support" in text or "please mention" in text
    assert gmail.send_called is False


def test_click_modifies_existing_draft_in_place(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Office hours?",
        "what are hours?",
        existing_draft="hello please confirm the office hours",
    )
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["id"] == first["draft_id"]
    assert result["draft_id"] == first["draft_id"]
    text = result["draftText"]
    assert "Dear Ada" in text
    assert "please confirm" in text.lower() or "office hours" in text.lower()
    assert "Best regards" in text
    assert gmail.send_called is False


def test_click_updates_draft_when_gmail_sends_web_compose_id(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Office hours?",
        "what are hours?",
        existing_draft="hello please confirm the office hours",
        gmail_draft_id=f"#msg-a:{first['draft_id']}",
    )
    assert len(gmail.drafts) == 1
    assert result["draft_id"] == first["draft_id"]
    assert gmail.send_called is False


def test_click_does_not_rewrite_incoming_email_as_the_draft(settings: Settings, store) -> None:
    from gmail_bot.models import StyleExample

    message = make_message(
        sender="Rachana <esupport@kuk.ac.in>",
        subject="Re: Request to Reopen Fee Payment Link",
        body="As discussed on call, I hope the issue has been resolved.",
    )
    gmail = FakeGmail([message])
    gmail.sent_examples = [
        StyleExample(
            subject="Request to Reopen Fee Payment Link",
            body="Please reopen the fee payment link for the internship backlog.",
        )
    ]
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail(
        "Rachana <esupport@kuk.ac.in>",
        "Re: Request to Reopen Fee Payment Link",
        "As discussed on call, I hope the issue has been resolved.",
        existing_draft="As discussed on call, I hope the issue has been resolved.",
    )
    text = result["draftText"].lower()
    assert "as discussed on call" not in text
    assert "issue has been resolved" not in text
    assert "fee payment" in text or "reopen" in text
    assert gmail.send_called is False


def test_click_updates_live_thread_draft_when_store_id_is_gone(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    live_id = first["draft_id"]
    queued = bot.store.get_queue_item("m1") or {}
    bot.store.upsert_queue({**queued, "draft_id": "r-stale-missing", "status": "drafted"})
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Office hours?",
        "what are hours?",
        existing_draft="hello please confirm the office hours",
    )
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["id"] == live_id
    assert result["draft_id"] == live_id
    assert gmail.send_called is False


def test_click_prefers_open_gmail_draft_id(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_from_open_mail("Ada <ada@example.com>", "Office hours?", "what are hours?")
    live_id = first["draft_id"]
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Office hours?",
        "what are hours?",
        existing_draft="hello please confirm the office hours",
        gmail_draft_id=live_id,
    )
    assert len(gmail.drafts) == 1
    assert result["draft_id"] == live_id
    assert gmail.send_called is False


def test_click_uses_open_thread_id(settings: Settings, store) -> None:
    first = make_message(message_id="m1", thread_id="t1", subject="Fee")
    second = make_message(
        message_id="m2",
        thread_id="t2",
        sender="Ada <ada@example.com>",
        subject="Fee payment",
        body="Please reopen the fee link.",
    )
    gmail = FakeGmail([first, second])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_from_open_mail(
        "Ada <ada@example.com>",
        "Fee",
        "Please reopen the fee link.",
        thread_id="t2",
    )
    assert result["message_id"] == "m2"
    assert gmail.drafts[0]["thread_id"] == "t2"
    assert gmail.send_called is False


def test_click_rejects_gmail_tab_for_other_account(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message], profile_email="me@gmail.com")
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    try:
        bot.draft_from_open_mail(
            "Ada <ada@example.com>",
            "Office hours?",
            "what are hours?",
            page_email="other@gmail.com",
        )
        raise AssertionError("expected account mismatch")
    except RuntimeError as exc:
        assert "other@gmail.com" in str(exc).lower()
        assert "me@gmail.com" in str(exc).lower()
    assert gmail.drafts == []
    assert gmail.send_called is False


def test_compose_creates_individual_gmail_draft(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_compose_mail(
        "Ada <ada@example.com>",
        "Fee payment",
        notes="Please reopen the fee payment link.",
    )
    assert result["mode"] == "compose"
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["from"] == "me@gmail.com"
    assert "ada@example.com" in gmail.drafts[0]["to"].lower()
    assert gmail.drafts[0]["standalone"] is True
    assert "thank you for your email" not in result["draftText"].lower()
    assert "fee payment" in result["draftText"].lower() or "reopen" in result["draftText"].lower()
    assert gmail.labels == []
    assert gmail.send_called is False


def test_compose_updates_open_gmail_draft_in_place(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    first = bot.draft_compose_mail("Ada <ada@example.com>", "Hours", notes="please confirm hours")
    result = bot.draft_compose_mail(
        "Ada <ada@example.com>",
        "Hours",
        existing_draft="hello please confirm the office hours",
        gmail_draft_id=first["draft_id"],
    )
    assert len(gmail.drafts) == 1
    assert result["draft_id"] == first["draft_id"]
    assert "please confirm" in result["draftText"].lower() or "hours" in result["draftText"].lower()
    assert gmail.send_called is False


def test_compose_redrafts_typed_draft_without_notes(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    result = bot.draft_compose_mail(
        "Ada <ada@example.com>",
        "Fee payment",
        existing_draft="hello please reopen the fee payment link for the internship backlog",
        gmail_draft_id="#msg-a:r-compose-1",
    )
    assert len(gmail.drafts) == 1
    text = result["draftText"].lower()
    assert "internship" in text or "reopen" in text
    assert "as discussed on call" not in text
    assert gmail.send_called is False


def test_compose_cllg_web_id_updates_the_same_draft(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    cllg = "CllgCHrjnPljXhkHsstfGGsFQxpKXJNHvpWGgFJNncrqmglKNWQPrjHrbbdvQXBfcLSkWJRwQzg"
    first = bot.draft_compose_mail(
        "Ada <ada@example.com>",
        "Hours",
        notes="please confirm hours",
        gmail_draft_id=cllg,
    )
    result = bot.draft_compose_mail(
        "Ada <ada@example.com>",
        "Hours",
        existing_draft="please confirm the office hours",
        gmail_draft_id=cllg,
    )
    assert len(gmail.drafts) == 1
    assert result["draft_id"] == first["draft_id"]
    assert gmail.send_called is False


def test_compose_requires_a_recipient(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    try:
        bot.draft_compose_mail("")
        raise AssertionError("expected missing To")
    except RuntimeError as exc:
        assert "to" in str(exc).lower()
    assert gmail.drafts == []


def test_local_api_compose_mode(settings: Settings, store) -> None:
    import http.client
    import json

    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    server = start_local_api(port=0, bot=bot)
    host, port = server.server_address
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        payload = json.dumps(
            {
                "mode": "compose",
                "to": "ada@example.com",
                "subject": "Fee payment",
                "notes": "Please reopen the fee payment link.",
            }
        )
        conn.request("POST", "/api/draft-open", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 200
        assert data["ok"] is True
        assert data["mode"] == "compose"
        assert len(gmail.drafts) == 1
        assert gmail.send_called is False
    finally:
        server.shutdown()
