"""
LM Studio client — talks to the local LM Studio server's OpenAI-compatible API.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Default LM Studio local endpoint
DEFAULT_BASE_URL = "http://localhost:1234/v1"


class LMStudioClient:
    """Thin wrapper around LM Studio's OpenAI-compatible chat completions API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 120):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._model_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base}{path}"
        r = requests.post(url, json=payload, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Model info
    # ------------------------------------------------------------------
    @property
    def model_id(self) -> str:
        """Return (and cache) the currently loaded model ID."""
        if self._model_id is None:
            try:
                models = self._get("/models")
                data = models.get("data", [])
                self._model_id = data[0]["id"] if data else "unknown"
            except Exception:
                self._model_id = "unknown"
        return self._model_id

    def _get(self, path: str) -> dict:
        r = requests.get(f"{self._base}{path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def refresh_model(self) -> str:
        self._model_id = None
        return self.model_id

    # ------------------------------------------------------------------
    # Chat with optional images (vision)
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> dict:
        """
        Send a chat completion request.

        Parameters
        ----------
        messages : list[dict]
            OpenAI-format messages. Images can be included as:
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}
        tools : list[dict] | None
            OpenAI function-calling tool definitions.
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        logger.debug("Chat payload (messages=%d, tools=%d)", len(messages), len(tools or []))
        return self._post("/chat/completions", payload)

    # ------------------------------------------------------------------
    # Convenience: build a user message with an image
    # ------------------------------------------------------------------
    @staticmethod
    def image_content(base64_jpeg: str, detail: str = "auto") -> dict:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64_jpeg}", "detail": detail},
        }

    @staticmethod
    def text_content(text: str) -> dict:
        return {"type": "text", "text": text}
