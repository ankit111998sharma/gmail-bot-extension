from gmail_bot.bot import InboxBot
from gmail_bot.config import Settings
from gmail_bot.guardian import is_retryable_draft_error
from tests.conftest import FakeGmail, make_message


def test_guardian_repairs_missing_faqs(settings: Settings, store) -> None:
    bot = InboxBot(settings=settings, store=store, gmail=FakeGmail([]))
    faqs = settings.faqs_path
    if faqs.is_file():
        faqs.unlink()
    report = bot.guardian.scan_and_repair()
    assert faqs.is_file()
    assert any("FAQ" in item for item in report["fixed"])
    assert report["ok"] is True


def test_guardian_repairs_invalid_faqs_json(settings: Settings, store) -> None:
    bot = InboxBot(settings=settings, store=store, gmail=FakeGmail([]))
    settings.faqs_path.write_text("{not json", encoding="utf-8")
    report = bot.guardian.scan_and_repair()
    assert '"faqs"' in settings.faqs_path.read_text(encoding="utf-8")
    assert any("invalid" in item.lower() or "FAQ" in item for item in report["fixed"])


def test_retryable_draft_error_detects_label_failures() -> None:
    assert is_retryable_draft_error('HttpError 400 ... "labelId not found"')
    assert not is_retryable_draft_error("This email did not match the saved sender/keyword filters.")


def test_retry_failed_drafts_recovers_label_errors(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    store.upsert_queue(
        {
            "message_id": message.message_id,
            "thread_id": message.thread_id,
            "sender": message.sender,
            "subject": message.subject,
            "snippet": message.snippet,
            "status": "failed",
            "attempts": 1,
            "last_error": "labelId not found",
        }
    )
    count = bot.retry_failed_drafts(limit=3)
    assert count == 1
    assert gmail.send_called is False
    assert store.get_queue_item(message.message_id)["status"] == "drafted"
