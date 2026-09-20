from gmail_bot.config import Settings
from gmail_bot.llm import GeminiLlm, PlaceholderLlm, _gemini_text, build_llm


def test_gemini_text_parser_reads_candidates() -> None:
    text = _gemini_text(
        {
            "candidates": [
                {"content": {"parts": [{"text": "Hello,\n\nI will follow up.\n\nBest regards,\nme"}]}}
            ]
        }
    )
    assert "I will follow up" in text


def test_build_llm_stays_placeholder_without_key(settings: Settings) -> None:
    settings.inbox_llm = "placeholder"
    settings.gemini_api_key = ""
    llm = build_llm(settings, prefer_ai=True)
    assert isinstance(llm, PlaceholderLlm)


def test_build_llm_prefer_ai_uses_gemini_when_key_present(settings: Settings) -> None:
    settings.inbox_llm = "placeholder"
    settings.gemini_api_key = "test-key"
    llm = build_llm(settings, prefer_ai=True)
    assert isinstance(llm, GeminiLlm)
    assert llm.api_key == "test-key"
