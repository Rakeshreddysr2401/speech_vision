import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool


class TTSNode(Node):
    def __init__(self):
        super().__init__("tts_node")

        self.declare_parameter("engine", "kokoro")

        # Kokoro params
        self.declare_parameter("kokoro.model", "af_heart")
        self.declare_parameter("kokoro.speed", 1.0)
        self.declare_parameter("kokoro.device", "cuda")
        self.declare_parameter("kokoro.sample_rate", 24000)
        self.declare_parameter("kokoro.lang", "a")

        # Piper params
        self.declare_parameter("piper.model_path", "/models/piper/en_US-lessac-medium.onnx")
        self.declare_parameter("piper.config_path", "/models/piper/en_US-lessac-medium.onnx.json")
        self.declare_parameter("piper.speed", 1.0)
        self.declare_parameter("piper.sample_rate", 22050)
        self.declare_parameter("piper.piper_bin", "piper")

        self._engine_name = self.get_parameter("engine").value
        self._backend = self._load_backend(self._engine_name)

        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._event = threading.Event()

        self._pub_speaking = self.create_publisher(Bool, "/voice/tts_speaking", 1)
        self._pub_backend  = self.create_publisher(String, "/voice/tts_backend_active", 1)
        self.create_subscription(String, "/voice/robot_speech", self._on_speech, 10)

        self._pub_backend.publish(String(data=self._engine_name))
        self.get_logger().info(f"TTS node ready — engine: {self._engine_name}")

        self._worker = threading.Thread(target=self._speak_loop, daemon=True)
        self._worker.start()

    def _load_backend(self, engine: str):
        if engine == "kokoro":
            from .backends.tts_kokoro import KokoroBackend
            params = {
                "model":       self.get_parameter("kokoro.model").value,
                "speed":       self.get_parameter("kokoro.speed").value,
                "device":      self.get_parameter("kokoro.device").value,
                "sample_rate": self.get_parameter("kokoro.sample_rate").value,
                "lang":        self.get_parameter("kokoro.lang").value,
            }
            return KokoroBackend(params)
        elif engine == "piper":
            from .backends.tts_piper import PiperBackend
            params = {
                "model_path":  self.get_parameter("piper.model_path").value,
                "config_path": self.get_parameter("piper.config_path").value,
                "speed":       self.get_parameter("piper.speed").value,
                "sample_rate": self.get_parameter("piper.sample_rate").value,
                "piper_bin":   self.get_parameter("piper.piper_bin").value,
            }
            return PiperBackend(params)
        else:
            raise ValueError(f"Unknown TTS engine: {engine!r}. Use 'kokoro' or 'piper'.")

    def _on_speech(self, msg: String):
        text = msg.data.strip()
        if text:
            with self._lock:
                self._queue.append(text)
            self._event.set()

    def _speak_loop(self):
        while rclpy.ok():
            self._event.wait(timeout=1.0)
            self._event.clear()
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    text = self._queue.pop(0)
                self._pub_speaking.publish(Bool(data=True))
                try:
                    self._backend.speak(text)
                except Exception as e:
                    self.get_logger().error(f"TTS error: {e}")
                finally:
                    self._pub_speaking.publish(Bool(data=False))


def main(args=None):
    rclpy.init(args=args)
    node = TTSNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
