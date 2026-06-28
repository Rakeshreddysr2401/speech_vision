"""USB camera publisher — Logitech (Brio 100) on the Jetson.

Opens a UVC/V4L2 webcam, grabs frames on a background thread (so we always have
the freshest frame, never a buffered-stale one), and republishes the latest frame
on a ROS timer at a throttled rate.

Publishes:
  /camera/color/image_raw            sensor_msgs/Image           (bgr8, raw)
  /camera/color/image_raw/compressed sensor_msgs/CompressedImage (JPEG, optional)

The raw `Image` is the contract the rest of the stack consumes:
  - Pi5 `agent_node` re-encodes it to JPEG for `look()` (Gemma multimodal)
  - `moondream_node` reads it for on-demand VLM queries
  - `detector_node` / isaac_ros_yolov8 reads it for object detection

Defaults to 640x480 @ ~5 fps to keep DDS bandwidth low over the Jetson↔Pi5 link.
The D555 PoE depth camera will later publish these topics natively over SafeDDS;
until then this node is the single source of `/camera/color/image_raw`.
"""

import glob
import threading

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, Image


class CameraNode(Node):
    def __init__(self):
        super().__init__('camera_node')

        # device: integer index ("0"), explicit path ("/dev/video0"), or "" to
        # auto-discover by name substring (device_name).
        self.declare_parameter('device', '')
        self.declare_parameter('device_name', 'Brio')   # substring match when device==""
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 5.0)              # publish rate (Hz)
        self.declare_parameter('frame_id', 'camera_color_optical_frame')
        self.declare_parameter('publish_compressed', True)
        self.declare_parameter('jpeg_quality', 85)
        self.declare_parameter('flip', False)           # rotate 180° if mounted upside-down

        self._device = self.get_parameter('device').value
        self._device_name = self.get_parameter('device_name').value
        self._width = int(self.get_parameter('width').value)
        self._height = int(self.get_parameter('height').value)
        self._fps = float(self.get_parameter('fps').value)
        self._frame_id = self.get_parameter('frame_id').value
        self._publish_compressed = bool(self.get_parameter('publish_compressed').value)
        self._jpeg_quality = int(self.get_parameter('jpeg_quality').value)
        self._flip = bool(self.get_parameter('flip').value)

        self._bridge = CvBridge()
        self._cap = None
        self._latest = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._pub = self.create_publisher(Image, '/camera/color/image_raw', 10)
        self._pub_compressed = None
        if self._publish_compressed:
            self._pub_compressed = self.create_publisher(
                CompressedImage, '/camera/color/image_raw/compressed', 10
            )

        # Background capture thread keeps `self._latest` fresh; the timer publishes it.
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()

        period = 1.0 / self._fps if self._fps > 0 else 0.2
        self.create_timer(period, self._publish_latest)

        self.get_logger().info(
            f'Camera node started — target {self._width}x{self._height} @ {self._fps}fps, '
            f'compressed={self._publish_compressed}'
        )

    # ── Device resolution ────────────────────────────────────────────────────

    def _resolve_device(self):
        """Return a cv2.VideoCapture source: int index or device path string."""
        dev = (self._device or '').strip()
        if dev:
            return int(dev) if dev.isdigit() else dev

        # Auto-discover by name from /sys/class/video4linux/*/name
        wanted = (self._device_name or '').strip().lower()
        for vdev in sorted(glob.glob('/dev/video*')):
            name_path = f'/sys/class/video4linux/{vdev.split("/")[-1]}/name'
            try:
                with open(name_path) as f:
                    name = f.read().strip()
            except OSError:
                continue
            if wanted and wanted in name.lower():
                self.get_logger().info(f'Matched camera "{name}" at {vdev}')
                return vdev
        # Fall back to first available video device, else index 0
        devs = sorted(glob.glob('/dev/video*'))
        return devs[0] if devs else 0

    def _open(self):
        source = self._resolve_device()
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(source)   # retry with default backend
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # minimise latency / stale frames
        self.get_logger().info(f'Opened camera source: {source}')
        return cap

    # ── Capture loop (background thread) ──────────────────────────────────────

    def _capture_loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            if self._cap is None:
                self._cap = self._open()
                if self._cap is None:
                    self.get_logger().warn(
                        f'No camera available (device="{self._device}", '
                        f'name="{self._device_name}") — retrying in {backoff:.0f}s'
                    )
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue
                backoff = 1.0

            ok, frame = self._cap.read()
            if not ok or frame is None:
                self.get_logger().warn('Frame grab failed — reopening camera')
                self._cap.release()
                self._cap = None
                self._stop.wait(0.5)
                continue

            if self._flip:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            with self._lock:
                self._latest = frame

    # ── Publish (ROS timer) ───────────────────────────────────────────────────

    def _publish_latest(self):
        with self._lock:
            frame = None if self._latest is None else self._latest.copy()
        if frame is None:
            return

        stamp = self.get_clock().now().to_msg()
        try:
            img = self._bridge.cv2_to_imgmsg(frame, encoding='bgr8')
            img.header.stamp = stamp
            img.header.frame_id = self._frame_id
            self._pub.publish(img)

            if self._pub_compressed is not None:
                ok, buf = cv2.imencode(
                    '.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
                )
                if ok:
                    cmsg = CompressedImage()
                    cmsg.header.stamp = stamp
                    cmsg.header.frame_id = self._frame_id
                    cmsg.format = 'jpeg'
                    cmsg.data = buf.tobytes()
                    self._pub_compressed.publish(cmsg)
        except Exception as e:
            self.get_logger().error(f'Publish error: {e}')

    def destroy_node(self):
        self._stop.set()
        if self._cap is not None:
            self._cap.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
