import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
import threading
import sounddevice as sd
import numpy as np
from kokoro import KPipeline


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)
        self.declare_parameter('device', 'cuda')
        self.declare_parameter('sample_rate', 22050)

        self._voice = self.get_parameter('voice').value
        self._speed = self.get_parameter('speed').value
        self._sample_rate = self.get_parameter('sample_rate').value

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self.create_subscription(String, '/voice/robot_speech', self._speech_cb, 10)

        self._pipeline = KPipeline(lang_code='a')
        self._lock = threading.Lock()

        self.get_logger().info(f'TTS ready — Kokoro voice={self._voice}')

    def _speech_cb(self, msg: String):
        text = msg.data.strip()
        if not text:
            return
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str):
        with self._lock:
            self._set_speaking(True)
            try:
                generator = self._pipeline(text, voice=self._voice, speed=self._speed)
                for _, _, audio in generator:
                    if audio is not None:
                        sd.play(audio, samplerate=self._sample_rate)
                        sd.wait()
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
