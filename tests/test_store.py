from __future__ import annotations

from gmail_bot.store import Store


def test_queue_and_processed_roundtrip(store: Store) -> None:
    store.upsert_queue(
        {
            "message_id": "m1",
            "thread_id": "t1",
            "sender": "a@x.com",
            "subject": "Hi",
            "snippet": "hello",
            "status": "pending",
            "attempts": 0,
        }
    )
    store.mark_processed("m1", "t1", "d1")
    assert store.already_processed("m1")
    assert store.processed_count() == 1
    item = store.get_queue_item("m1")
    assert item is not None
    assert item["subject"] == "Hi"
    store.upsert_template("thanks", "Thank you.")
    assert store.list_templates()[0]["name"] == "thanks"
    store.set_setting("gmail_account", "ada@example.com")
    assert store.get_setting("gmail_account") == "ada@example.com"
    store.upsert_queue(
        {
            "message_id": "m2",
            "thread_id": "t2",
            "sender": "b@x.com",
            "subject": "Later",
            "snippet": "later",
            "status": "failed",
            "attempts": 1,
            "last_error": "labelId not found",
        }
    )
    failed = store.list_failed_queue()
    assert failed[0]["message_id"] == "m2"
