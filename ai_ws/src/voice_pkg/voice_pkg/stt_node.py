import queue
import threading
import numpy as np
import webrtcvad
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_capture import make_capture
from voice_pkg.audio_device import find_input_device, list_devices
from voice_pkg.stt_backend import load_stt_backend

# WebRTC VAD requires 20ms frames: 320 samples at 16kHz
_VAD_FRAME_SAMPLES = 320


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
        self._lock = threading.Lock()

        self._tts_speaking   = False
        self._is_recording   = False
        self._speech_frames  = []
        self._silence_frames = 0

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
            self._tts_speaking = msg.data
            if msg.data:
                self._is_recording   = False
                self._speech_frames  = []
                self._silence_frames = 0

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
                    continue
                self._process(chunk)

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
                        self._transcription_queue.put_nowait(audio)
                self._speech_frames  = []
                self._silence_frames = 0
                self._is_recording   = False

    def _transcription_worker(self):
        while True:
            try:
                audio = self._transcription_queue.get(timeout=1.0)
                self._transcribe(audio)
            except queue.Empty:
                continue

    def _transcribe(self, audio: np.ndarray):
        try:
            text = self._backend.transcribe(audio, self._sample_rate)
            if text:
                self.get_logger().info(f'Transcribed: "{text}"')
                msg = String()
                msg.data = text
                self._pub.publish(msg)
        except Exception as e:
            self.get_logger().error(f'Transcription error: {e}')

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
