"""STT backend abstraction. Add a new engine by subclassing STTBackend."""
from abc import ABC, abstractmethod
import numpy as np


class STTBackend(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a float32 mono PCM array. Return plain text."""


class FasterWhisperBackend(STTBackend):
    """faster-whisper — default backend, runs on CPU (ctranslate2 in this image lacks CUDA)."""

    def __init__(self, model: str = 'small', device: str = 'cpu',
                 compute_type: str = 'int8', language: str = 'en',
                 download_root: str | None = None):
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            model, device=device, compute_type=compute_type,
            download_root=download_root or None,
        )
        self._language = language

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            vad_filter=True,
        )
        parts = [s.text.strip() for s in segments if s.text.strip()]
        if not parts:
            return ''
        # Drop repeated segments — Whisper hallucination pattern
        deduped = [parts[0]]
        for p in parts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        return ' '.join(deduped)


class WhisperCudaBackend(STTBackend):
    """openai-whisper on CUDA — ~10x faster than faster-whisper CPU on Jetson.

    Requires: pip install openai-whisper
    Note: cuDNN is disabled to work around version mismatch on Jetson (harmless for inference).
    """

    def __init__(self, model: str = 'base', language: str = 'en'):
        import torch
        torch.backends.cudnn.enabled = False  # cuDNN version mismatch on Jetson
        import whisper
        self._model    = whisper.load_model(model, device='cuda')
        self._language = language

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        result = self._model.transcribe(
            audio,
            language=self._language,
            fp16=False,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        text = result.get('text', '').strip()
        if not text:
            return ''
        # Drop repeated segments — Whisper hallucination pattern
        parts = [s['text'].strip() for s in result.get('segments', []) if s['text'].strip()]
        if not parts:
            return text
        deduped = [parts[0]]
        for p in parts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        return ' '.join(deduped)


# ── Registry ────────────────────────────────────────────────────────────────
# To swap backends: change stt_backend param in voice_params.yaml
_REGISTRY: dict[str, type[STTBackend]] = {
    'faster_whisper': FasterWhisperBackend,
    'whisper_cuda':   WhisperCudaBackend,
}


def load_stt_backend(name: str, **kwargs) -> STTBackend:
    if name not in _REGISTRY:
        raise ValueError(f'Unknown STT backend "{name}". Available: {list(_REGISTRY)}')
    return _REGISTRY[name](**kwargs)
