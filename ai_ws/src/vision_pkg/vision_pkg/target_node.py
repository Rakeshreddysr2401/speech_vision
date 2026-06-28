"""Visual-servoing target finder — the local "go near the cup" nav brain.

Runs YOLOv8n on the camera feed. The LangGraph agent on the Pi5 names a target
(e.g. "cup") on `/vision/target`; this node finds that object in the freshest
frame and publishes a bearing + relative-size signal on `/vision/target_result`
that the agent uses to drive the wheels toward it.

Why YOLOv8n and not a VLM: on the 8GB Orin Nano, YOLOv8n needs ~0.08 GB and runs
in ~33 ms, leaving the GPU free for the voice stack. A local Moondream/VLM (3.75 GB
FP16) does not fit alongside voice and takes minutes to load. Rich scene
description ("what do you see") is handled separately by the Pi5/Mac-Mini Gemma
`look()` tool — this node only answers "where is <target>".

Subscribes:
  /camera/color/image_raw   sensor_msgs/Image   (bgr8) — from camera_node
  /vision/target            std_msgs/String     target class name, "" to stop

Publishes:
  /vision/target_result     std_msgs/String     JSON:
    {
      "target":   "cup",          # what we were asked to find
      "found":    true,           # was it in frame
      "bearing_x": -0.42,         # horizontal offset of target centre, [-1..1]
                                  #   -1 = far left, 0 = centred, +1 = far right
      "rel_size": 0.18,           # box area / frame area, [0..1] — proximity proxy
                                  #   (bigger = closer; mono cam, NOT metric distance)
      "conf":     0.81,           # detection confidence of the chosen box
      "stamp":    1719560000.12   # image capture time (epoch seconds)
    }

The target class must be one of the 80 COCO classes (cup, bottle, chair, person,
…). Detection only runs while a non-empty target is set, so it costs nothing idle.
"""

import json
import threading

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

import torch
torch.backends.cudnn.enabled = False  # cuDNN version mismatch on Jetson (same as voice STT)
from ultralytics import YOLO


class TargetNode(Node):
    def __init__(self):
        super().__init__('target_node')

        self.declare_parameter('model', '/model_store/yolov8n.pt')
        self.declare_parameter('confidence', 0.4)
        self.declare_parameter('device', 'cuda')
        self.declare_parameter('detect_rate', 5.0)     # Hz — how often to run YOLO while a target is set
        self.declare_parameter('image_topic', '/camera/color/image_raw')
        self.declare_parameter('target_topic', '/vision/target')
        self.declare_parameter('result_topic', '/vision/target_result')

        model_path = self.get_parameter('model').value
        self._conf = float(self.get_parameter('confidence').value)
        self._device = self.get_parameter('device').value
        detect_rate = float(self.get_parameter('detect_rate').value)
        image_topic = self.get_parameter('image_topic').value
        target_topic = self.get_parameter('target_topic').value
        result_topic = self.get_parameter('result_topic').value

        self._model = YOLO(model_path)
        self._model.to(self._device)
        self._classes = {name.lower(): cid for cid, name in self._model.names.items()}

        self._latest = None          # (frame, stamp)
        self._frame_lock = threading.Lock()
        self._target = ''            # current class name to hunt for ("" = idle)
        self._busy = False

        self.create_subscription(Image, image_topic, self._image_cb, 10)
        self.create_subscription(String, target_topic, self._target_cb, 10)
        self._pub = self.create_publisher(String, result_topic, 10)

        period = 1.0 / detect_rate if detect_rate > 0 else 0.2
        self.create_timer(period, self._tick)

        self.get_logger().info(
            f'Target node ready — {model_path} on {self._device}, '
            f'{len(self._classes)} COCO classes. Idle until a target is set on {target_topic}.'
        )

    # ── Inputs ────────────────────────────────────────────────────────────────

    def _image_cb(self, msg: Image):
        # Decode bgr8 Image by hand (no cv_bridge — see camera_node docstring).
        if msg.encoding != 'bgr8':
            self.get_logger().warn(f'Expected bgr8, got "{msg.encoding}" — ignoring frame.')
            return
        try:
            frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3).copy()
        except ValueError as e:
            self.get_logger().error(f'Frame decode error: {e}')
            return
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        with self._frame_lock:
            self._latest = (frame, stamp)

    def _target_cb(self, msg: String):
        target = msg.data.strip().lower()
        if target and target not in self._classes:
            self.get_logger().warn(
                f'Target "{target}" is not a known COCO class — ignoring. '
                f'Examples: cup, bottle, chair, person.'
            )
            return
        self._target = target
        self.get_logger().info('Target cleared.' if not target else f'Now hunting for "{target}".')

    # ── Detection loop (ROS timer) ────────────────────────────────────────────

    def _tick(self):
        target = self._target
        if not target or self._busy:
            return
        with self._frame_lock:
            snap = self._latest
        if snap is None:
            return
        self._busy = True
        try:
            self._detect_and_publish(target, *snap)
        except Exception as e:
            self.get_logger().error(f'Detection error: {e}')
        finally:
            self._busy = False

    def _detect_and_publish(self, target, frame, stamp):
        h, w = frame.shape[:2]
        cls_id = self._classes[target]
        results = self._model.predict(
            frame, conf=self._conf, classes=[cls_id], device=self._device, verbose=False
        )[0]

        best = None  # (area, cx, conf)
        for box in results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            area = (x2 - x1) * (y2 - y1)
            if best is None or area > best[0]:
                best = (area, (x1 + x2) / 2.0, float(box.conf[0]))

        if best is None:
            payload = {'target': target, 'found': False, 'bearing_x': 0.0,
                       'rel_size': 0.0, 'conf': 0.0, 'stamp': stamp}
        else:
            area, cx, conf = best
            payload = {
                'target': target,
                'found': True,
                'bearing_x': round((cx / w) * 2.0 - 1.0, 4),  # [-1 left .. +1 right]
                'rel_size': round(area / (w * h), 4),          # proximity proxy
                'conf': round(conf, 4),
                'stamp': stamp,
            }
        self._pub.publish(String(data=json.dumps(payload)))


def main(args=None):
    rclpy.init(args=args)
    node = TargetNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
