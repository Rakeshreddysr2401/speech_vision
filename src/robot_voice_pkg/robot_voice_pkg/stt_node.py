import threading
import numpy as np
import pyaudio
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool
from .vad_utils import VADBuffer


class STTNode(Node):
    def __init__(self):
        super().__init__("stt_node")

        self.declare_parameter("model_size", "base.en")
        self.declare_parameter("device", "cuda")
        self.declare_parameter("compute_type", "float16")
        self.declare_parameter("sample_rate", 16000)
        self.declare_parameter("vad_aggressiveness", 2)
        self.declare_parameter("language", "en")
        self.declare_parameter("input_device_index", 0)

        model_size   = self.get_parameter("model_size").value
        device       = self.get_parameter("device").value
        compute_type = self.get_parameter("compute_type").value
        self._rate   = self.get_parameter("sample_rate").value
        self._lang   = self.get_parameter("language").value
        self._device_index = self.get_parameter("input_device_index").value

        from faster_whisper import WhisperModel
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.get_logger().info(f"Whisper loaded: {model_size} on {device}")

        self._vad = VADBuffer(
            sample_rate=self._rate,
            aggressiveness=self.get_parameter("vad_aggressiveness").value,
        )

        self._muted = False  # True while TTS is speaking

        self._pub_input    = self.create_publisher(String, "/voice/user_input", 10)
        self._pub_listening = self.create_publisher(Bool, "/voice/stt_listening", 1)
        self.create_subscription(Bool, "/voice/tts_speaking", self._on_tts_speaking, 1)

        self._thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._thread.start()
        self.get_logger().info("STT node ready — listening")

    def _on_tts_speaking(self, msg: Bool):
        self._muted = msg.data

    def _mic_loop(self):
        pa = pyaudio.PyAudio()
        # stream = pa.open(
        #     format=pyaudio.paInt16,
        #     channels=1,
        #     rate=self._rate,
        #     input=True,
        #     frames_per_buffer=self._vad.frame_bytes // 2,
        # )
        self.get_logger().info(f"Opening mic device {self._device_index if self._device_index != -1 else 'DEFAULT'}")
        
        try:
            # Try 1 channel first
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._rate,
                input=True,
                input_device_index=None if self._device_index == -1 else self._device_index,
                frames_per_buffer=self._vad.frame_bytes // 2,
            )
            self._is_stereo = False
            self.get_logger().info("Using 1 channel (Mono)")
        except Exception:
            # Fallback to 2 channels
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=self._rate,
                input=True,
                input_device_index=None if self._device_index == -1 else self._device_index,
                frames_per_buffer=self._vad.frame_bytes // 2,
            )
            self._is_stereo = True
            self.get_logger().info("Using 2 channels (Stereo fallback)")

        try:
            while rclpy.ok():
                raw = stream.read(self._vad.frame_bytes // 2, exception_on_overflow=False)
                if self._muted:
                    continue
                
                # Convert to mono if needed
                if self._is_stereo:
                    frame_data = np.frombuffer(raw, dtype=np.int16)
                    if frame_data.size > 0:
                        mono_frame = frame_data.reshape(-1, 2).mean(axis=1).astype(np.int16).tobytes()
                    else:
                        continue
                else:
                    mono_frame = raw
                    
                self._pub_listening.publish(Bool(data=True))
                utterance = self._vad.process_frame(mono_frame)
                if utterance:
                    self._pub_listening.publish(Bool(data=False))
                    self._transcribe(utterance)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def _transcribe(self, pcm: bytes):
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language=self._lang, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            self.get_logger().info(f"STT: {text}")
            self._pub_input.publish(String(data=text))
            self._pub_echo.publish(String(data=text))


def main(args=None):
    rclpy.init(args=args)
    node = STTNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
