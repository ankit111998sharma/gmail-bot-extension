from __future__ import annotations

from gmail_bot.bot import InboxBot, matches_filters
from gmail_bot.config import Settings, normalize_email, same_email
from tests.conftest import FakeGmail, make_message


def test_skip_already_processed(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    store.mark_processed(message.message_id, message.thread_id, "draft-old")
    assert bot.process_once() == 0
    assert gmail.drafts == []
    assert gmail.send_called is False


def test_process_creates_draft_and_label(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    assert bot.process_once() == 1
    assert len(gmail.drafts) == 1
    assert gmail.drafts[0]["thread_id"] == "t1"
    assert gmail.labels == [(message.message_id, settings.label_name)]
    assert store.already_processed(message.message_id)
    assert "DRAFT" not in gmail.drafts[0]["text"]
    assert "knowledge base" not in gmail.drafts[0]["text"].lower()
    assert "Office hours?" not in gmail.drafts[0]["text"]
    assert gmail.drafts[0]["from"] == "me@gmail.com"
    assert "ada@example.com" in gmail.drafts[0]["to"].lower()
    assert gmail.drafts[0]["from"] != gmail.drafts[0]["to"]
    assert "me" in gmail.drafts[0]["text"].split("Best regards")[-1].lower()


def test_second_pass_does_not_duplicate(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.process_once()
    bot.process_once()
    assert len(gmail.drafts) == 1


def test_start_pause_stop(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.start()
    status = bot.status()
    assert status.running is True
    assert status.paused is False
    bot.pause()
    assert bot.status().paused is True
    bot.stop()
    assert bot.status().running is False
    assert bot.status().paused is False


def test_sender_filter(settings: Settings) -> None:
    settings.sender_filter = "ada@example.com"
    assert matches_filters(make_message(), settings) is True
    other = make_message(sender="Bob <bob@example.com>")
    assert matches_filters(other, settings) is False


def test_normalize_email() -> None:
    assert normalize_email("Ada <ada@example.com>") == "ada@example.com"
    assert normalize_email("  YOU@Gmail.Com ") == "you@gmail.com"
    assert normalize_email("not-an-email") == ""
    assert same_email("Me <me@gmail.com>", "me@gmail.com") is True
    assert same_email("Ada <ada@example.com>", "me@gmail.com") is False


def test_process_skips_mail_from_inbox_owner(settings: Settings, store) -> None:
    mine = make_message(
        sender="Me <me@gmail.com>",
        to_header="Ada <ada@example.com>",
        subject="Follow up",
        body="Please confirm the hours.",
    )
    gmail = FakeGmail([mine], profile_email="me@gmail.com")
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    store.set_setting("connected_email", "me@gmail.com")
    assert bot.process_once() == 0
    assert gmail.drafts == []
    assert gmail.send_called is False


def test_set_target_email_is_saved(settings: Settings, store) -> None:
    gmail = FakeGmail([])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    assert bot.set_target_email("Ada <ada@example.com>") == "ada@example.com"
    assert store.get_setting("gmail_account") == "ada@example.com"
    assert bot.status().target_email == "ada@example.com"


def test_connect_account_uses_typed_email(settings: Settings, store) -> None:
    gmail = FakeGmail([], profile_email="old@example.com")
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    assert bot.connect_account("new@example.com") == "new@example.com"
    assert gmail.profile_email == "new@example.com"
    assert store.get_setting("connected_email") == "new@example.com"


def test_process_once_rejects_other_signed_in_account(settings: Settings, store) -> None:
    message = make_message()
    gmail = FakeGmail([message], profile_email="other@example.com")
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.set_target_email("ada@example.com")
    try:
        bot.process_once()
        raise AssertionError("expected account mismatch")
    except RuntimeError as exc:
        assert "ada@example.com" in str(exc)
        assert "other@example.com" in str(exc)
    assert gmail.drafts == []


def test_process_once_runs_for_matching_account(settings: Settings, store) -> None:
    message = make_message(sender="Bob <bob@example.com>")
    gmail = FakeGmail([message], profile_email="ada@example.com")
    bot = InboxBot(settings=settings, store=store, gmail=gmail)
    bot.set_target_email("ada@example.com")
    assert bot.process_once() == 1
    assert store.get_setting("connected_email") == "ada@example.com"


def test_style_fetch_failure_is_not_retried(settings: Settings, store) -> None:
    class FailingStyleGmail(FakeGmail):
        def __init__(self, messages):
            super().__init__(messages)
            self.fetch_calls = 0

        def fetch_sent_examples(self, limit: int = 20):
            self.fetch_calls += 1
            raise RuntimeError("quota exceeded")

    message = make_message()
    gmail = FailingStyleGmail([message])
    bot = InboxBot(settings=settings, store=store, gmail=gmail)

    assert bot.process_once() == 1
    assert bot.process_once() == 0
    assert gmail.fetch_calls == 1
    assert bot._style_loaded is True
    assert len(gmail.drafts) == 1
