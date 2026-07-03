import json
import queue
import threading
import time
import numpy as np
import webrtcvad
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_capture import make_capture
from voice_pkg.audio_device import find_input_device, list_devices
from voice_pkg.stt_backend import load_stt_backend
from voice_pkg.wake_gate import wake_gate

# WebRTC VAD requires 20ms frames: 320 samples at 16kHz
_VAD_FRAME_SAMPLES = 320

# Stop-keyword spotting while TTS is playing ("barge-in lite", no AEC).
# The mic hears the robot's own voice, so we only transcribe SHORT isolated
# bursts (a barked "stop!") and require a tiny transcript containing the
# keyword — the robot's own sentences are continuous and get discarded by the
# duration cap before ever reaching Whisper.
_SPOT_MIN_S     = 0.2    # shorter = noise
_SPOT_MAX_S     = 1.5    # longer = robot's own speech / real sentence
_SPOT_SILENCE_S = 0.4    # gap that closes a spot segment
_SPOT_MAX_WORDS = 3      # "stop", "stop it", "rakhi stop"


class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        self.declare_parameter('mic_preference', 'auto')
        self.declare_parameter('silence_timeout', 1.5)
        self.declare_parameter('min_speech_duration', 0.5)
        self.declare_parameter('vad_aggressiveness', 2)
        self.declare_parameter('chunk_frames', 1280)
        self.declare_parameter('stt_backend', 'whisper_cuda')
        self.declare_parameter('model', 'small')
        self.declare_parameter('language', 'en')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('download_root', '/model_store/whisper_cache')
        # Spot the stop keyword while the robot is speaking (mic otherwise muted)
        self.declare_parameter('stop_spotter', True)
        self.declare_parameter('stop_keyword', 'stop')
        # Wake-word gate: only utterances addressed to the robot reach the brain.
        # Aliases cover Whisper's spellings of "Rakhi"; attention_s keeps the
        # conversation open after the robot speaks (no name needed for follow-ups).
        self.declare_parameter('wake_word', True)
        self.declare_parameter('wake_aliases',
                               ['rakhi', 'rakhee', 'raki', 'rakki', 'rocky', 'rocki', 'roki'])
        self.declare_parameter('attention_s', 15.0)

        mic_pref        = self.get_parameter('mic_preference').value
        silence_timeout = self.get_parameter('silence_timeout').value
        min_speech      = self.get_parameter('min_speech_duration').value
        vad_mode        = self.get_parameter('vad_aggressiveness').value
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
        self._stop_pub = self.create_publisher(String, '/voice/tts_stop', 10)

        # ── Wake-word gate state ────────────────────────────────────────────
        self._wake_enabled    = self.get_parameter('wake_word').value
        self._wake_aliases    = {a.lower() for a in self.get_parameter('wake_aliases').value}
        self._attention_s     = self.get_parameter('attention_s').value
        self._attention_until = 0.0   # epoch — follow-ups forwarded until then

        self.create_subscription(Bool, '/voice/tts_speaking', self._tts_cb, 10)

        self._vad = webrtcvad.Vad(vad_mode)

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

        self._mic_pref    = mic_pref
        self._chunk_frames = chunk_frames

        self.get_logger().info(list_devices())
        self._device_idx, device_name = find_input_device(mic_pref)
        self.get_logger().info(f'Mic: {device_name} (idx={self._device_idx})')

        self._capture = make_capture(
            device_idx=self._device_idx,
            sample_rate=self._sample_rate,
            chunk_frames=chunk_frames,
        )
        self._capture.start()
        threading.Thread(target=self._audio_loop, daemon=True).start()

        self.create_timer(10.0, self._check_device)

        actual_device = 'cuda' if backend_name == 'whisper_cuda' else self.get_parameter('device').value
        self.get_logger().info(
            f'STT ready — backend={backend_name} '
            f'model={self.get_parameter("model").value} '
            f'device={actual_device}'
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
            # Entering OR leaving speech resets the spotter
            self._spot_frames   = []
            self._spot_silence  = 0
            self._spot_overlong = False

    def _is_speech(self, chunk: np.ndarray) -> bool:
        # WebRTC VAD only accepts 10/20/30 ms frames (320 samples = 20 ms @ 16 kHz),
        # but our capture chunk is 1280 samples (80 ms). Evaluate EVERY 20 ms
        # sub-frame and treat the chunk as speech if any sub-frame is speech.
        # (Previously only the first 320 samples were checked, so 75% of each
        # chunk was ignored — clipping word tails and leaking onset noise.)
        try:
            pcm = chunk.astype(np.int16)
            n = _VAD_FRAME_SAMPLES
            for i in range(0, len(pcm) - n + 1, n):
                if self._vad.is_speech(pcm[i:i + n].tobytes(), self._sample_rate):
                    return True
            return False
        except Exception:
            return False

    def _audio_loop(self):
        while rclpy.ok():
            chunk = self._capture.read(timeout=1.0)
            if chunk is None:
                continue
            with self._lock:
                if self._tts_speaking:
                    if self._stop_spotter:
                        self._spot(chunk)
                    continue
                self._process(chunk)

    def _spot(self, chunk: np.ndarray):
        """Called (with lock) for every chunk while TTS is speaking.

        Collect short isolated speech bursts and queue them for keyword-only
        transcription. Continuous speech longer than _SPOT_MAX_S is the robot's
        own voice (or a real sentence we can't act on) — discard it and wait
        for a silence gap before re-arming."""
        if self._is_speech(chunk):
            if self._spot_overlong:
                return
            self._spot_frames.append(chunk.copy())
            self._spot_silence = 0
            if len(self._spot_frames) > self._spot_max_frames:
                self._spot_frames  = []
                self._spot_overlong = True
            return
        # silence chunk
        if self._spot_overlong:
            self._spot_overlong = False   # gap over — re-arm
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
        if self._is_speech(chunk):
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
                        # VAD confirmed end-of-utterance. The user actually stopped
                        # talking ~silence_timeout earlier — harness subtracts it.
                        self._emit_timing('stt_vad_end',
                                          speech_s=round(len(audio) / self._sample_rate, 2),
                                          silence_timeout=self._silence_timeout)
                        self._transcription_queue.put_nowait(('user', audio))
                self._speech_frames  = []
                self._silence_frames = 0
                self._is_recording   = False

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
        """Keyword check for a short burst heard during TTS playback."""
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
            self._stop_pub.publish(String(data=text))
        elif words:
            self.get_logger().debug(f'Spot segment ignored: "{text}"')

    def _transcribe(self, audio: np.ndarray):
        try:
            text = self._backend.transcribe(audio, self._sample_rate)
            if not text:
                return
            if self._wake_enabled:
                attention = time.time() < self._attention_until
                forward, out = wake_gate(text, self._wake_aliases, attention)
                if not forward:
                    # Room chatter not addressed to the robot. Log the text so
                    # missed wake words show up (tune wake_aliases from these).
                    self.get_logger().info(f'Not addressed to me — ignored: "{text}"')
                    self._emit_timing('wake_ignored', chars=len(text))
                    return
                # Addressed (or in-conversation) — keep the window open.
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
