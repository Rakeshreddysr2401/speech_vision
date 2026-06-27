"""TTS backend abstraction. Add a new engine by subclassing TTSBackend."""
from abc import ABC, abstractmethod


class TTSBackend(ABC):
    @abstractmethod
    def speak(self, text: str, output_device: int | None, sample_rate: int):
        """Synthesise text and play it. Blocks until playback is complete."""


class KokoroBackend(TTSBackend):
    """Kokoro TTS via kokoro-onnx (ONNX runtime, no PyTorch required)."""

    ONNX_MODEL  = '/opt/kokoro-onnx/examples/kokoro-v1.0.onnx'
    VOICES_FILE = '/opt/kokoro-onnx/examples/voices-v1.0.bin'

    def __init__(self, voice: str = 'af_heart', speed: float = 1.0):
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(self.ONNX_MODEL, self.VOICES_FILE)
        self._voice  = voice
        self._speed  = speed

    def speak(self, text: str, output_device: int | None, sample_rate: int):
        import os, subprocess
        import numpy as np
        import sounddevice as sd
        samples, sr = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang='en-us'
        )
        if output_device is None:
            # No ALSA hw device found — play via PipeWire (handles BT speakers)
            subprocess.run(
                ['/usr/local/bin/pw-cat', '--playback', '--format=f32',
                 f'--rate={sr}', '--channels=1', '-'],
                input=samples.astype(np.float32).tobytes(),
                env={**os.environ},
                check=False,
            )
        else:
            sd.play(samples, samplerate=sr, device=output_device)
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
