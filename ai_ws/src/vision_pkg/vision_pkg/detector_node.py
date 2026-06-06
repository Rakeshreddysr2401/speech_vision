import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
from cv_bridge import CvBridge
from ultralytics import YOLO
import threading


class DetectorNode(Node):
    def __init__(self):
        super().__init__('detector_node')

        self.declare_parameter('model', 'yolov8n.pt')
        self.declare_parameter('confidence', 0.5)
        self.declare_parameter('device', 'cuda')
        self.declare_parameter('image_topic', '/camera/color/image_raw')

        model_path = self.get_parameter('model').value
        self._conf = self.get_parameter('confidence').value
        self._device = self.get_parameter('device').value
        image_topic = self.get_parameter('image_topic').value

        self._bridge = CvBridge()
        self._model = YOLO(model_path)
        self._model.to(self._device)

        self._pub = self.create_publisher(Detection2DArray, '/vision/detections', 10)
        self.create_subscription(Image, image_topic, self._image_cb, 10)

        self._lock = threading.Lock()
        self._processing = False
        self.get_logger().info(f'Detector ready — {model_path} on {self._device}')

    def _image_cb(self, msg: Image):
        # Drop frame if previous is still processing
        if self._processing:
            return
        with self._lock:
            self._processing = True
        threading.Thread(target=self._detect, args=(msg,), daemon=True).start()

    def _detect(self, msg: Image):
        try:
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            results = self._model(frame, conf=self._conf, verbose=False)[0]

            det_array = Detection2DArray()
            det_array.header = msg.header

            for box in results.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                w = x2 - x1
                h = y2 - y1
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = self._model.names[cls_id]

                det = Detection2D()
                det.header = msg.header
                det.bbox = BoundingBox2D()
                det.bbox.center.position.x = cx
                det.bbox.center.position.y = cy
                det.bbox.size_x = w
                det.bbox.size_y = h

                hyp = ObjectHypothesisWithPose()
                hyp.hypothesis.class_id = label
                hyp.hypothesis.score = conf
                det.results.append(hyp)
                det_array.detections.append(det)

            self._pub.publish(det_array)
        except Exception as e:
            self.get_logger().error(f'Detection error: {e}')
        finally:
            with self._lock:
                self._processing = False


def main(args=None):
    rclpy.init(args=args)
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
