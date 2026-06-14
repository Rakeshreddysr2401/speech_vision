"""TTS backend abstraction. Add a new engine by subclassing TTSBackend."""
from abc import ABC, abstractmethod


class TTSBackend(ABC):
    @abstractmethod
    def speak(self, text: str, output_device: int | None, sample_rate: int):
        """Synthesise text and play it. Blocks until playback is complete."""


class KokoroBackend(TTSBackend):
    """Kokoro TTS via ONNX Runtime — runs on CUDA EP, lower VRAM than HF pipeline."""

    def __init__(self, voice: str = 'af_heart', speed: float = 1.0,
                 model_path: str = 'kokoro-v1.0.onnx', voices_path: str = 'voices.bin'):
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(model_path, voices_path)
        self._voice = voice
        self._speed = speed

    def speak(self, text: str, output_device: int | None, sample_rate: int):
        import sounddevice as sd
        import numpy as np
        samples, sr = self._kokoro.create(text, voice=self._voice,
                                          speed=self._speed, lang='en-us')
        audio = samples.astype(np.float32)
        sd.play(audio, samplerate=sr, device=output_device)
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
