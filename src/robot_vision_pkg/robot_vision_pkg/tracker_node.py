import numpy as np
import rclpy
from rclpy.node import Node
from vision_msgs.msg import (
    Detection2DArray, Detection2D,
    ObjectHypothesisWithPose,
)


class KalmanBoxTracker:
    """Simple Kalman filter tracker for a single bounding box."""

    _count = 0

    def __init__(self, bbox, cls_id, score):
        from filterpy.kalman import KalmanFilter
        self.kf = KalmanFilter(dim_x=7, dim_z=4)
        self.kf.F = np.array([
            [1, 0, 0, 0, 1, 0, 0],
            [0, 1, 0, 0, 0, 1, 0],
            [0, 0, 1, 0, 0, 0, 1],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 1],
        ], dtype=np.float64)
        self.kf.H = np.array([
            [1, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0],
        ], dtype=np.float64)
        self.kf.R[2:, 2:] *= 10.0
        self.kf.P[4:, 4:] *= 1000.0
        self.kf.P *= 10.0
        self.kf.Q[-1, -1] *= 0.01
        self.kf.Q[4:, 4:] *= 0.01

        cx, cy, w, h = bbox
        self.kf.x[:4] = np.array([[cx], [cy], [w], [h]])

        KalmanBoxTracker._count += 1
        self.id = KalmanBoxTracker._count
        self.hits = 1
        self.age = 0
        self.time_since_update = 0
        self.cls_id = cls_id
        self.score = score

    def update(self, bbox, cls_id, score):
        self.time_since_update = 0
        self.hits += 1
        self.cls_id = cls_id
        self.score = score
        self.kf.update(np.array(bbox).reshape(4, 1))

    def predict(self):
        self.kf.predict()
        self.age += 1
        self.time_since_update += 1
        return self.kf.x[:4].flatten()

    def get_state(self):
        return self.kf.x[:4].flatten()


def iou_batch(bb_a, bb_b):
    """Compute IoU between two sets of [cx, cy, w, h] boxes."""
    if len(bb_a) == 0 or len(bb_b) == 0:
        return np.zeros((len(bb_a), len(bb_b)))

    a = np.array(bb_a)
    b = np.array(bb_b)
    a_x1 = a[:, 0] - a[:, 2] / 2
    a_y1 = a[:, 1] - a[:, 3] / 2
    a_x2 = a[:, 0] + a[:, 2] / 2
    a_y2 = a[:, 1] + a[:, 3] / 2
    b_x1 = b[:, 0] - b[:, 2] / 2
    b_y1 = b[:, 1] - b[:, 3] / 2
    b_x2 = b[:, 0] + b[:, 2] / 2
    b_y2 = b[:, 1] + b[:, 3] / 2

    xx1 = np.maximum(a_x1[:, None], b_x1[None, :])
    yy1 = np.maximum(a_y1[:, None], b_y1[None, :])
    xx2 = np.minimum(a_x2[:, None], b_x2[None, :])
    yy2 = np.minimum(a_y2[:, None], b_y2[None, :])

    inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
    area_a = (a_x2 - a_x1) * (a_y2 - a_y1)
    area_b = (b_x2 - b_x1) * (b_y2 - b_y1)
    union = area_a[:, None] + area_b[None, :] - inter
    return inter / np.maximum(union, 1e-6)


class TrackerNode(Node):
    """Multi-object tracker using Kalman filter + IoU matching."""

    def __init__(self):
        super().__init__("tracker_node")

        self.declare_parameter("max_age", 30)
        self.declare_parameter("min_hits", 3)
        self.declare_parameter("iou_threshold", 0.3)

        self._max_age = self.get_parameter("max_age").value
        self._min_hits = self.get_parameter("min_hits").value
        self._iou_thresh = self.get_parameter("iou_threshold").value

        self._trackers: list[KalmanBoxTracker] = []

        self._pub = self.create_publisher(Detection2DArray, "/vision/tracks", 5)
        self.create_subscription(
            Detection2DArray, "/vision/detections", self._on_detections, 5
        )
        self.get_logger().info("Tracker node ready")

    def _on_detections(self, msg: Detection2DArray):
        dets = []
        cls_ids = []
        scores = []
        for det in msg.detections:
            cx = det.bbox.center.position.x
            cy = det.bbox.center.position.y
            w = det.bbox.size_x
            h = det.bbox.size_y
            dets.append([cx, cy, w, h])
            if det.results:
                cls_ids.append(det.results[0].hypothesis.class_id)
                scores.append(det.results[0].hypothesis.score)
            else:
                cls_ids.append("unknown")
                scores.append(0.0)

        tracked = self._update(dets, cls_ids, scores)

        out = Detection2DArray()
        out.header = msg.header
        for trk_id, cx, cy, w, h, cls_id, score in tracked:
            det = Detection2D()
            det.header = msg.header
            det.bbox.center.position.x = float(cx)
            det.bbox.center.position.y = float(cy)
            det.bbox.size_x = float(w)
            det.bbox.size_y = float(h)
            det.id = str(trk_id)

            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = str(cls_id)
            hyp.hypothesis.score = float(score)
            det.results.append(hyp)
            out.detections.append(det)

        self._pub.publish(out)

    def _update(self, detections, cls_ids, scores):
        for trk in self._trackers:
            trk.predict()

        if len(detections) == 0:
            self._trackers = [
                t for t in self._trackers if t.time_since_update <= self._max_age
            ]
            return self._get_active_tracks()

        trk_boxes = [t.get_state().tolist() for t in self._trackers]
        iou_matrix = iou_batch(detections, trk_boxes) if trk_boxes else np.empty((len(detections), 0))

        matched_det = set()
        matched_trk = set()

        if iou_matrix.size > 0:
            from scipy.optimize import linear_sum_assignment
            row_ind, col_ind = linear_sum_assignment(-iou_matrix)
            for r, c in zip(row_ind, col_ind):
                if iou_matrix[r, c] >= self._iou_thresh:
                    matched_det.add(r)
                    matched_trk.add(c)
                    self._trackers[c].update(detections[r], cls_ids[r], scores[r])

        for d in range(len(detections)):
            if d not in matched_det:
                self._trackers.append(
                    KalmanBoxTracker(detections[d], cls_ids[d], scores[d])
                )

        self._trackers = [
            t for i, t in enumerate(self._trackers)
            if i in matched_trk or t.time_since_update <= self._max_age
        ]

        return self._get_active_tracks()

    def _get_active_tracks(self):
        results = []
        for trk in self._trackers:
            if trk.hits >= self._min_hits and trk.time_since_update == 0:
                state = trk.get_state()
                results.append(
                    (trk.id, state[0], state[1], state[2], state[3], trk.cls_id, trk.score)
                )
        return results


def main(args=None):
    rclpy.init(args=args)
    node = TrackerNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
