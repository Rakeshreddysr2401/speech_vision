import threading
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from openwakeword.model import Model

from voice_pkg.audio_device import find_input_device, list_devices
from voice_pkg.audio_capture import AudioCapture


class WakeWordNode(Node):
    def __init__(self):
        super().__init__('wakeword_node')

        self.declare_parameter('wake_word', 'hey_jarvis')
        self.declare_parameter('threshold', 0.5)
        self.declare_parameter('chunk_size', 1280)
        self.declare_parameter('mic_preference', 'auto')  # auto | bluetooth | usb | <substring>

        wake_word  = self.get_parameter('wake_word').value
        threshold  = self.get_parameter('threshold').value
        chunk_size = self.get_parameter('chunk_size').value
        mic_pref   = self.get_parameter('mic_preference').value

        self._threshold = threshold
        self._wake_word = wake_word

        self._pub = self.create_publisher(Bool, '/voice/wake_detected', 10)

        self.get_logger().info(list_devices())
        device_idx, device_name = find_input_device(mic_pref)
        self.get_logger().info(f'Mic selected: {device_name} (idx={device_idx}, pref="{mic_pref}")')

        self._model = Model(wakeword_models=[wake_word], inference_framework='onnx')

        self._capture = AudioCapture(device_idx=device_idx, sample_rate=16000,
                                     chunk_frames=chunk_size)
        self._capture.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.get_logger().info(f'Listening for wake word: "{wake_word}" (threshold={threshold})')

    def _run(self):
        while rclpy.ok():
            chunk = self._capture.read(timeout=1.0)
            if chunk is None:
                continue
            self._model.predict(chunk)
            score = self._model.prediction_buffer.get(self._wake_word, [0])[-1]
            if score >= self._threshold:
                self.get_logger().info(f'Wake word detected (score={score:.2f})')
                msg = Bool()
                msg.data = True
                self._pub.publish(msg)

    def destroy_node(self):
        self._capture.stop()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = WakeWordNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
