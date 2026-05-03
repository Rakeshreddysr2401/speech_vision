"""Ollama backend (native /api/chat endpoint, no API key required).

Text LLM:  set model to any text model (llama3.2, mistral, gemma3 …)
VLM:       set model to a multimodal model (llava, moondream, llava-llama3 …)
           Images are sent as base64 strings in the Ollama messages format.
"""

import base64

import requests

from .llm_base import LLMBackend


class OllamaBackend(LLMBackend):
    def __init__(self, params: dict):
        self._base_url = params.get("base_url", "http://localhost:11434").rstrip("/")
        self._model = params.get("model", "llama3.2")
        self._temperature = float(params.get("temperature", 0.7))
        self._max_tokens = int(params.get("max_tokens", 150))
        self._system_prompt = params.get("system_prompt", "")
        self._timeout = int(params.get("timeout", 60))

    # ------------------------------------------------------------------
    def _build_messages(self, history: list[dict], user_text: str,
                        image_bytes: bytes = None) -> list[dict]:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)
        user_msg: dict = {"role": "user", "content": user_text}
        if image_bytes:
            user_msg["images"] = [base64.b64encode(image_bytes).decode()]
        messages.append(user_msg)
        return messages

    def _call(self, messages: list[dict]) -> str:
        resp = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self._temperature,
                    "num_predict": self._max_tokens,
                },
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

    def chat(self, history: list[dict], user_text: str) -> str:
        return self._call(self._build_messages(history, user_text))

    def chat_with_image(self, history: list[dict], user_text: str, image_bytes: bytes) -> str:
        return self._call(self._build_messages(history, user_text, image_bytes))
