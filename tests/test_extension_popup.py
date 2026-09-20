from gmail_bot.config import PROJECT_ROOT


def test_extension_popup_shows_optional_url_and_notes() -> None:
    root = PROJECT_ROOT / "extension"
    manifest = (root / "manifest.json").read_text(encoding="utf-8")
    popup = (root / "popup.html").read_text(encoding="utf-8")
    script = (root / "popup.js").read_text(encoding="utf-8")
    background = (root / "background.js").read_text(encoding="utf-8")
    content = (root / "content.js").read_text(encoding="utf-8")
    assert '"default_popup": "popup.html"' in manifest
    assert '"scripting"' in manifest
    assert "ensureGmailContent" in background
    assert "ping" in background
    assert "website-url" in popup
    assert "draft-notes" in popup
    assert "Draft reply" in popup
    assert "optional" in popup.lower()
    assert "draftFromPopup" in script
    assert "shouldRetry" in script
    assert "rulesUrl" in script
    assert "notes" in script
    assert "draftFromPopup" in background
    assert "runDraft" in background
    assert "Write a new mail" in popup
    assert "Reply to an open mail" in popup
    assert "Redraft" in popup
    assert "mode-compose" in popup
    assert "mode-reply" in popup
    assert "draft-to" in popup
    assert "draft-subject" in popup
    assert "selectedMode" in script
    assert "mode" in script
    assert "subject" in script
    assert "shouldUseComposeMode" in content
    assert "mode-compose" in content
    assert "Write a new mail" in content
    assert "Reply to an open mail" in content
    assert "gmail-bot-mode-compose" in content
    assert "gmail-bot-mode-reply" in content
    assert "gmail-bot-redraft" in content
    assert "pageEmail" in content
    assert "g_editable" in content
    assert "keepDraftVisible" in content
    assert "openGmailDraft" in content
    assert "normalizeGmailDraftId" in content
    assert "gmail_quote" in content
    assert "stripQuotedText" in content
    assert "composeBodyText" in content
    assert "removeQuotedBlocks" in content
    assert "isNewComposeWindow" in content
    assert "rememberDraftId" in content
    assert "cllg" in content.lower()
    assert "1.5.5" in manifest
