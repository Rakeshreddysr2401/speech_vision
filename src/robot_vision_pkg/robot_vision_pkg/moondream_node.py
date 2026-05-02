import json
import threading
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge
from PIL import Image as PILImage


class MoondreamNode(Node):
    """Answers natural-language queries about the latest camera frame,
    enriched with real-time detection context when available."""

    def __init__(self):
        super().__init__("moondream_node")

        self.declare_parameter("model_id", "vikhyatk/moondream2")
        self.declare_parameter("revision", "2025-01-09")
        self.declare_parameter("device", "cuda")
        self.declare_parameter("max_new_tokens", 256)
        self.declare_parameter("use_detection_context", True)

        model_id  = self.get_parameter("model_id").value
        revision  = self.get_parameter("revision").value
        device    = self.get_parameter("device").value
        self._max_tokens = self.get_parameter("max_new_tokens").value
        self._use_context = self.get_parameter("use_detection_context").value

        self.get_logger().info(f"Loading Moondream: {model_id} rev={revision}")
        import torch
        from transformers import AutoModelForCausalLM
        dtype = torch.float16 if device == "cuda" else torch.float32
        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            revision=revision,
            torch_dtype=dtype,
            device_map={"": device},
        )
        self._model.eval()
        self.get_logger().info("Moondream loaded")

        self._bridge = CvBridge()
        self._latest_frame: PILImage.Image | None = None
        self._frame_lock = threading.Lock()
        self._query_lock = threading.Lock()

        self._latest_detections: str = ""
        self._det_lock = threading.Lock()

        self.create_subscription(Image, "/vision/image_raw", self._on_image, 1)
        self.create_subscription(String, "/vision/query", self._on_query, 10)
        self._pub_result = self.create_publisher(String, "/vision/query_result", 10)

        if self._use_context:
            self.create_subscription(
                String, "/vision/objects_3d", self._on_objects_3d, 1
            )

    def _on_image(self, msg: Image):
        cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
        pil_img = PILImage.fromarray(cv_img)
        with self._frame_lock:
            self._latest_frame = pil_img

    def _on_objects_3d(self, msg: String):
        with self._det_lock:
            self._latest_detections = msg.data

    def _on_query(self, msg: String):
        query = msg.data.strip()
        if not query:
            return
        threading.Thread(target=self._answer, args=(query,), daemon=True).start()

    def _build_query(self, user_query: str) -> str:
        if not self._use_context:
            return user_query
        with self._det_lock:
            raw = self._latest_detections
        if not raw:
            return user_query
        try:
            objects = json.loads(raw)
            if not objects:
                return user_query
            parts = []
            for obj in objects:
                desc = obj.get("class", "object")
                dist = obj.get("distance_m")
                dirn = obj.get("direction", "")
                if dist:
                    parts.append(f"{desc} ({dist}m, {dirn})")
                else:
                    parts.append(f"{desc} ({dirn})")
            context = "Detected objects nearby: " + ", ".join(parts) + ". "
            return context + user_query
        except (json.JSONDecodeError, TypeError):
            return user_query

    def _answer(self, query: str):
        with self._frame_lock:
            frame = self._latest_frame
        if frame is None:
            self.get_logger().warn("Query received but no camera frame available yet")
            self._pub_result.publish(
                String(data="I cannot see anything yet — no camera frame.")
            )
            return
        enriched_query = self._build_query(query)
        with self._query_lock:
            enc = self._model.encode_image(frame)
            answer = self._model.answer_question(enc, enriched_query)
        self.get_logger().info(f"VLM Q: {query!r}  A: {answer!r}")
        self._pub_result.publish(String(data=answer))


def main(args=None):
    rclpy.init(args=args)
    node = MoondreamNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
