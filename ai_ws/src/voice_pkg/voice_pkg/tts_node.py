import queue
import threading
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_device import find_output_device, list_devices
from voice_pkg.tts_backend import load_tts_backend

_POST_SPEECH_SILENCE = 0.4   # seconds to keep mic muted after speech ends


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        self.declare_parameter('speaker_preference', 'auto')
        self.declare_parameter('sample_rate', 22050)
        self.declare_parameter('tts_backend', 'kokoro')
        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)

        speaker_pref = self.get_parameter('speaker_preference').value
        sample_rate  = self.get_parameter('sample_rate').value
        backend_name = self.get_parameter('tts_backend').value

        backend_kwargs = {
            'kokoro': dict(
                voice = self.get_parameter('voice').value,
                speed = self.get_parameter('speed').value,
            ),
        }.get(backend_name, {})

        self._backend     = load_tts_backend(backend_name, **backend_kwargs)
        self._sample_rate = sample_rate

        self.get_logger().info(f'TTS backend: {backend_name}')

        self.get_logger().info(list_devices())
        self._output_idx, output_name = find_output_device(speaker_pref)
        self.get_logger().info(
            f'Speaker: {output_name} (idx={self._output_idx}, pref="{speaker_pref}")')

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self.create_subscription(String, '/voice/robot_speech', self._speech_cb, 10)

        # Single worker thread + bounded queue — drops oldest if full
        self._queue: queue.Queue[str] = queue.Queue(maxsize=3)
        threading.Thread(target=self._worker, daemon=True).start()

        self.get_logger().info('TTS ready')

    def _speech_cb(self, msg: String):
        text = msg.data.strip()
        if not text:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()   # drop oldest to make room
            except queue.Empty:
                pass
        self._queue.put_nowait(text)

    def _worker(self):
        while True:
            text = self._queue.get()
            self._set_speaking(True)
            self.get_logger().info(f'Speaking: "{text}"')
            try:
                self._backend.speak(text, self._output_idx, self._sample_rate)
            except Exception as e:
                self.get_logger().error(f'TTS error: {e}')
            finally:
                time.sleep(_POST_SPEECH_SILENCE)
                self._set_speaking(False)
                self.get_logger().info('Done speaking')

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
