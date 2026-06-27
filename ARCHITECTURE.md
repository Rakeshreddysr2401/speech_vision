
# Robot Architecture — Decision Record

## Hardware

| Device | Role |
|---|---|
| Jetson Orin Nano 8GB | Vision, SLAM, Nav2, STT, TTS — pure perception |
| RealSense D555 (PoE) | Depth + RGB + IMU — publishes SafeDDS ROS2 topics natively |
| Raspberry Pi 5 8GB | LangGraph brain, ROS2↔LangGraph bridge, micro-ROS agent |
| Mac Mini 16GB | llama.cpp server — LLM + complex VLM queries (Llava) over HTTP |
| ESP32 | Motor controller (4 wheels, micro-ROS over WiFi) |

## Network Topology

```
D555 ──PoE injector──┐
                     └── Router/Switch ── Jetson (eth0) 192.168.1.10
                                      └── Pi5
                                      └── Mac Mini
D555 static IP: 192.168.1.100 (set via router DHCP reservation)
```

- All devices: ROS_DOMAIN_ID=0, ROS2 Jazzy
- Topic namespacing: `/voice/`, `/vision/`, `/camera/` to keep things clean
- D555 uses SafeDDS (Fast DDS compatible) — no librealsense SDK needed
- DDS discovery: multicast first, fallback to unicast fastdds.xml if blocked

## Two-Container Design

### Container 1 — Isaac ROS (NGC JP7.1 image)
```
isaac_ros_visual_slam     ← SLAM (NITROS zero-copy GPU)
isaac_ros_image_pipeline  ← accelerated image processing
isaac_ros_nvblox          ← 3D voxel map for Nav2
nav2-bringup + nvblox_nav2 ← standard Nav2 stack + nvblox costmap plugin (ships with nvblox)
isaac_ros_yolov8          ← object detection (NITROS zero-copy, no image copy between containers)
```
No custom nodes needed — Pi5 publishes /goal_pose directly to Nav2 over ROS2.

### Container 2 — AI Stack (built via jetson-containers)
```
Base: ros:jazzy-ros-base + pytorch + faster-whisper + kokoro + openwakeword + mlc

~/robot/ai_ws/src/
├── voice_pkg/
│   ├── audio_device.py  ← device discovery: USB headset > BT > system default
│   │                       find_input_device(preference) / find_output_device(preference)
│   │                       preference: 'auto' | 'bluetooth' | 'usb' | '<name substring>'
│   ├── audio_capture.py ← shared sd.InputStream → queue, used by wakeword + stt
│   ├── stt_backend.py   ← abstract STTBackend + FasterWhisperBackend
│   │                       swap engine: change stt_backend param, add class to _REGISTRY
│   ├── tts_backend.py   ← abstract TTSBackend + KokoroBackend
│   │                       swap engine: change tts_backend param, add class to _REGISTRY
│   ├── wakeword_node    ← always-on CPU, openWakeWord "hey_jarvis"
│   │                       publishes: /voice/wake_detected (Bool)
│   ├── stt_node         ← triggered by /voice/wake_detected
│   │                       default: Whisper small on GPU via FasterWhisperBackend
│   │                       publishes: /voice/user_input (String)
│   └── tts_node         ← default: Kokoro on GPU via KokoroBackend
│                           subscribes: /voice/robot_speech (String)
│                           publishes:  /voice/tts_speaking (Bool) → mutes stt_node
│
├── vision_pkg/
│   └── moondream_node   ← NanoLLM MLC INT4 (~0.8GB VRAM)
│                           subscribes: /vision/query (String)
│                           publishes:  /vision/query_result (String)
│                           note: handles object localisation via VLM — no spatial_node needed
│                                 nvblox handles 3D env mapping for Nav2
│
└── bringup_pkg/         ← launch files for all modes
```

### Voice Stack — Audio Device Priority

| Priority | Type | How to select |
|---|---|---|
| 1 (highest) | USB headset | `mic_preference: "usb"` or `auto` when headset connected |
| 2 | USB headset | `mic_preference: "usb"` or `auto` when USB plugged in |
| 3 (fallback) | System default | `mic_preference: "auto"` with no BT/USB present |
| Manual | Any device | `mic_preference: "Jabra"` — case-insensitive substring match |

Change `mic_preference` / `speaker_preference` in `voice_params.yaml` — no code changes needed.

### Voice Stack — Swapping STT/TTS Models

To swap the STT engine (e.g. add a new Whisper variant or a different engine entirely):
1. Add a subclass of `STTBackend` in `stt_backend.py`
2. Register it in `_REGISTRY`
3. Set `stt_backend: "your_key"` in `voice_params.yaml`

Same pattern for TTS via `tts_backend.py`. Nodes never import the model directly — only through the backend interface.
YOLOv8 moved to Container 1 (isaac_ros_yolov8) — runs NITROS zero-copy in the same GPU pipeline as visual_slam.

Both containers: `network_mode: host` → ROS2 topics flow freely.
NITROS zero-copy applies inside Container 1 only.
One workspace: `ai_ws` → Container 2. Container 1 uses Isaac ROS packages only — no custom nodes.

## Full Data Flow

```
D555 (SafeDDS/ethernet)
  → /camera/depth/image_rect_raw  → Container 1: isaac_ros_visual_slam (SLAM)
  → /camera/color/image_raw       → Container 1: isaac_ros_yolov8 (NITROS)
                                  → Container 2: moondream_node (on-demand queries only)
  → /camera/imu                   → Container 1: isaac_ros_visual_slam

USB mic → wakeword_node (CPU)
  → /voice/wake_detected
  → stt_node (Whisper small GPU)
  → /voice/user_input
  → Pi5 LangGraph (subscribes over network)
      → Mac Mini llama.cpp HTTP (LLM + complex VLM: Llava)
      → /voice/robot_speech      → tts_node → Kokoro → USB speaker
      → /vision/query            → moondream_node (fast/repeated lookups) → /vision/query_result → Pi5
      → /goal_pose               → Nav2 directly

Nav2 → /cmd_vel → microros_agent → WiFi → ESP32 → wheels
```

## ROS2 Topic Map

| Topic | Type | From → To |
|---|---|---|
| `/camera/depth/image_rect_raw` | Image | D555 → visual_slam (NITROS) |
| `/camera/color/image_raw` | Image | D555 → isaac_ros_yolov8 (Container 1), moondream_node (Container 2, on-demand) |
| `/camera/imu` | Imu | D555 → visual_slam |
| `/visual_slam/tracking/odometry` | Odometry | Isaac ROS → Nav2, Pi5 |
| `/vision/detections` | Detection2DArray | isaac_ros_yolov8 (Container 1) → Pi5 (object awareness) |
| `/vision/query` | String | Pi5 → moondream_node |
| `/vision/query_result` | String | moondream_node → Pi5 |
| `/voice/wake_detected` | Bool | wakeword_node → stt_node |
| `/voice/user_input` | String | stt_node → Pi5, nav_goal_node |
| `/voice/robot_speech` | String | Pi5 → tts_node |
| `/voice/tts_speaking` | Bool | tts_node → stt_node (mute during playback) |
| `/goal_pose` | PoseStamped | Pi5 LangGraph → Nav2 (directly) |
| `/cmd_vel` | Twist | Nav2 → Pi5 micro-ROS agent → WiFi → ESP32 |

## Build Order

| Phase | What | Milestone | Status |
|---|---|---|---|
| 1 | SLAM | D555 → visual_slam → odometry publishing | Blocked — D555 ETA ~1 week |
| 2 | Nav2 | nvblox + Nav2 → robot drives to (x,y) goal | Blocked — needs Phase 1 |
| 3 | Voice loop | wake → STT → Pi5 → TTS → spoken response | **Active** — no camera needed |
| 4 | Object nav | YOLO + moondream → "go to the chair" works | Blocked — needs Phase 1 |

> Phase 3 is being developed first (USB/BT mic + speaker, Logitech camera optional).
> Phases 1, 2, 4 resume when D555 arrives.

## Isaac ROS Packages

| Phase | Package | Purpose |
|---|---|---|
| 1 | `isaac_ros_visual_slam` | VIO SLAM using D555 depth + IMU |
| 1 | `isaac_ros_image_pipeline` | Accelerated image processing |
| 2 | `isaac_ros_nvblox` | 3D voxel map for Nav2 costmap |
| 2 | `nav2-bringup` + `nvblox_nav2` | Standard Nav2 stack + nvblox costmap plugin |

## AI Stack

| Component | Choice | VRAM | Notes |
|---|---|---|---|
| Wake word | openWakeWord "hey jarvis" | ~0 (CPU) | Always on, pre-trained |
| STT | faster-whisper small | ~0.6 GB | ~1s latency, good accuracy |
| TTS | Kokoro | ~0.4 GB | GPU-accelerated, natural voice |
| Detector | YOLOv8 (isaac_ros_yolov8, Container 1) | ~0.5 GB | 80 COCO classes, NITROS zero-copy |
| VLM | Moondream2 NanoLLM MLC INT4 | ~0.8 GB | Frequent visual queries |
| LLM + complex VLM | Mac Mini llama.cpp (Llava) | 0 on Jetson | Conversation, reasoning, rich scene description |
| Fast VLM | Moondream2 NanoLLM MLC INT4 (Jetson) | ~0.8 GB | Frequent nav lookups: distance checks, object tracking, repeated queries |

## Memory Budget (8GB Unified)

| Workload | VRAM/RAM |
|---|---|
| Isaac ROS SLAM + Nav2 + nvblox + YOLOv8 | ~2.0 GB |
| Whisper small | ~0.6 GB |
| Kokoro TTS | ~0.4 GB |
| Moondream2 MLC INT4 | ~0.8 GB |
| Ubuntu 24.04 OS + overhead | ~1.0 GB |
| **Total** | **~4.8 GB** ✅ |
| **Headroom** | **~3.2 GB** |

Note: YOLO moved to Container 1 — memory budget unchanged (~0.5 GB still allocated there, now under Isaac ROS line).

**Critical:** Moondream must use NanoLLM MLC INT4 — NOT HuggingFace FP16 (~2GB).

## Storage Layout (256GB NVMe = root)

| Path | Purpose | Budget |
|---|---|---|
| `/` | Ubuntu + JetPack | ~40 GB |
| `/var/lib/docker` | Container images | ~80 GB |
| `~/workspaces/isaac_ros-dev` | Container 1 workspace (isaac-cli default) | ~10 GB |
| `~/robot/ai_ws` | Container 2 workspace | ~10 GB |
| `~/robot/data` | Rosbags, maps, logs | ~100 GB |
| `~/robot/models` | TensorRT .engine files (mounted at /models in Container 1) | ~5 GB |
| Buffer | — | ~11 GB |

## Pi5 ↔ Jetson

- Same ROS2 Jazzy, ROS_DOMAIN_ID=0, same network
- Pi5 subscribes: `/voice/user_input`, `/vision/detections`, `/vision/query_result`, `/visual_slam/tracking/odometry`
- Pi5 publishes: `/voice/robot_speech`, `/vision/query`, `/goal_pose`
- Pi5 → Mac Mini: HTTP REST to Ollama API
- Pi5 runs micro-ROS agent — subscribes `/cmd_vel` from Jetson Nav2, forwards to ESP32 over WiFi

## VLM Split — When to Use Which

| Query type | Route | Why |
|---|---|---|
| Rich description: "What room is this?", "Describe the scene" | Pi5 → Mac Mini Llava | Needs reasoning, latency OK |
| Conversational vision: "Is the person happy?" | Pi5 → Mac Mini Llava | Complex inference |
| Navigation lookups: "Is the chair still there?", "How far?" | Pi5 → Moondream (Jetson) | Repeated, latency-critical |
| Object tracking during nav: frequent frame checks | Pi5 → Moondream (Jetson) | ~150ms local vs ~1s over network |

**Rule:** Moondream for anything called repeatedly during active navigation. Mac Mini Llava for one-shot reasoning queries per conversation turn.

Moondream stays loaded in VRAM permanently (always ready). Mac Mini VLM is on-demand over HTTP (`http://singireddys.local:8080/v1`).

## micro-ROS

- Agent on **Pi5** (alongside LangGraph + ROS2 bridge — same device, cleaner separation)
- ESP32 over WiFi, UDP port 8888
- Path: `Jetson Nav2 → /cmd_vel → ethernet → Pi5 micro-ROS agent → WiFi → ESP32 → wheels`
- Extra latency vs Jetson-hosted: ~1ms ethernet — negligible at 10-50Hz motor control rate
- Jetson = pure perception (Isaac ROS + AI stack), Pi5 = brain + motor bridge

## Jetson System

- IP: 192.168.55.1 (USB RNDIS), 192.168.1.10 (robot ethernet)
- Username: rakhi24
- JetPack: 7.2 (L4T 39.1.0), CUDA 13.2
- Power: nvpmodel -m 0 + jetson_clocks (max, already applied)
- Isaac ROS: 4.4.0 JP7.1 containers — ABI compatible with JP7.2
