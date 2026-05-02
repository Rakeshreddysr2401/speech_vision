import io
import sounddevice as sd
import numpy as np


class KokoroBackend:
    """TTS backend using Kokoro (neural, CUDA-accelerated)."""

    def __init__(self, params: dict):
        from kokoro import KPipeline
        self._speed = float(params.get("speed", 1.0))
        self._voice = str(params.get("model", "af_heart"))
        self._sample_rate = int(params.get("sample_rate", 24000))
        self._device_index = params.get("device_index")
        lang = str(params.get("lang", "a"))  # 'a' = American English
        self._pipeline = KPipeline(lang_code=lang)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def synthesize(self, text: str) -> np.ndarray:
        """Return float32 audio array for the given text."""
        chunks = []
        for _, _, audio in self._pipeline(text, voice=self._voice, speed=self._speed):
            if audio is not None:
                chunks.append(audio)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def speak(self, text: str) -> None:
        audio = self.synthesize(text)
        if audio.size > 0:
            # sd.play(audio, samplerate=self._sample_rate, blocking=True)
            sd.play( audio, samplerate=self._sample_rate, device=self._device_index, blocking=True )
