"""TTS backend abstraction. Add a new engine by subclassing TTSBackend."""
from abc import ABC, abstractmethod


class TTSBackend(ABC):
    @abstractmethod
    def speak(self, text: str, output_device: int | None, sample_rate: int):
        """Synthesise text and play it. Blocks until playback is complete."""


class KokoroBackend(TTSBackend):
    """Kokoro TTS — default backend."""

    def __init__(self, voice: str = 'af_heart', speed: float = 1.0):
        from kokoro import KPipeline
        self._pipeline = KPipeline(lang_code='a')
        self._voice = voice
        self._speed = speed

    def speak(self, text: str, output_device: int | None, sample_rate: int):
        import sounddevice as sd
        for _, _, audio in self._pipeline(text, voice=self._voice, speed=self._speed):
            if audio is not None:
                sd.play(audio, samplerate=sample_rate, device=output_device)
                sd.wait()


# ── Registry ────────────────────────────────────────────────────────────────
# To swap backends: change tts_backend param in voice_params.yaml
_REGISTRY: dict[str, type[TTSBackend]] = {
    'kokoro': KokoroBackend,
}


def load_tts_backend(name: str, **kwargs) -> TTSBackend:
    if name not in _REGISTRY:
        raise ValueError(f'Unknown TTS backend "{name}". Available: {list(_REGISTRY)}')
    return _REGISTRY[name](**kwargs)
