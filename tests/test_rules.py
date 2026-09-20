from gmail_bot.rules import extract_rules, normalize_rules_url, rule_suggestions


def test_extract_rules_prefers_topic_and_must_lines() -> None:
    page = """
    Campus news
    Contact the helpdesk for hostel keys.

    Fee payment rules
    1. Students must pay the semester fee before the notified deadline.
    2. The fee payment link may be reopened for internship backlog cases.
    3. Late payment is not permitted after the final notice.
    """
    rules = extract_rules(page, "Request to Reopen Fee Payment Link for Internship Backlog")
    blob = " ".join(rules).lower()
    assert "deadline" in blob
    assert "backlog" in blob or "not permitted" in blob
    assert "hostel keys" not in blob


def test_rule_suggestions_and_url_validation() -> None:
    assert normalize_rules_url("https://kuk.ac.in/rules") == "https://kuk.ac.in/rules"
    try:
        normalize_rules_url("ftp://example.com/x")
        raise AssertionError("expected invalid URL")
    except ValueError:
        pass
    suggestions = rule_suggestions(
        ["Students must pay the semester fee before the notified deadline."],
        "Fee Payment",
    )
    assert suggestions
    assert "deadline" in suggestions[0].lower()
