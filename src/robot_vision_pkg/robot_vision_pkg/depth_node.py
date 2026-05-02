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

from transformers import pipeline
from PIL import Image as PILImage

class DepthNode(Node):
    """Monocular depth estimation using Depth Anything V2."""

    def __init__(self):
        super().__init__("depth_node")

        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("device", "cuda")
        self.declare_parameter("max_depth_m", 10.0)

        rate = self.get_parameter("publish_rate_hz").value
        device = self.get_parameter("device").value
        self._max_depth = self.get_parameter("max_depth_m").value

        self._device = device
        self._min_interval = 1.0 / rate
        self._last_inference = 0.0

        self._bridge = CvBridge()
        self._lock = threading.Lock()

        self.get_logger().info("Loading Depth Anything V2 pipeline...")

        self._pipe = pipeline(
            task="depth-estimation",
            model="depth-anything/Depth-Anything-V2-Small-hf",
            device=0 if device.startswith("cuda") else -1,
        )

        self.get_logger().info("Depth model loaded")

        self._pub_depth = self.create_publisher(
            Image,
            "/vision/depth",
            1,
        )

        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self.create_subscription(
            Image,
            "/vision/image_raw",
            self._on_image,
            image_qos,
        )

        self.get_logger().info(
            f"Depth node ready — publishing at {rate} Hz"
        )

    def _on_image(self, msg: Image):
        now = time.monotonic()

        if now - self._last_inference < self._min_interval:
            return

        if not self._lock.acquire(blocking=False):
            return

        try:
            self._last_inference = now

            frame = self._bridge.imgmsg_to_cv2(
                msg,
                desired_encoding="bgr8",
            )

            depth = self._infer(frame)

            depth_msg = self._bridge.cv2_to_imgmsg(
                depth,
                encoding="32FC1",
            )

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

        pil_img = PILImage.fromarray(rgb)

        output = self._pipe(pil_img)

        depth = np.array(output["depth"])

        depth = cv2.resize(
            depth,
            (w, h),
            interpolation=cv2.INTER_LINEAR,
        )

        depth = depth.astype(np.float32)

        if depth.max() > 0:
            # depth = depth / depth.max() * self._max_depth
            depth = (1.0 - depth / depth.max()) * self._max_depth

        return depth



    # @torch.no_grad()
    # def _infer(self, frame: np.ndarray) -> np.ndarray:

    #     h, w = frame.shape[:2]

    #     rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    #     output = self._pipe(rgb)

    #     depth = np.array(output["depth"])

    #     depth = cv2.resize(
    #         depth,
    #         (w, h),
    #         interpolation=cv2.INTER_LINEAR,
    #     )

    #     depth = depth.astype(np.float32)

    #     if depth.max() > 0:
    #         depth = depth / depth.max() * self._max_depth

    #     return depth


def main(args=None):

    rclpy.init(args=args)

    node = DepthNode()

    rclpy.spin(node)

    node.destroy_node()

    rclpy.shutdown()

