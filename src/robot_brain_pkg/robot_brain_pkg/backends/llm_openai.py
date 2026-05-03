"""OpenAI-compatible backend.

Covers three cases with the same class:
  provider=openai    — OpenAI API (GPT-4o, GPT-4o-mini …)
  provider=llamacpp  — local llama-server (Gemma4, Llama3, …)
                       Set base_url = "http://<host>:8080/v1"
                       VLM works when server is started with --mmproj <file>
  Any other OpenAI-compatible endpoint — just set base_url accordingly.
"""

import base64
import os

from .llm_base import LLMBackend


class OpenAIBackend(LLMBackend):
    def __init__(self, params: dict):
        from openai import OpenAI

        api_key_env = params.get("api_key_env")
        api_key = os.environ.get(api_key_env, "not-needed") if api_key_env else "not-needed"

        base_url = params.get("base_url") or None  # None → OpenAI default endpoint
        self._model = params.get("model", "gpt-4o-mini")
        self._temperature = float(params.get("temperature", 0.7))
        self._max_tokens = int(params.get("max_tokens", 150))
        self._system_prompt = params.get("system_prompt", "")

        self._client = OpenAI(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------
    def _prepend_system(self, messages: list[dict]) -> list[dict]:
        if not self._system_prompt:
            return messages
        return [{"role": "system", "content": self._system_prompt}] + messages

    def chat(self, history: list[dict], user_text: str) -> str:
        messages = self._prepend_system(history + [{"role": "user", "content": user_text}])
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return resp.choices[0].message.content.strip()

    def chat_with_image(self, history: list[dict], user_text: str, image_bytes: bytes) -> str:
        """Send image as base64 data-URL — works with OpenAI GPT-4o and llama.cpp --mmproj."""
        b64 = base64.b64encode(image_bytes).decode()
        user_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
        messages = self._prepend_system(history + [user_msg])
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return resp.choices[0].message.content.strip()
