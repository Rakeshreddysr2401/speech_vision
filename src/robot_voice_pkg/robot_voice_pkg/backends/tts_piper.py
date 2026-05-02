import subprocess
import tempfile
import os
import numpy as np
import sounddevice as sd
import soundfile as sf


class PiperBackend:
    """TTS backend using Piper (ONNX, CPU, fast)."""

    def __init__(self, params: dict):
        self._model_path = str(params.get("model_path", "/models/piper/en_US-lessac-medium.onnx"))
        self._config_path = str(params.get("config_path", self._model_path + ".json"))
        self._speed = float(params.get("speed", 1.0))
        self._sample_rate = int(params.get("sample_rate", 22050))
        self._piper_bin = str(params.get("piper_bin", "piper"))

        if not os.path.exists(self._model_path):
            raise FileNotFoundError(f"Piper model not found: {self._model_path}")

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def synthesize(self, text: str) -> np.ndarray:
        """Run piper subprocess, return float32 audio array."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out_path = f.name
        try:
            cmd = [
                self._piper_bin,
                "--model", self._model_path,
                "--config", self._config_path,
                "--length-scale", str(1.0 / self._speed),
                "--output-file", out_path,
            ]
            proc = subprocess.run(
                cmd, input=text.encode(), capture_output=True, timeout=15
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Piper failed: {proc.stderr.decode()}")
            audio, _ = sf.read(out_path, dtype="float32")
            return audio
        finally:
            os.unlink(out_path)

    def speak(self, text: str) -> None:
        audio = self.synthesize(text)
        if audio.size > 0:
            # sd.play(audio, samplerate=self._sample_rate, blocking=True)
            sd.play( audio, samplerate=self._sample_rate, device=2, blocking=True )
