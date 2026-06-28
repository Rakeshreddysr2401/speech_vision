import cv2, time, glob, torch
torch.backends.cudnn.enabled = False
from ultralytics import YOLO

# find the Brio camera by V4L2 name
dev = 0
for v in sorted(glob.glob("/dev/video*")):
    name_path = "/sys/class/video4linux/" + v.split("/")[-1] + "/name"
    try:
        n = open(name_path).read().strip()
    except OSError:
        continue
    if "brio" in n.lower():
        dev = v
        print("camera:", n, "->", v)
        break

cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
ok, frame = False, None
for _ in range(10):                 # warm up / let auto-exposure settle
    ok, frame = cap.read()
    time.sleep(0.05)
cap.release()
if not ok or frame is None:
    print("ERROR: could not grab a frame")
    raise SystemExit(1)
print("frame:", frame.shape)

m = YOLO("/model_store/yolov8n.pt")
m.to("cuda")
r = m.predict(frame, conf=0.25, device="cuda", verbose=False)[0]

H, W = frame.shape[:2]
print("\n=== YOLO detected %d object(s) ===" % len(r.boxes))
for b in r.boxes:
    cls = m.names[int(b.cls[0])]
    conf = float(b.conf[0])
    x1, y1, x2, y2 = b.xyxy[0].tolist()
    bearing = ((x1 + x2) / 2 / W) * 2 - 1
    relsize = ((x2 - x1) * (y2 - y1)) / (W * H)
    print("  %-12s conf=%.2f  bearing_x=%+.2f  rel_size=%.2f" % (cls, conf, bearing, relsize))

cv2.imwrite("/model_store/yolo_test.jpg", r.plot())
print("\nannotated image saved -> ~/robot/models/yolo_test.jpg")
