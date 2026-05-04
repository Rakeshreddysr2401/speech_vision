import threading
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from vision_msgs.msg import (
    Detection2DArray,
    Detection2D,
    ObjectHypothesisWithPose,
)
from std_msgs.msg import Header
from cv_bridge import CvBridge


class DetectorNode(Node):
    """Real-time object detection using YOLO via Ultralytics CUDA backend."""

    COCO_NAMES = [
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
        "truck", "boat", "traffic light", "fire hydrant", "stop sign",
        "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
        "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
        "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
        "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
        "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
        "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv",
        "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
        "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
        "scissors", "teddy bear", "hair drier", "toothbrush",
    ]

    def __init__(self):
        super().__init__("detector_node")

        self.declare_parameter("model_name", "yolo11n.pt")
        self.declare_parameter("confidence_threshold", 0.5)
        self.declare_parameter("iou_threshold", 0.45)
        self.declare_parameter("input_size", 640)
        self.declare_parameter("device", "cuda:0")
        self.declare_parameter("classes_filter", [])

        model_name = self.get_parameter("model_name").value
        self._conf = self.get_parameter("confidence_threshold").value
        self._iou = self.get_parameter("iou_threshold").value
        self._size = self.get_parameter("input_size").value
        self._device = self.get_parameter("device").value
        self._filter = self.get_parameter("classes_filter").value or None

        self._bridge = CvBridge()
        self._lock = threading.Lock()

        model_path = self._resolve_model(model_name)
        self.get_logger().info(f"Loading YOLO model: {model_path}")

        from ultralytics import YOLO

        self._model = YOLO(model_path)

        self.get_logger().info(
            f"YOLO model loaded with CUDA backend ({self._device})"
        )

        self._pub = self.create_publisher(
            Detection2DArray,
            "/vision/detections",
            5,
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

        self.get_logger().info("Detector node ready")

    def _resolve_model(self, model_name: str) -> str:
        import os
        if os.path.isabs(model_name):
            return model_name
        from ament_index_python.packages import get_package_share_directory
        candidate = os.path.join(
            get_package_share_directory("robot_vision_pkg"), "models", model_name
        )
        return candidate if os.path.exists(candidate) else model_name

    def _on_image(self, msg: Image):
        if not self._lock.acquire(blocking=False):
            return

        try:
            frame = self._bridge.imgmsg_to_cv2(
                msg,
                desired_encoding="bgr8",
            )

            results = self._model.predict(
                frame,
                imgsz=self._size,
                conf=self._conf,
                iou=self._iou,
                classes=self._filter,
                device=self._device,
                verbose=False,
            )

            det_msg = self._build_msg(results[0], msg.header)
            self._pub.publish(det_msg)

        except Exception as e:
            self.get_logger().error(f"Detection error: {e}")

        finally:
            self._lock.release()

    def _build_msg(
        self,
        result,
        header: Header,
    ) -> Detection2DArray:

        msg = Detection2DArray()
        msg.header = header

        if result.boxes is None or len(result.boxes) == 0:
            return msg

        for box, conf, cls_id in zip(
            result.boxes.xyxy.cpu().numpy(),
            result.boxes.conf.cpu().numpy(),
            result.boxes.cls.cpu().numpy(),
        ):

            det = Detection2D()
            det.header = header

            x1, y1, x2, y2 = box

            det.bbox.center.position.x = float((x1 + x2) / 2)
            det.bbox.center.position.y = float((y1 + y2) / 2)
            det.bbox.size_x = float(x2 - x1)
            det.bbox.size_y = float(y2 - y1)

            hyp = ObjectHypothesisWithPose()

            cls_idx = int(cls_id)

            hyp.hypothesis.class_id = (
                self.COCO_NAMES[cls_idx]
                if cls_idx < len(self.COCO_NAMES)
                else str(cls_idx)
            )

            hyp.hypothesis.score = float(conf)

            det.results.append(hyp)

            msg.detections.append(det)

        return msg


def main(args=None):
    rclpy.init(args=args)

    node = DetectorNode()

    rclpy.spin(node)

    node.destroy_node()
    rclpy.shutdown()

