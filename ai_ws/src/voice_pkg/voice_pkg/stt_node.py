import threading
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_device import find_input_device, list_devices
from voice_pkg.audio_capture import AudioCapture
from voice_pkg.stt_backend import load_stt_backend

_RMS_THRESHOLD = 0.01  # below this level is considered silence


class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        # ── Audio params
        self.declare_parameter('mic_preference', 'auto')
        self.declare_parameter('silence_timeout', 1.5)

        # ── Backend selection
        self.declare_parameter('stt_backend', 'faster_whisper')

        # ── faster_whisper params (ignored when using a different backend)
        self.declare_parameter('model', 'small')
        self.declare_parameter('language', 'en')
        self.declare_parameter('device', 'cuda')
        self.declare_parameter('compute_type', 'float16')
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_filter', True)

        mic_pref        = self.get_parameter('mic_preference').value
        silence_timeout = self.get_parameter('silence_timeout').value
        backend_name    = self.get_parameter('stt_backend').value

        self._pub = self.create_publisher(String, '/voice/user_input', 10)
        self._tts_speaking = False
        self._listening    = False
        self._lock         = threading.Lock()

        self.create_subscription(Bool, '/voice/tts_speaking',  self._tts_cb,  10)
        self.create_subscription(Bool, '/voice/wake_detected', self._wake_cb, 10)

        # ── Load STT backend
        backend_kwargs = {
            'faster_whisper': dict(
                model        = self.get_parameter('model').value,
                device       = self.get_parameter('device').value,
                compute_type = self.get_parameter('compute_type').value,
                language     = self.get_parameter('language').value,
                beam_size    = self.get_parameter('beam_size').value,
                vad_filter   = self.get_parameter('vad_filter').value,
            ),
        }.get(backend_name, {})

        self._backend = load_stt_backend(backend_name, **backend_kwargs)
        self.get_logger().info(f'STT backend: {backend_name}')

        # ── Audio capture
        self.get_logger().info(list_devices())
        device_idx, device_name = find_input_device(mic_pref)
        self.get_logger().info(f'Mic selected: {device_name} (idx={device_idx})')

        sample_rate      = 16000
        chunk_frames     = 1024
        self._sample_rate = sample_rate
        silence_limit    = int(silence_timeout * sample_rate / chunk_frames)

        self._capture = AudioCapture(device_idx=device_idx, sample_rate=sample_rate,
                                     chunk_frames=chunk_frames)
        self._capture.start()

        self._buffer         = []
        self._silence_frames = 0
        self._silence_limit  = silence_limit

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.get_logger().info('STT ready')

    # ── ROS callbacks ───────────────────────────────────────────────────────

    def _tts_cb(self, msg: Bool):
        self._tts_speaking = msg.data

    def _wake_cb(self, msg: Bool):
        if msg.data and not self._tts_speaking:
            with self._lock:
                self._listening      = True
                self._buffer         = []
                self._silence_frames = 0
            self.get_logger().info('Listening for speech...')

    # ── Audio loop ──────────────────────────────────────────────────────────

    def _run(self):
        while rclpy.ok():
            chunk = self._capture.read(timeout=1.0)
            if chunk is None or self._tts_speaking:
                continue

            with self._lock:
                if not self._listening:
                    continue
                self._ingest(chunk)

    def _ingest(self, chunk: np.ndarray):
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) / 32768.0

        if rms > _RMS_THRESHOLD:
            self._buffer.append(chunk.copy())
            self._silence_frames = 0
        elif self._buffer:
            self._silence_frames += 1
            if self._silence_frames >= self._silence_limit:
                audio = np.concatenate(self._buffer).astype(np.float32) / 32768.0
                self._buffer         = []
                self._silence_frames = 0
                self._listening      = False
                threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

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
