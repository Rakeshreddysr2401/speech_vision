"""Neural wake-word engine (openWakeWord) — audio-level gating BEFORE STT.

Replaces transcript-based wake gating as the primary gate: a small ONNX
keyword model scores every 80ms chunk (~1ms CPU on Orin), and only audio
inside a wake-opened capture window ever reaches Whisper. Falls back to
`available = False` when openwakeword/models are missing — stt_node then
reverts to the legacy transcribe-everything + transcript-gate path, so the
robot never goes deaf because of a missing dependency.

Models live in /model_store/wake (bind-mounted, survives container rebuilds):
    melspectrogram.onnx, embedding_model.onnx   — shared feature extractors
    hey_jarvis_v0.1.onnx                        — bootstrap wake model
    hey_rakhi.onnx                              — custom model (when trained,
                                                  see JETSON_VOICE_UPGRADE.md)
Download: python3 -c "import openwakeword.utils as u; u.download_models(target_directory='/model_store/wake')"
"""

import logging
import os

import numpy as np

_log = logging.getLogger(__name__)

MODEL_DIR = os.environ.get('WAKE_MODEL_DIR', '/model_store/wake')


class WakeEngine:
    """Thin wrapper around openwakeword.Model with graceful unavailability."""

    def __init__(self, model_names: list[str], threshold: float = 0.5):
        self.available = False
        self.threshold = threshold
        self._names: list[str] = []
        try:
            from openwakeword.model import Model
            paths = []
            for name in model_names:
                p = name if os.path.isabs(name) else os.path.join(MODEL_DIR, name)
                if not p.endswith('.onnx'):
                    p += '.onnx'
                if os.path.exists(p):
                    paths.append(p)
                else:
                    _log.warning('Wake model missing: %s', p)
            if not paths:
                _log.warning('No wake models found in %s — wake engine disabled', MODEL_DIR)
                return
            self._model = Model(
                wakeword_models=paths,
                melspec_model_path=os.path.join(MODEL_DIR, 'melspectrogram.onnx'),
                embedding_model_path=os.path.join(MODEL_DIR, 'embedding_model.onnx'),
                inference_framework='onnx',
            )
            # prediction keys are model basenames without extension
            self._names = [os.path.splitext(os.path.basename(p))[0] for p in paths]
            self.available = True
            _log.info('Wake engine ready: %s (threshold %.2f)', self._names, threshold)
        except Exception as e:
            _log.warning('openWakeWord unavailable (%s) — falling back to transcript gating', e)

    def detect(self, chunk_int16: np.ndarray) -> str | None:
        """Feed one 16kHz int16 chunk; return the triggered model name or None.

        Must be called on EVERY chunk (the model is stateful/streaming) —
        including chunks you otherwise discard.
        """
        if not self.available:
            return None
        try:
            scores = self._model.predict(chunk_int16)
        except Exception:
            _log.exception('wake predict failed')
            return None
        for name in self._names:
            if scores.get(name, 0.0) > self.threshold:
                # reset so one utterance can't retrigger for several chunks
                self._model.reset()
                return name
        return None
