"""STT backend abstraction. Add a new engine by subclassing STTBackend."""
from abc import ABC, abstractmethod
import numpy as np


class STTBackend(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a float32 mono PCM array. Return plain text."""


class FasterWhisperBackend(STTBackend):
    """faster-whisper — default backend, runs on CUDA or CPU."""

    def __init__(self, model: str = 'small', device: str = 'cuda',
                 compute_type: str = 'float16', language: str = 'en',
                 beam_size: int = 5, vad_filter: bool = True):
        from faster_whisper import WhisperModel
        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self._language = language
        self._beam_size = beam_size
        self._vad_filter = vad_filter

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
        )
        return ' '.join(s.text.strip() for s in segments).strip()


# ── Registry ────────────────────────────────────────────────────────────────
# To swap backends: change stt_backend param in voice_params.yaml
_REGISTRY: dict[str, type[STTBackend]] = {
    'faster_whisper': FasterWhisperBackend,
}


def load_stt_backend(name: str, **kwargs) -> STTBackend:
    if name not in _REGISTRY:
        raise ValueError(f'Unknown STT backend "{name}". Available: {list(_REGISTRY)}')
    return _REGISTRY[name](**kwargs)
