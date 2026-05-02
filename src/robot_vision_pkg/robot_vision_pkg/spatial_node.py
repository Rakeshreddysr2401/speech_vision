import json
import math
import threading
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from vision_msgs.msg import Detection2DArray
from std_msgs.msg import String
from cv_bridge import CvBridge
import message_filters


class SpatialNode(Node):
    """Fuses 2D detections with depth to estimate 3D positions."""

    def __init__(self):
        super().__init__("spatial_node")

        self.declare_parameter("camera_fov_h_deg", 60.0)
        self.declare_parameter("camera_fov_v_deg", 45.0)
        self.declare_parameter("depth_patch_size", 10)
        self.declare_parameter("use_tracks", True)
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)

        self._fov_h = math.radians(self.get_parameter("camera_fov_h_deg").value)
        self._fov_v = math.radians(self.get_parameter("camera_fov_v_deg").value)
        self._patch  = self.get_parameter("depth_patch_size").value
        use_tracks   = self.get_parameter("use_tracks").value
        self._img_w  = self.get_parameter("image_width").value
        self._img_h  = self.get_parameter("image_height").value

        self._bridge = CvBridge()

        det_topic = "/vision/tracks" if use_tracks else "/vision/detections"

        det_sub = message_filters.Subscriber(self, Detection2DArray, det_topic)
        depth_sub = message_filters.Subscriber(self, Image, "/vision/depth")

        self._sync = message_filters.ApproximateTimeSynchronizer(
            [det_sub, depth_sub], queue_size=5, slop=0.15
        )
        self._sync.registerCallback(self._on_sync)

        self._pub = self.create_publisher(String, "/vision/objects_3d", 5)

        self._latest_depth: np.ndarray | None = None
        self._depth_lock = threading.Lock()
        self.create_subscription(Image, "/vision/depth", self._cache_depth, 1)

        self.create_subscription(
            Detection2DArray, det_topic, self._on_det_only, 5
        )

        self.get_logger().info(
            f"Spatial node ready — subscribing to {det_topic} + /vision/depth"
        )

    def _cache_depth(self, msg: Image):
        depth = self._bridge.imgmsg_to_cv2(msg, desired_encoding="32FC1")
        with self._depth_lock:
            self._latest_depth = depth

    def _on_det_only(self, msg: Detection2DArray):
        """Fallback when depth is not available — publish detections without distance."""
        pass

    def _on_sync(self, det_msg: Detection2DArray, depth_msg: Image):
        depth = self._bridge.imgmsg_to_cv2(depth_msg, desired_encoding="32FC1")
        objects = self._fuse(det_msg, depth)
        self._pub.publish(String(data=json.dumps(objects)))

    def _fuse(self, det_msg: Detection2DArray, depth: np.ndarray) -> list[dict]:
        h, w = depth.shape[:2]
        objects = []

        for det in det_msg.detections:
            cx = det.bbox.center.position.x
            cy = det.bbox.center.position.y
            bw = det.bbox.size_x
            bh = det.bbox.size_y

            cls_id = det.results[0].hypothesis.class_id if det.results else "unknown"
            score = det.results[0].hypothesis.score if det.results else 0.0
            track_id = det.id if det.id else None

            px = int(min(max(cx, 0), w - 1))
            py = int(min(max(cy, 0), h - 1))
            half = self._patch // 2
            x1 = max(0, px - half)
            x2 = min(w, px + half + 1)
            y1 = max(0, py - half)
            y2 = min(h, py + half + 1)

            patch = depth[y1:y2, x1:x2]
            valid = patch[patch > 0]
            distance_m = float(np.median(valid)) if len(valid) > 0 else None

            angle_h = math.degrees((cx - w / 2) / (w / 2) * (self._fov_h / 2))
            angle_v = math.degrees((cy - h / 2) / (h / 2) * (self._fov_v / 2))

            direction = self._angle_to_direction(angle_h)

            obj = {
                "class": cls_id,
                "confidence": round(score, 2),
                "distance_m": round(distance_m, 2) if distance_m else None,
                "angle_h_deg": round(angle_h, 1),
                "angle_v_deg": round(angle_v, 1),
                "direction": direction,
                "bbox": {
                    "cx": round(cx, 1), "cy": round(cy, 1),
                    "w": round(bw, 1), "h": round(bh, 1),
                },
            }
            if track_id:
                obj["track_id"] = int(track_id)

            objects.append(obj)

        return objects

    @staticmethod
    def _angle_to_direction(angle_h: float) -> str:
        if angle_h < -20:
            return "left"
        elif angle_h > 20:
            return "right"
        else:
            return "center"


def main(args=None):
    rclpy.init(args=args)
    node = SpatialNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
