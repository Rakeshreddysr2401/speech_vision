from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """Common interface for all LLM/VLM backends.

    history: list of {"role": "user"|"assistant", "content": str} — no system message.
    """

    @abstractmethod
    def chat(self, history: list[dict], user_text: str) -> str:
        """Text-only inference."""
        ...

    def chat_with_image(self, history: list[dict], user_text: str, image_bytes: bytes) -> str:
        """VLM inference. Falls back to text-only for backends that don't support images."""
        return self.chat(history, user_text)
