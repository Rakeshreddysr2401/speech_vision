"""Shared audio capture — one InputStream per consumer, queue-based delivery."""
import logging
import os
import queue
import signal
import subprocess
import time
import threading
import numpy as np
import sounddevice as sd

_log = logging.getLogger(__name__)

_PW_CAT = '/usr/local/bin/pw-cat'


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
            _log.debug('Audio stream status: %s', status)
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # drop oldest-ish chunk rather than blocking the audio thread


class PipeWireCapture:
    """Capture audio via pw-cat subprocess — used when no ALSA hw device is found.

    BT devices route through PipeWire and are not visible to PortAudio/sounddevice,
    so we bypass sounddevice entirely and read raw PCM from pw-cat's stdout.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_frames: int = 1280,
        maxsize: int = 40,
        target: str | None = None,
    ):
        self._rate   = sample_rate
        self._frames = chunk_frames
        self._target = target       # PipeWire node name, e.g. "ec_mic" (AEC source)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=maxsize)
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self._stopped = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped = True
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _spawn(self):
        env = {**os.environ}
        cmd = [_PW_CAT, '--record', '--format=s16',
               f'--rate={self._rate}', '--channels=1']
        if self._target:
            cmd += ['--target', self._target]
        self._proc = subprocess.Popen(
            cmd + ['-'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            # Prevent pw-cat from dying on SIGPIPE when parent is a ROS node
            preexec_fn=lambda: signal.signal(signal.SIGPIPE, signal.SIG_DFL),
        )
        _log.info('PipeWire capture started (pid=%d)', self._proc.pid)

    def _run_loop(self):
        """Outer loop: spawn pw-cat, read chunks, restart on crash."""
        bytes_per_chunk = self._frames * 2  # int16 = 2 bytes/sample
        while not self._stopped:
            self._spawn()
            while not self._stopped and self._proc.poll() is None:
                data = self._proc.stdout.read(bytes_per_chunk)
                if len(data) < bytes_per_chunk:
                    break
                arr = np.frombuffer(data, dtype=np.int16)
                try:
                    self._queue.put_nowait(arr)
                except queue.Full:
                    pass
            if self._proc.poll() is not None and not self._stopped:
                err = self._proc.stderr.read().decode(errors='replace').strip()
                _log.warning('pw-cat exited (rc=%d)%s', self._proc.returncode,
                             f': {err}' if err else '')
                self._proc = None
                # Brief pause before restart to avoid tight crash loop
                time.sleep(1.0)


def make_capture(
    device_idx: int | None,
    sample_rate: int = 16000,
    chunk_frames: int = 1280,
    pw_target: str | None = None,
) -> AudioCapture | PipeWireCapture:
    """Return the right capture backend.

    pw_target set (e.g. "ec_mic") → PipeWire capture from that node — the
    echo-cancelled source. Otherwise: sounddevice for USB, pw-cat for BT/None.
    """
    if pw_target:
        return PipeWireCapture(sample_rate=sample_rate, chunk_frames=chunk_frames,
                               target=pw_target)
    if device_idx is None:
        return PipeWireCapture(sample_rate=sample_rate, chunk_frames=chunk_frames)
    return AudioCapture(device_idx=device_idx, sample_rate=sample_rate, chunk_frames=chunk_frames)
