"""Shared audio capture — one InputStream per consumer, queue-based delivery."""
import queue
import numpy as np
import sounddevice as sd


class AudioCapture:
    """Opens a single sounddevice InputStream and delivers chunks via a queue.

    Usage:
        cap = AudioCapture(device_idx=2, sample_rate=16000, chunk_frames=1280)
        cap.start()
        while True:
            chunk = cap.read()   # np.int16 array of length chunk_frames
        cap.stop()
    """

    def __init__(
        self,
        device_idx: int | None,
        sample_rate: int = 16000,
        chunk_frames: int = 1280,
        maxsize: int = 40,
    ):
        self._device = device_idx
        self._rate = sample_rate
        self._frames = chunk_frames
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=maxsize)
        self._stream: sd.InputStream | None = None

    def start(self):
        self._stream = sd.InputStream(
            device=self._device,
            samplerate=self._rate,
            channels=1,
            dtype='int16',
            blocksize=self._frames,
            callback=self._cb,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Return next chunk or None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _cb(self, indata: np.ndarray, frames: int, time, status):
        if status:
            pass  # overflow/underflow — tolerate
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # drop oldest-ish chunk rather than blocking the audio thread
