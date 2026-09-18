from __future__ import annotations

import re

DEVANAGARI = re.compile(r"[\u0900-\u097F]")
ARABIC = re.compile(r"[\u0600-\u06FF]")
CJK = re.compile(r"[\u4E00-\u9FFF]")


def detect_language(text: str) -> str:
    sample = text or ""
    if DEVANAGARI.search(sample):
        return "hi"
    if ARABIC.search(sample):
        return "ar"
    if CJK.search(sample):
        return "zh"
    return "en"


def language_name(code: str) -> str:
    return {
        "hi": "Hindi",
        "en": "English",
        "ar": "Arabic",
        "zh": "Chinese",
    }.get(code, code)
