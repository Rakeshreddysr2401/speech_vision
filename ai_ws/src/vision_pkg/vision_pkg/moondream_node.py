import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge
import threading
import nano_llm
from PIL import Image as PILImage
import numpy as np


class MoondreamNode(Node):
    def __init__(self):
        super().__init__('moondream_node')

        self.declare_parameter('model', 'vikhyatk/moondream2')
        self.declare_parameter('revision', '2025-01-09')
        self.declare_parameter('backend', 'nano_llm')
        self.declare_parameter('image_topic', '/camera/color/image_raw')

        model_id = self.get_parameter('model').value
        revision = self.get_parameter('revision').value
        image_topic = self.get_parameter('image_topic').value

        self._bridge = CvBridge()
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._query_lock = threading.Lock()

        # NanoLLM loads Moondream2 with MLC INT4 quantization (~0.8GB VRAM)
        self.get_logger().info(f'Loading Moondream2 via NanoLLM MLC INT4...')
        self._model = nano_llm.NanoLLM.from_pretrained(
            model_id,
            revision=revision,
            api='mlc',
            quantization='q4f16_ft',
        )
        self.get_logger().info('Moondream2 ready')

        self._pub = self.create_publisher(String, '/vision/query_result', 10)
        self.create_subscription(Image, image_topic, self._image_cb, 10)
        self.create_subscription(String, '/vision/query', self._query_cb, 10)

    def _image_cb(self, msg: Image):
        try:
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            with self._frame_lock:
                self._latest_frame = frame
        except Exception:
            pass

    def _query_cb(self, msg: String):
        question = msg.data.strip()
        if not question:
            return
        with self._frame_lock:
            frame = self._latest_frame.copy() if self._latest_frame is not None else None
        if frame is None:
            self.get_logger().warn('Query received but no camera frame yet')
            return
        threading.Thread(target=self._answer, args=(question, frame), daemon=True).start()

    def _answer(self, question: str, frame: np.ndarray):
        with self._query_lock:
            try:
                pil_img = PILImage.fromarray(frame)
                chat = nano_llm.ChatHistory(self._model)
                chat.append(role='user', image=pil_img)
                chat.append(role='user', text=question)
                embedding, _ = chat.embed_chat()
                reply = self._model.generate(embedding, streaming=False, max_new_tokens=256)
                answer = reply.strip()
                self.get_logger().info(f'Q: "{question}" → A: "{answer}"')
                result = String()
                result.data = answer
                self._pub.publish(result)
            except Exception as e:
                self.get_logger().error(f'Moondream error: {e}')


def main(args=None):
    rclpy.init(args=args)
    node = MoondreamNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
