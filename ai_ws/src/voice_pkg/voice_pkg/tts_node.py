import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_device import find_output_device, list_devices
from voice_pkg.tts_backend import load_tts_backend


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        # ── Audio params
        self.declare_parameter('speaker_preference', 'auto')  # auto | bluetooth | usb | <substring>
        self.declare_parameter('sample_rate', 22050)

        # ── Backend selection
        self.declare_parameter('tts_backend', 'kokoro')

        # ── Kokoro params (ignored when using a different backend)
        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)

        speaker_pref = self.get_parameter('speaker_preference').value
        sample_rate  = self.get_parameter('sample_rate').value
        backend_name = self.get_parameter('tts_backend').value

        # ── Load TTS backend
        backend_kwargs = {
            'kokoro': dict(
                voice = self.get_parameter('voice').value,
                speed = self.get_parameter('speed').value,
            ),
        }.get(backend_name, {})

        self._backend     = load_tts_backend(backend_name, **backend_kwargs)
        self._sample_rate = sample_rate
        self._lock        = threading.Lock()

        self.get_logger().info(f'TTS backend: {backend_name}')

        # ── Output device
        self.get_logger().info(list_devices())
        self._output_idx, output_name = find_output_device(speaker_pref)
        self.get_logger().info(
            f'Speaker selected: {output_name} (idx={self._output_idx}, pref="{speaker_pref}")')

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self.create_subscription(String, '/voice/robot_speech', self._speech_cb, 10)

        self.get_logger().info('TTS ready')

    def _speech_cb(self, msg: String):
        text = msg.data.strip()
        if not text:
            return
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str):
        with self._lock:
            self._set_speaking(True)
            try:
                self._backend.speak(text, self._output_idx, self._sample_rate)
            except Exception as e:
                self.get_logger().error(f'TTS error: {e}')
            finally:
                self._set_speaking(False)

    def _set_speaking(self, state: bool):
        msg = Bool()
        msg.data = state
        self._speaking_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = TTSNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
