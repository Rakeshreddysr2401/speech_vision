import json
import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class SceneNode(Node):
    """Periodically asks Moondream for a scene description,
    enriched with detection context when available."""

    def __init__(self):
        super().__init__("scene_node")

        self.declare_parameter("interval_sec", 10.0)
        self.declare_parameter("prompt", "Describe what you see in one sentence.")
        self.declare_parameter("include_detections", True)

        interval = self.get_parameter("interval_sec").value
        self._prompt = self.get_parameter("prompt").value
        self._include_det = self.get_parameter("include_detections").value

        self._latest_objects: str = ""
        self._det_lock = threading.Lock()

        self._pub_query = self.create_publisher(String, "/vision/query", 10)
        self._pub_scene = self.create_publisher(String, "/vision/scene_description", 10)
        self.create_subscription(String, "/vision/query_result", self._on_result, 10)

        if self._include_det:
            self.create_subscription(
                String, "/vision/objects_3d", self._on_objects, 1
            )

        self.create_timer(interval, self._request_description)
        self.get_logger().info(f"Scene node ready — describing every {interval}s")

    def _on_objects(self, msg: String):
        with self._det_lock:
            self._latest_objects = msg.data

    def _request_description(self):
        prompt = self._prompt
        if self._include_det:
            with self._det_lock:
                raw = self._latest_objects
            if raw:
                try:
                    objects = json.loads(raw)
                    if objects:
                        summary = []
                        for obj in objects:
                            cls = obj.get("class", "object")
                            dist = obj.get("distance_m")
                            dirn = obj.get("direction", "")
                            if dist:
                                summary.append(f"{cls} at {dist}m {dirn}")
                            else:
                                summary.append(f"{cls} {dirn}")
                        prompt = (
                            f"Currently detected: {', '.join(summary)}. "
                            f"{self._prompt}"
                        )
                except (json.JSONDecodeError, TypeError):
                    pass
        self._pub_query.publish(String(data=prompt))

    def _on_result(self, msg: String):
        self._pub_scene.publish(msg)
        self.get_logger().info(f"Scene: {msg.data}")


def main(args=None):
    rclpy.init(args=args)
    node = SceneNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
