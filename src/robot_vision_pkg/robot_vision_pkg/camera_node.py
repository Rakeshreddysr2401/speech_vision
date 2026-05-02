import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge


class CameraNode(Node):
    def __init__(self):
        super().__init__("camera_node")

        self.declare_parameter("device_id", 0)
        self.declare_parameter("width", 640)
        self.declare_parameter("height", 480)
        self.declare_parameter("fps", 30)
        self.declare_parameter("camera_type", "usb")  # "usb" or "csi"

        dev   = self.get_parameter("device_id").value
        w     = self.get_parameter("width").value
        h     = self.get_parameter("height").value
        fps   = self.get_parameter("fps").value
        ctype = self.get_parameter("camera_type").value

        self._bridge = CvBridge()

        if ctype == "csi":
            gst = (
                f"nvarguscamerasrc sensor-id={dev} ! "
                f"video/x-raw(memory:NVMM), width={w}, height={h}, "
                f"framerate={fps}/1, format=NV12 ! "
                f"nvvidconv ! video/x-raw, format=BGRx ! "
                f"videoconvert ! video/x-raw, format=BGR ! appsink"
            )
            self._cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        else:
            self._cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            self._cap.set(cv2.CAP_PROP_FPS, fps)

        if not self._cap.isOpened():
            raise RuntimeError(
                f"Cannot open {ctype} camera device {dev}. "
                f"Check /dev/video* or CSI connection."
            )

        self._pub = self.create_publisher(Image, "/vision/image_raw", 1)
        self.create_timer(1.0 / fps, self._capture)
        self.get_logger().info(
            f"Camera node ready — {ctype} device {dev} {w}x{h} @{fps}fps"
        )

    def _capture(self):
        ok, frame = self._cap.read()
        if ok:
            msg = self._bridge.cv2_to_imgmsg(frame, encoding="bgr8")
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "camera_link"
            self._pub.publish(msg)

    def destroy_node(self):
        self._cap.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
