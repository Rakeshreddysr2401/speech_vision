import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
import pyaudio
import numpy as np
import threading
from faster_whisper import WhisperModel


class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        self.declare_parameter('model', 'small')
        self.declare_parameter('language', 'en')
        self.declare_parameter('device', 'cuda')
        self.declare_parameter('compute_type', 'float16')
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_filter', True)
        self.declare_parameter('silence_timeout', 1.5)

        model_size = self.get_parameter('model').value
        self._language = self.get_parameter('language').value
        self._device = self.get_parameter('device').value
        self._compute_type = self.get_parameter('compute_type').value
        self._beam_size = self.get_parameter('beam_size').value
        self._vad_filter = self.get_parameter('vad_filter').value
        self._silence_timeout = self.get_parameter('silence_timeout').value

        self._pub = self.create_publisher(String, '/voice/user_input', 10)

        # Suppress STT while TTS is speaking
        self._tts_speaking = False
        self.create_subscription(Bool, '/voice/tts_speaking', self._tts_cb, 10)

        # Triggered by wake word
        self._listening = False
        self.create_subscription(Bool, '/voice/wake_detected', self._wake_cb, 10)

        self._model = WhisperModel(
            model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

        self._audio = pyaudio.PyAudio()
        self._buffer = []
        self._silence_frames = 0
        self._frames_per_chunk = 1024
        self._sample_rate = 16000
        # frames of silence before finalising
        self._silence_limit = int(self._silence_timeout * self._sample_rate / self._frames_per_chunk)

        self._stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._sample_rate,
            input=True,
            frames_per_buffer=self._frames_per_chunk,
        )

        self._lock = threading.Lock()
        self.create_timer(0.064, self._process_audio)
        self.get_logger().info(f'STT ready — Whisper {model_size} on {self._device}')

    def _tts_cb(self, msg: Bool):
        self._tts_speaking = msg.data

    def _wake_cb(self, msg: Bool):
        if msg.data and not self._tts_speaking:
            with self._lock:
                self._listening = True
                self._buffer = []
                self._silence_frames = 0
            self.get_logger().info('Listening for speech...')

    def _process_audio(self):
        if self._tts_speaking:
            return

        try:
            raw = self._stream.read(self._frames_per_chunk, exception_on_overflow=False)
        except OSError:
            return

        with self._lock:
            if not self._listening:
                return

            audio_chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(audio_chunk ** 2)))

            if rms > 0.01:
                self._buffer.append(raw)
                self._silence_frames = 0
            else:
                if self._buffer:
                    self._silence_frames += 1

                if self._silence_frames >= self._silence_limit and self._buffer:
                    audio_data = np.frombuffer(b''.join(self._buffer), dtype=np.int16).astype(np.float32) / 32768.0
                    self._buffer = []
                    self._silence_frames = 0
                    self._listening = False
                    threading.Thread(target=self._transcribe, args=(audio_data,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray):
        try:
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
            )
            text = ' '.join(s.text.strip() for s in segments).strip()
            if text:
                self.get_logger().info(f'Transcribed: "{text}"')
                msg = String()
                msg.data = text
                self._pub.publish(msg)
        except Exception as e:
            self.get_logger().error(f'Transcription error: {e}')

    def destroy_node(self):
        self._stream.stop_stream()
        self._stream.close()
        self._audio.terminate()
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
