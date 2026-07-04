"""STT node — echo-cancelled, wake-word-gated speech input.

Pipeline (see ros2_ws/JETSON_VOICE_UPGRADE.md):

    ec_mic (PipeWire AEC source — the mic minus the robot's own audio)
      → openWakeWord ("hey rakhi"/"hey jarvis" + barge-in), every chunk, ~1ms
      → capture window (wake heard, or attention after the robot spoke)
      → VAD endpointing (Silero neural, webrtcvad fallback)
      → Whisper (whisper_cuda) — runs ONLY on in-window utterances
      → wake_gate (secondary text check / alias stripping)
      → /voice/user_input

Inversion vs the old node: previously everything in the room was transcribed
then text-filtered (Whisper misspells "Rakhi" seven ways; the 15s attention
window forwarded all room chatter). Now audio is rejected before STT unless
the robot was addressed.

Echo cancellation makes the mic usable WHILE the robot speaks or plays music:
  - wake word during playback = barge-in → halt TTS locally, capture the
    utterance, forward it (the Pi5 aborts its in-flight turn).
  - "stop" during playback → halt TTS + music locally (<400ms), then tell the
    Pi5 (/voice/tts_stop) so wheels stop too.

Every neural dependency degrades gracefully: no openwakeword → legacy
transcript gating; no silero → webrtcvad; no ec_mic → default mic.
"""

import json
import queue
import threading
import time
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_capture import make_capture
from voice_pkg.audio_device import find_input_device, list_devices
from voice_pkg.stt_backend import load_stt_backend
from voice_pkg.vad_backend import load_vad
from voice_pkg.wake_engine import WakeEngine
from voice_pkg.wake_gate import wake_gate

# Stop-keyword spotting while TTS/music plays. With AEC the segments are
# clean (the robot's own voice is subtracted), so this is a plain short-burst
# keyword check — the old duration gymnastics guarded against self-echo.
_SPOT_MIN_S     = 0.2
_SPOT_MAX_S     = 2.0
_SPOT_SILENCE_S = 0.4
_SPOT_MAX_WORDS = 3      # "stop", "stop it", "rakhi stop"


class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        self.declare_parameter('mic_preference', 'auto')
        # PipeWire capture target. "ec_mic" = the echo-cancelled source
        # (requires the host 99-echo-cancel.conf). "" = legacy device search.
        self.declare_parameter('audio_source', 'ec_mic')
        self.declare_parameter('silence_timeout', 0.8)
        self.declare_parameter('min_speech_duration', 0.3)
        self.declare_parameter('vad_backend', 'silero')          # silero | webrtc
        self.declare_parameter('vad_aggressiveness', 3)          # webrtc fallback mode
        self.declare_parameter('chunk_frames', 1280)
        self.declare_parameter('stt_backend', 'whisper_cuda')
        self.declare_parameter('model', 'small')
        self.declare_parameter('language', 'en')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('download_root', '/model_store/whisper_cache')
        # Stop keyword (spot path during TTS/music; acts locally first)
        self.declare_parameter('stop_spotter', True)
        self.declare_parameter('stop_keyword', 'stop')
        # ── Wake word ────────────────────────────────────────────────────
        # openwakeword: neural KWS on raw audio (primary gate, recommended).
        # transcript:   legacy — transcribe everything, gate by text.
        self.declare_parameter('wake_backend', 'openwakeword')
        # Model names in /model_store/wake (or absolute paths). Swap to
        # ["hey_rakhi"] once the custom model is trained.
        self.declare_parameter('wake_models', ['hey_jarvis_v0.1'])
        self.declare_parameter('wake_threshold', 0.5)
        # Seconds the capture window stays open after a wake trigger.
        self.declare_parameter('capture_window_s', 8.0)
        # Follow-up window after the robot speaks (no name needed). Short —
        # the neural wake word is reliable, unlike transcript matching.
        self.declare_parameter('attention_s', 6.0)
        self.declare_parameter('wake_word', True)   # transcript-gate secondary check
        self.declare_parameter('wake_aliases',
                               ['rakhi', 'rakhee', 'raki', 'rakki', 'rocky', 'rocki', 'roki',
                                'jarvis'])

        mic_pref        = self.get_parameter('mic_preference').value
        silence_timeout = self.get_parameter('silence_timeout').value
        min_speech      = self.get_parameter('min_speech_duration').value
        chunk_frames    = self.get_parameter('chunk_frames').value
        backend_name    = self.get_parameter('stt_backend').value

        self._sample_rate   = 16000
        self._silence_limit = int(silence_timeout * self._sample_rate / chunk_frames)
        self._min_frames    = int(min_speech * self._sample_rate / chunk_frames)

        self._pub  = self.create_publisher(String, '/voice/user_input', 10)
        self._timing_pub = self.create_publisher(String, '/diag/timing', 10)
        self._silence_timeout = silence_timeout
        self._lock = threading.Lock()

        self._tts_speaking   = False
        self._music_playing  = False
        self._music_state_at = 0.0    # monotonic time of last music_state msg
        self._is_recording   = False
        self._speech_frames  = []
        self._silence_frames = 0

        # ── Stop-keyword spotter state ──────────────────────────────────────
        self._stop_spotter  = self.get_parameter('stop_spotter').value
        self._stop_keyword  = self.get_parameter('stop_keyword').value.lower()
        chunk_s             = chunk_frames / self._sample_rate
        self._spot_min_frames     = max(1, int(_SPOT_MIN_S / chunk_s))
        self._spot_max_frames     = int(_SPOT_MAX_S / chunk_s)
        self._spot_silence_limit  = max(1, int(_SPOT_SILENCE_S / chunk_s))
        self._spot_frames:  list = []
        self._spot_silence  = 0
        self._spot_overlong = False
        self._stop_pub  = self.create_publisher(String, '/voice/tts_stop', 10)
        self._music_pub = self.create_publisher(String, '/audio/music_cmd', 10)

        # ── Wake engine (primary gate) + windows ────────────────────────────
        self._wake_backend  = self.get_parameter('wake_backend').value
        self._wake_engine   = WakeEngine(
            list(self.get_parameter('wake_models').value),
            self.get_parameter('wake_threshold').value,
        ) if self._wake_backend == 'openwakeword' else None
        self._audio_gated   = bool(self._wake_engine and self._wake_engine.available)
        self._capture_window_s = self.get_parameter('capture_window_s').value
        self._window_until  = 0.0   # wake-opened capture window
        if not self._audio_gated and self._wake_backend == 'openwakeword':
            self.get_logger().warning(
                'openWakeWord unavailable — legacy transcript gating active')

        # ── Transcript wake gate (secondary check / alias stripping) ────────
        self._wake_enabled    = self.get_parameter('wake_word').value
        self._wake_aliases    = {a.lower() for a in self.get_parameter('wake_aliases').value}
        self._attention_s     = self.get_parameter('attention_s').value
        self._attention_until = 0.0

        self.create_subscription(Bool, '/voice/tts_speaking', self._tts_cb, 10)
        self.create_subscription(String, '/audio/music_state', self._music_cb, 10)

        self._vad = load_vad(self.get_parameter('vad_backend').value,
                             self.get_parameter('vad_aggressiveness').value)

        self._transcription_queue = queue.Queue(maxsize=2)
        threading.Thread(target=self._transcription_worker, daemon=True).start()

        backend_kwargs = {
            'faster_whisper': dict(
                model         = self.get_parameter('model').value,
                device        = self.get_parameter('device').value,
                compute_type  = self.get_parameter('compute_type').value,
                language      = self.get_parameter('language').value,
                download_root = self.get_parameter('download_root').value or None,
            ),
            'whisper_cuda': dict(
                model    = self.get_parameter('model').value,
                language = self.get_parameter('language').value,
            ),
        }.get(backend_name, {})
        self._backend = load_stt_backend(backend_name, **backend_kwargs)

        self._mic_pref     = mic_pref
        self._chunk_frames = chunk_frames

        # ── Capture: echo-cancelled PipeWire source, or legacy device hunt ──
        self._audio_source = self.get_parameter('audio_source').value.strip()
        if self._audio_source:
            self._device_idx = None
            self._capture = make_capture(
                device_idx=None, sample_rate=self._sample_rate,
                chunk_frames=chunk_frames, pw_target=self._audio_source)
            self.get_logger().info(f'Capture: PipeWire target "{self._audio_source}" (AEC)')
        else:
            self.get_logger().info(list_devices())
            self._device_idx, device_name = find_input_device(mic_pref)
            self.get_logger().info(f'Mic: {device_name} (idx={self._device_idx})')
            self._capture = make_capture(
                device_idx=self._device_idx, sample_rate=self._sample_rate,
                chunk_frames=chunk_frames)
            self.create_timer(10.0, self._check_device)
        self._capture.start()
        threading.Thread(target=self._audio_loop, daemon=True).start()

        actual_device = 'cuda' if backend_name == 'whisper_cuda' else self.get_parameter('device').value
        self.get_logger().info(
            f'STT ready — backend={backend_name} model={self.get_parameter("model").value} '
            f'device={actual_device} gate={"openwakeword" if self._audio_gated else "transcript"} '
            f'vad={type(self._vad).__name__}'
        )

    def _check_device(self):
        new_idx, new_name = find_input_device(self._mic_pref)
        if new_idx != self._device_idx:
            self.get_logger().info(f'Mic switched: {new_name} (idx={new_idx})')
            old = self._capture
            self._capture = make_capture(
                device_idx=new_idx,
                sample_rate=self._sample_rate,
                chunk_frames=self._chunk_frames,
            )
            self._capture.start()
            old.stop()
            with self._lock:
                self._device_idx   = new_idx
                self._is_recording = False
                self._speech_frames  = []
                self._silence_frames = 0

    # ── Playback state callbacks ────────────────────────────────────────────

    def _tts_cb(self, msg: Bool):
        with self._lock:
            was_speaking = self._tts_speaking
            self._tts_speaking = msg.data
            if msg.data:
                self._is_recording   = False
                self._speech_frames  = []
                self._silence_frames = 0
            elif was_speaking:
                # Robot just finished speaking — hold attention so the user can
                # follow up without repeating the wake word.
                self._attention_until = time.time() + self._attention_s
            self._spot_frames   = []
            self._spot_silence  = 0
            self._spot_overlong = False

    def _music_cb(self, msg: String):
        try:
            state = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        with self._lock:
            self._music_playing = bool(state.get('playing')) and not state.get('paused')
            self._music_state_at = time.monotonic()

    def _music_active(self) -> bool:
        """music_node heartbeats at 1Hz while playing. If it dies mid-song the
        last state says "playing" forever — without this expiry the mic would
        stay in stop-spotter-only gating until reboot. (Lock held by caller.)"""
        return self._music_playing and time.monotonic() - self._music_state_at < 3.0

    # ── Window logic ────────────────────────────────────────────────────────

    def _window_open(self) -> bool:
        now = time.time()
        return now < self._window_until or now < self._attention_until

    def _on_wake(self, model_name: str):
        """Wake word heard (called with lock)."""
        now = time.time()
        self._window_until = now + self._capture_window_s
        self._emit_timing('wake_detected', model=model_name)
        if self._tts_speaking:
            # Barge-in: the user is addressing the robot over its own speech —
            # halt TTS locally (tts_node flushes) and capture the utterance.
            # Music keeps playing: it routes through the AEC reference sink, so
            # the canceller already subtracts it from the mic (ducking happens
            # automatically when the robot replies).
            self.get_logger().info(f'Barge-in: wake "{model_name}" during TTS')
            self._stop_pub.publish(String(data=f'[wake:{model_name}]'))
        else:
            self.get_logger().info(f'Wake: "{model_name}" — listening')
        # Fresh utterance from here.
        self._is_recording   = False
        self._speech_frames  = []
        self._silence_frames = 0

    # ── Audio loop ──────────────────────────────────────────────────────────

    def _audio_loop(self):
        while rclpy.ok():
            chunk = self._capture.read(timeout=1.0)
            if chunk is None:
                continue
            with self._lock:
                # 1. Wake engine sees EVERY chunk (stateful streaming model).
                if self._audio_gated:
                    hit = self._wake_engine.detect(chunk)
                    if hit:
                        self._on_wake(hit)
                        continue   # the wake chunk itself isn't utterance audio

                playback_active = self._tts_speaking or self._music_active()

                # 2. During playback with NO open window: only the stop
                #    spotter listens (AEC gives it clean audio).
                if playback_active and not self._window_open():
                    if self._stop_spotter:
                        self._spot(chunk)
                    continue

                # 3. Normal capture — inside a window (audio-gated mode) or
                #    always (legacy transcript mode).
                if self._audio_gated and not self._window_open():
                    continue   # not addressed — never reaches Whisper
                self._process(chunk)

    def _spot(self, chunk: np.ndarray):
        """Short-burst keyword check while the robot plays audio (lock held)."""
        if self._vad.is_speech(chunk):
            if self._spot_overlong:
                return
            self._spot_frames.append(chunk.copy())
            self._spot_silence = 0
            if len(self._spot_frames) > self._spot_max_frames:
                self._spot_frames  = []
                self._spot_overlong = True
            return
        if self._spot_overlong:
            self._spot_overlong = False
            return
        if not self._spot_frames:
            return
        self._spot_silence += 1
        if self._spot_silence >= self._spot_silence_limit:
            frames, self._spot_frames = self._spot_frames, []
            self._spot_silence = 0
            if len(frames) >= self._spot_min_frames and not self._transcription_queue.full():
                audio = np.concatenate(frames).astype(np.float32) / 32768.0
                self._transcription_queue.put_nowait(('spot', audio))

    def _process(self, chunk: np.ndarray):
        if self._vad.is_speech(chunk):
            if not self._is_recording:
                self._is_recording   = True
                self._speech_frames  = []
                self._silence_frames = 0
            self._speech_frames.append(chunk.copy())
            self._silence_frames = 0
        elif self._is_recording:
            self._speech_frames.append(chunk.copy())
            self._silence_frames += 1
            if self._silence_frames >= self._silence_limit:
                if len(self._speech_frames) >= self._min_frames:
                    audio = np.concatenate(self._speech_frames).astype(np.float32) / 32768.0
                    if not self._transcription_queue.full():
                        self._emit_timing('stt_vad_end',
                                          speech_s=round(len(audio) / self._sample_rate, 2),
                                          silence_timeout=self._silence_timeout)
                        self._transcription_queue.put_nowait(('user', audio))
                self._speech_frames  = []
                self._silence_frames = 0
                self._is_recording   = False

    # ── Transcription worker ────────────────────────────────────────────────

    def _transcription_worker(self):
        while True:
            try:
                kind, audio = self._transcription_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if kind == 'spot':
                self._spot_transcribe(audio)
            else:
                self._transcribe(audio)

    def _spot_transcribe(self, audio: np.ndarray):
        """Keyword check for a short burst heard during playback."""
        try:
            text = self._backend.transcribe(audio, self._sample_rate) or ''
        except Exception as e:
            self.get_logger().error(f'Spot transcription error: {e}')
            return
        words = [w.strip('.,!?…\'"').lower() for w in text.split()]
        words = [w for w in words if w]
        if words and self._stop_keyword in words and len(words) <= _SPOT_MAX_WORDS:
            self.get_logger().info(f'Stop keyword spotted: "{text}"')
            self._emit_timing('stop_spotted')
            # Act locally FIRST (instant), then tell the Pi5 (wheels sweep).
            self._music_pub.publish(String(data=json.dumps(
                {'action': 'stop', 't': time.time(), 'reason': 'stop_keyword'})))
            self._stop_pub.publish(String(data=text))
        elif words:
            self.get_logger().debug(f'Spot segment ignored: "{text}"')

    def _transcribe(self, audio: np.ndarray):
        try:
            text = self._backend.transcribe(audio, self._sample_rate)
            if not text:
                return
            if self._audio_gated:
                # The wake model already decided we're addressed — the text
                # gate only strips the leading/trailing name if present.
                _, out = wake_gate(text, self._wake_aliases, attention_active=True)
                text = out
                self._attention_until = time.time() + self._attention_s
            elif self._wake_enabled:
                attention = time.time() < self._attention_until
                forward, out = wake_gate(text, self._wake_aliases, attention)
                if not forward:
                    self.get_logger().info(f'Not addressed to me — ignored: "{text}"')
                    self._emit_timing('wake_ignored', chars=len(text))
                    return
                self._attention_until = time.time() + self._attention_s
                text = out
            self.get_logger().info(f'Transcribed: "{text}"')
            self._emit_timing('stt_end', chars=len(text))
            msg = String()
            msg.data = text
            self._pub.publish(msg)
        except Exception as e:
            self.get_logger().error(f'Transcription error: {e}')

    def _emit_timing(self, stage: str, **fields):
        event = {'stage': stage, 't': time.time(), **fields}
        self._timing_pub.publish(String(data=json.dumps(event)))

    def destroy_node(self):
        self._capture.stop()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = STTNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
