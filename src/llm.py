"""Thin LLM client. Free options: Google Gemini (AI Studio key) or Groq.
Returns None when no key is set so the program falls back to rule-based logic."""
from __future__ import annotations

import json
import os
import re
import time

import requests


class LLM:
    def __init__(self, cfg: dict):
        self.provider = (cfg.get("provider") or "none").lower()
        self.model = cfg.get("model", "gemini-flash-latest")
        self.groq_model = cfg.get("groq_model", "llama-3.3-70b-versatile")
        # .strip() removes stray spaces / line breaks picked up when pasting a key into GitHub
        self.gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'") or None
        self.groq_key = (os.getenv("GROQ_API_KEY") or "").strip().strip('"').strip("'") or None

    @property
    def available(self) -> bool:
        return (self.provider == "gemini" and bool(self.gemini_key)) or \
               (self.provider == "groq" and bool(self.groq_key))

    def ask_json(self, system: str, prompt: str, retries: int = 3) -> dict | None:
        if not self.available:
            return None
        for attempt in range(retries):
            try:
                text = self._gemini(system, prompt) if self.provider == "gemini" else self._groq(system, prompt)
                return _parse_json(text)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (429, 500, 503):
                    time.sleep(8 * (attempt + 1))   # free-tier rate limit — back off and retry
                    continue
                print(f"[llm] HTTP error: {e}")
                return None
            except Exception as e:
                print(f"[llm] error: {e}")
                return None
        return None

    def _gemini(self, system: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        r = requests.post(url, json=body, headers={"x-goog-api-key": self.gemini_key}, timeout=90)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    def _groq(self, system: str, prompt: str) -> str:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json={"model": self.groq_model, "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}]},
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
