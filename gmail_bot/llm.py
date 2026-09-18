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


def build_llm(settings: Settings) -> LlmPort:
    if settings.inbox_llm == "ollama":
        return OllamaLlm(settings.ollama_host, settings.ollama_model)
    return PlaceholderLlm()
