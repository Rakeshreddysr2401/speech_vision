import time
import threading
import numpy as np
import torch
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge


class DepthNode(Node):
    """Monocular depth estimation using Depth Anything V2 Small."""

    def __init__(self):
        super().__init__("depth_node")

        self.declare_parameter("model_size", "Small")
        self.declare_parameter("input_size", 518)
        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("device", "cuda")
        self.declare_parameter("max_depth_m", 10.0)

        model_size = self.get_parameter("model_size").value
        self._input_size = self.get_parameter("input_size").value
        rate = self.get_parameter("publish_rate_hz").value
        device = self.get_parameter("device").value
        self._max_depth = self.get_parameter("max_depth_m").value

        self._device = torch.device(device)
        self._min_interval = 1.0 / rate
        self._last_inference = 0.0
        self._bridge = CvBridge()
        self._lock = threading.Lock()

        self.get_logger().info(f"Loading Depth Anything V2 {model_size}...")
        self._model = torch.hub.load(
            "DepthAnything/Depth-Anything-V2",
            f"depth_anything_v2_{model_size.lower()}",
            trust_repo=True,
        )
        self._model = self._model.to(self._device).eval()
        self.get_logger().info("Depth model loaded")

        self._pub_depth = self.create_publisher(Image, "/vision/depth", 1)

        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(Image, "/vision/image_raw", self._on_image, image_qos)
        self.get_logger().info(f"Depth node ready — publishing at {rate} Hz")

    def _on_image(self, msg: Image):
        now = time.monotonic()
        if now - self._last_inference < self._min_interval:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._last_inference = now
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            depth = self._infer(frame)

            depth_msg = self._bridge.cv2_to_imgmsg(depth, encoding="32FC1")
            depth_msg.header = msg.header
            self._pub_depth.publish(depth_msg)
        except Exception as e:
            self.get_logger().error(f"Depth error: {e}")
        finally:
            self._lock.release()

    @torch.no_grad()
    def _infer(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        depth = self._model.infer_image(rgb, input_size=self._input_size)

        depth = cv2.resize(depth, (w, h), interpolation=cv2.INTER_LINEAR)

        if depth.max() > 0:
            depth = depth / depth.max() * self._max_depth

        return depth.astype(np.float32)


def main(args=None):
    rclpy.init(args=args)
    node = DepthNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
