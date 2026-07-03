"""VAD backends — Silero (neural, noise-robust) with webrtcvad fallback.

webrtcvad is an energy detector: fans, TV, music and keyboard clicks all read
as "speech" and end up in Whisper. Silero is a small neural model that fires
on human speech specifically. Interface: is_speech(chunk_int16) -> bool for a
1280-sample (80ms) 16kHz chunk.

Silero is run DIRECTLY with onnxruntime on the model file shipped inside the
silero-vad pip package — the package's own loader imports torchaudio, which
is broken in this container (torch/torchaudio CUDA mismatch), and a VAD has
no business dragging torch onto the audio thread anyway.
"""

import logging

import numpy as np

_log = logging.getLogger(__name__)

_SILERO_FRAME = 512      # silero v5/v6 requires exactly 512 samples @ 16kHz


class SileroVAD:
    def __init__(self, threshold: float = 0.5):
        import os
        import onnxruntime as ort
        # Prefer the copy openwakeword downloads to the persistent model store;
        # locating it inside the silero_vad package would IMPORT the package,
        # which drags in the container's broken torchaudio.
        path = os.path.join(os.environ.get('WAKE_MODEL_DIR', '/model_store/wake'),
                            'silero_vad.onnx')
        if not os.path.exists(path):
            import importlib.util
            spec = importlib.util.find_spec('silero_vad')   # no import executed
            path = os.path.join(spec.submodule_search_locations[0],
                                'data', 'silero_vad.onnx')
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(path, sess_options=opts,
                                          providers=['CPUExecutionProvider'])
        self._threshold = threshold
        self._sr = np.array(16000, dtype=np.int64)
        # Two model generations exist: v5+ uses one combined 'state' input;
        # the v4 model (bundled by openwakeword) uses separate 'h'/'c' and
        # accepts variable-length windows. Detect by input names.
        names = {i.name for i in self._sess.get_inputs()}
        self._v5 = 'state' in names
        if self._v5:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)
        _log.info('Silero VAD ready (onnxruntime direct, %s, threshold %.2f)',
                  'v5' if self._v5 else 'v4', threshold)

    def is_speech(self, chunk: np.ndarray) -> bool:
        f32 = (chunk.astype(np.float32) / 32768.0)
        if not self._v5:
            # v4: whole 80ms chunk in one call
            out, self._h, self._c = self._sess.run(
                None, {'input': f32[None, :], 'sr': self._sr,
                       'h': self._h, 'c': self._c})
            return float(out.reshape(-1)[-1]) > self._threshold
        hit = False
        for i in range(0, len(f32) - _SILERO_FRAME + 1, _SILERO_FRAME):
            out, self._state = self._sess.run(
                None,
                {'input': f32[i:i + _SILERO_FRAME][None, :],
                 'state': self._state, 'sr': self._sr},
            )
            if float(out[0]) > self._threshold:
                hit = True
        return hit


class WebRtcVAD:
    """Legacy fallback — same behaviour as the original stt_node detector."""

    _FRAME = 320   # 20ms @ 16kHz

    def __init__(self, aggressiveness: int = 3):
        import webrtcvad
        self._vad = webrtcvad.Vad(aggressiveness)
        _log.info('WebRTC VAD ready (mode %d)', aggressiveness)

    def is_speech(self, chunk: np.ndarray) -> bool:
        try:
            pcm = chunk.astype(np.int16)
            n = self._FRAME
            for i in range(0, len(pcm) - n + 1, n):
                if self._vad.is_speech(pcm[i:i + n].tobytes(), 16000):
                    return True
            return False
        except Exception:
            return False


def load_vad(backend: str, aggressiveness: int = 3):
    """Return the requested VAD, falling back to webrtc if silero is missing."""
    if backend == 'silero':
        try:
            return SileroVAD()
        except Exception as e:
            _log.warning('Silero VAD unavailable (%s) — using webrtcvad', e)
    return WebRtcVAD(aggressiveness)
