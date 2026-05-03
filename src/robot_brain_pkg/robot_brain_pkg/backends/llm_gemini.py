"""Google Gemini backend (google-generativeai SDK).

Supports both text (gemini-2.0-flash, gemini-1.5-pro …) and VLM
(all Gemini models accept inline images natively).
"""

import io
import os

from .llm_base import LLMBackend


class GeminiBackend(LLMBackend):
    def __init__(self, params: dict):
        import google.generativeai as genai

        api_key = os.environ.get(params.get("api_key_env", "GEMINI_API_KEY"), "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY env var not set")

        genai.configure(api_key=api_key)

        self._system_prompt = params.get("system_prompt", "")
        gen_config = genai.GenerationConfig(
            temperature=float(params.get("temperature", 0.7)),
            max_output_tokens=int(params.get("max_tokens", 150)),
        )
        self._model = genai.GenerativeModel(
            params.get("model", "gemini-2.0-flash"),
            generation_config=gen_config,
            system_instruction=self._system_prompt or None,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _to_gemini_history(history: list[dict]) -> list[dict]:
        """Convert OpenAI-style history to Gemini role format."""
        result = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            result.append({"role": role, "parts": [msg["content"]]})
        return result

    def chat(self, history: list[dict], user_text: str) -> str:
        convo = self._model.start_chat(history=self._to_gemini_history(history))
        resp = convo.send_message(user_text)
        return resp.text.strip()

    def chat_with_image(self, history: list[dict], user_text: str, image_bytes: bytes) -> str:
        import PIL.Image
        image = PIL.Image.open(io.BytesIO(image_bytes))
        convo = self._model.start_chat(history=self._to_gemini_history(history))
        resp = convo.send_message([user_text, image])
        return resp.text.strip()
