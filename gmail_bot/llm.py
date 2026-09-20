from __future__ import annotations

from typing import Protocol

import httpx

from gmail_bot.config import Settings


class LlmPort(Protocol):
    name: str

    def generate(self, prompt: str) -> str: ...


class PlaceholderLlm:
    name = "placeholder"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError("Placeholder engine does not call a model")


class OllamaLlm:
    name = "ollama"

    def __init__(self, host: str, model: str, timeout: float = 60.0) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You write email draft bodies only. No subject lines."},
                {"role": "user", "content": prompt},
            ],
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        message = (data.get("message") or {}).get("content") or ""
        text = message.strip()
        if not text:
            raise RuntimeError("Ollama returned an empty draft")
        return text


class GeminiLlm:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: float = 45.0) -> None:
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 512},
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, params={"key": self.api_key}, json=payload)
            response.raise_for_status()
            data = response.json()
        text = _gemini_text(data)
        if not text:
            raise RuntimeError("Gemini returned an empty draft")
        return text


def _gemini_text(data: dict) -> str:
    parts: list[str] = []
    for candidate in data.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            piece = (part.get("text") or "").strip()
            if piece:
                parts.append(piece)
    text = "\n".join(parts).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1].strip()
    return text


def build_llm(settings: Settings, *, prefer_ai: bool = False) -> LlmPort:
    mode = (settings.inbox_llm or "placeholder").strip().lower()
    if mode == "ollama":
        return OllamaLlm(settings.ollama_host, settings.ollama_model)
    if mode == "gemini" or (prefer_ai and getattr(settings, "gemini_api_key", "")):
        key = (getattr(settings, "gemini_api_key", "") or "").strip()
        if key:
            return GeminiLlm(key, getattr(settings, "gemini_model", "gemini-2.0-flash"))
        if mode == "gemini":
            raise RuntimeError("Set GEMINI_API_KEY in .env to use Gemini.")
    return PlaceholderLlm()
