import collections
import webrtcvad
import numpy as np


class VADBuffer:
    """Rolling buffer that detects speech onset/offset via webrtcvad."""

    def __init__(self, sample_rate=16000, aggressiveness=2,
                 frame_ms=30, padding_ms=300):
        self._vad = webrtcvad.Vad(aggressiveness)
        self._sample_rate = sample_rate
        self._frame_bytes = int(sample_rate * frame_ms / 1000) * 2  # int16
        self._padding_frames = padding_ms // frame_ms
        self._ring = collections.deque(maxlen=self._padding_frames)
        self._triggered = False
        self._voiced: list[bytes] = []

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def process_frame(self, frame: bytes) -> bytes | None:
        """Feed one PCM frame. Returns complete utterance bytes when speech ends, else None."""
        is_speech = self._vad.is_speech(frame, self._sample_rate)

        if not self._triggered:
            self._ring.append((frame, is_speech))
            num_voiced = sum(1 for _, s in self._ring if s)
            if num_voiced > 0.9 * self._ring.maxlen:
                self._triggered = True
                self._voiced.extend(f for f, _ in self._ring)
                self._ring.clear()
        else:
            self._voiced.append(frame)
            self._ring.append((frame, is_speech))
            num_unvoiced = sum(1 for _, s in self._ring if not s)
            if num_unvoiced > 0.9 * self._ring.maxlen:
                utterance = b"".join(self._voiced)
                self._triggered = False
                self._voiced = []
                self._ring.clear()
                return utterance
        return None
