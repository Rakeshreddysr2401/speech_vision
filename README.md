# speech_vision — Jetson Orin Sensing Stack

The **eyes, ears, and voice** of the distributed home assistant robot.
Runs on a **Jetson Orin Nano 8GB** and handles all perception and speech I/O.
The Pi5 ([pi5_ros2_ws](https://github.com/Rakeshreddysr2401/pi5_ros2_ws)) is the brain — this repo feeds it data.

```
Mac Mini  ──────  llama.cpp / LLM server
Jetson    ──────  THIS REPO — STT · TTS · Camera · YOLO · Moondream · Depth
Pi 5      ──────  LangGraph brain + chassis control
ESP32     ──────  4-wheel drive chassis
```

---

## Prerequisites

```bash
# ROS2 Humble on Jetson (JetPack 6+)
sudo apt install ros-humble-desktop ros-humble-vision-msgs

# Python dependencies
pip install torch transformers pillow ultralytics \
            faster-whisper kokoro-onnx sounddevice soundfile \
            opencv-python-headless

# Build
cd ~/speech_vision
colcon build --symlink-install
source install/setup.bash
```

---

## Launch

```bash
# Primary mode — STT + TTS + camera + depth + spatial + Moondream
ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant

# Voice only (no camera) — lighter on VRAM
ros2 launch robot_bringup_pkg robot.launch.py mode:=voice_only

# Full pipeline
ros2 launch robot_bringup_pkg robot.launch.py mode:=full

# Vision pipeline only (no voice/brain)
ros2 launch robot_bringup_pkg robot.launch.py mode:=perception_only
```

**Start this before the Pi5.**

---

## Launch Modes

| Mode | Nodes started | VRAM |
|------|--------------|------|
| `visual_assistant` | STT + TTS + camera + detector + depth + spatial + moondream | ~5 GB |
| `voice_only` | STT + TTS | ~1 GB |
| `full` | All nodes | ~6 GB |
| `perception_only` | All vision nodes, no voice/brain | ~5 GB |

`visual_assistant` is the default and the correct mode for Pi5 integration.
It enables depth + spatial nodes so `/vision/objects_3d` is published.

---

## Packages

| Package | Purpose |
|---------|---------|
| `robot_voice_pkg` | `stt_node` (Faster-Whisper) + `tts_node` (Kokoro/Piper) |
| `robot_vision_pkg` | `camera_node` + `detector_node` (YOLO) + `depth_node` + `spatial_node` + `moondream_node` + `scene_node` + `tracker_node` |
| `robot_brain_pkg` | Legacy `brain_node` (LLM directly on Jetson — not used when Pi5 is the brain) |
| `robot_bringup_pkg` | Top-level launch file (`robot.launch.py`) |

---

## Topics Published to Pi5

These are what the Pi5 brain subscribes to:

| Topic | Type | Node | Description |
|-------|------|------|-------------|
| `/voice/user_input` | `String` | `stt_node` | Transcribed speech text |
| `/vision/image_raw` | `Image` | `camera_node` | BGR8 camera frames |
| `/vision/objects_3d` | `String` (JSON) | `spatial_node` | YOLO detections with distance + direction |
| `/vision/query_result` | `String` | `moondream_node` | Moondream VLM answer |

**`/vision/objects_3d` requires** `depth_node` + `spatial_node` — both are enabled in `visual_assistant` mode.

JSON format for each object:
```json
{
  "class": "chair",
  "confidence": 0.91,
  "distance_m": 1.2,
  "direction": "left",
  "angle_h_deg": -25.3,
  "bbox": {"cx": 210.0, "cy": 180.0, "w": 95.0, "h": 140.0}
}
```

---

## Topics Subscribed from Pi5

These are what Pi5 sends to Jetson:

| Topic | Type | Node | Description |
|-------|------|------|-------------|
| `/voice/robot_speech` | `String` | `tts_node` | Text for robot to speak |
| `/vision/query` | `String` | `moondream_node` | Visual question to answer |

---

## Internal Topics (Jetson only)

| Topic | Type | Purpose |
|-------|------|---------|
| `/vision/detections` | `Detection2DArray` | Raw 2D YOLO bounding boxes |
| `/vision/depth` | `Image` (32FC1) | Monocular depth map in metres |
| `/vision/tracks` | `Detection2DArray` | Tracked detections with IDs |
| `/vision/scene_description` | `String` | Periodic scene summary |
| `/voice/stt_listening` | `Bool` | STT is actively listening |
| `/voice/tts_speaking` | `Bool` | TTS is currently speaking |
| `/brain/thinking` | `Bool` | Brain is processing |

---

## Node Pipeline

```
Microphone
    │
    ▼  (VAD detects speech)
stt_node  ──►  /voice/user_input  ──►  Pi5 brain
                                              │
                                     [Pi5 thinks, responds]
                                              │
/voice/robot_speech  ◄────────────────────────┘
    │
    ▼
tts_node  ──►  Speaker

Camera
    │
    ▼
camera_node  ──►  /vision/image_raw  ──►  Pi5 (cached for LLM)
    │
    ├──►  detector_node  ──►  /vision/detections
    │          │
    │     tracker_node  ──►  /vision/tracks
    │                              │
    │     depth_node  ──►  /vision/depth
    │          │                   │
    └──────────┴───►  spatial_node  ──►  /vision/objects_3d  ──►  Pi5
    │
    └──►  moondream_node  ◄──  /vision/query  ◄──  Pi5
               │
               └──►  /vision/query_result  ──►  Pi5
```

---

## Configuration

Each package has a `config/` YAML. Key settings:

**vision_params.yaml**
```yaml
camera_node:
  device_index: 0          # USB camera index
  width: 640
  height: 480
  fps: 30

detector_node:
  model: "yolov8n.pt"      # yolov8n (fast) or yolov8s (accurate)
  confidence: 0.5

moondream_node:
  model_id: "vikhyatk/moondream2"
  revision: "2025-01-09"
  device: "cuda"
  max_new_tokens: 256
```

**voice_params.yaml**
```yaml
stt_node:
  model: "base.en"         # faster-whisper model size
  device: "cuda"

tts_node:
  backend: "kokoro"        # kokoro | piper
```

---

## ROS Domain

Make sure Pi5 and Jetson share the same domain:
```bash
export ROS_DOMAIN_ID=0   # must match on both machines
```

Or set it in `/etc/environment` for persistence.
