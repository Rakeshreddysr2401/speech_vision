"""TTS backend abstraction. Add a new engine by subclassing TTSBackend."""
from abc import ABC, abstractmethod


class TTSBackend(ABC):
    @abstractmethod
    def speak(self, text: str, output_device: int | None, sample_rate: int,
              on_audio_start=None):
        """Synthesise text and play it. Blocks until playback is complete.

        on_audio_start: optional zero-arg callable invoked after synthesis,
        right before playback begins — lets callers time synthesis vs audio.
        """

    def stop(self):
        """Best-effort abort of the current speak() from another thread.

        speak() should return promptly after this is called; a speak() already
        past synthesis skips playback. Default: no-op."""


class KokoroBackend(TTSBackend):
    """Kokoro TTS via kokoro-onnx (ONNX runtime, no PyTorch required)."""

    ONNX_MODEL  = '/opt/kokoro-onnx/examples/kokoro-v1.0.onnx'
    VOICES_FILE = '/opt/kokoro-onnx/examples/voices-v1.0.bin'

    def __init__(self, voice: str = 'af_heart', speed: float = 1.0):
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(self.ONNX_MODEL, self.VOICES_FILE)
        self._voice  = voice
        self._speed  = speed
        self._proc        = None    # active pw-cat process (PipeWire path)
        self._interrupted = False   # set by stop(), checked around playback

    def speak(self, text: str, output_device: int | None, sample_rate: int,
              on_audio_start=None):
        import os, subprocess
        import numpy as np
        import sounddevice as sd
        self._interrupted = False
        samples, sr = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang='en-us'
        )
        if self._interrupted:
            return  # stopped while synthesising — skip playback
        if on_audio_start is not None:
            on_audio_start()
        if output_device is None:
            # No ALSA hw device found — play via PipeWire (handles BT speakers)
            proc = subprocess.Popen(
                ['/usr/local/bin/pw-cat', '--playback', '--format=f32',
                 f'--rate={sr}', '--channels=1', '-'],
                stdin=subprocess.PIPE,
                env={**os.environ},
            )
            self._proc = proc
            try:
                proc.communicate(input=samples.astype(np.float32).tobytes())
            except BrokenPipeError:
                pass  # killed by stop() mid-playback
            finally:
                self._proc = None
        else:
            sd.play(samples, samplerate=sr, device=output_device)
            sd.wait()

    def stop(self):
        self._interrupted = True
        try:
            import sounddevice as sd
            sd.stop()   # unblocks sd.wait() on the ALSA path
        except Exception:
            pass
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()   # unblocks communicate() on the PipeWire path
            except Exception:
                pass


# ── Registry ────────────────────────────────────────────────────────────────
# To swap backends: change tts_backend param in voice_params.yaml
_REGISTRY: dict[str, type[TTSBackend]] = {
    'kokoro': KokoroBackend,
}


def load_tts_backend(name: str, **kwargs) -> TTSBackend:
    if name not in _REGISTRY:
        raise ValueError(f'Unknown TTS backend "{name}". Available: {list(_REGISTRY)}')
    return _REGISTRY[name](**kwargs)
