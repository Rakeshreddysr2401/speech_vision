import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
import pyaudio
import numpy as np
from openwakeword.model import Model


class WakeWordNode(Node):
    def __init__(self):
        super().__init__('wakeword_node')

        self.declare_parameter('wake_word', 'hey_jarvis')
        self.declare_parameter('threshold', 0.5)
        self.declare_parameter('chunk_size', 1280)

        self._wake_word = self.get_parameter('wake_word').value
        self._threshold = self.get_parameter('threshold').value
        self._chunk_size = self.get_parameter('chunk_size').value

        self._pub = self.create_publisher(Bool, '/voice/wake_detected', 10)

        self._model = Model(wakeword_models=[self._wake_word], inference_framework='onnx')

        self._audio = pyaudio.PyAudio()
        self._stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=self._chunk_size,
        )

        # Poll audio at ~80ms intervals (chunk_size=1280 @ 16kHz)
        self.create_timer(0.08, self._process_audio)
        self.get_logger().info(f'Listening for wake word: {self._wake_word}')

    def _process_audio(self):
        try:
            raw = self._stream.read(self._chunk_size, exception_on_overflow=False)
        except OSError:
            return

        audio = np.frombuffer(raw, dtype=np.int16)
        self._model.predict(audio)

        score = self._model.prediction_buffer.get(self._wake_word, [0])[-1]
        if score >= self._threshold:
            self.get_logger().info(f'Wake word detected (score={score:.2f})')
            msg = Bool()
            msg.data = True
            self._pub.publish(msg)

    def destroy_node(self):
        self._stream.stop_stream()
        self._stream.close()
        self._audio.terminate()
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
