from __future__ import annotations

from gmail_bot.bot import InboxBot, matches_filters
from gmail_bot.config import Settings
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
    assert "DRAFT" in gmail.drafts[0]["text"]


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
