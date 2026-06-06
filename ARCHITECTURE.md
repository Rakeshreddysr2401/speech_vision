# Robot Architecture — Decision Record

## Hardware

| Device | Role |
|---|---|
| Jetson Orin Nano 8GB | Vision, SLAM, Nav2, STT, TTS — pure perception |
| RealSense D555 (PoE) | Depth + RGB + IMU — publishes SafeDDS ROS2 topics natively |
| Raspberry Pi 5 8GB | LangGraph brain, ROS2↔LangGraph bridge, micro-ROS agent |
| Mac Mini 16GB | Ollama (Llava, Gemma, Qwen) — LLM and VLM inference |
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
isaac_ros_nav2            ← GPU path planning, accepts /goal_pose directly from Pi5
```
No custom nodes needed — Pi5 publishes /goal_pose directly to Nav2 over ROS2.

### Container 2 — AI Stack (built via jetson-containers)
```
Base: ros:jazzy-ros-base + pytorch + faster-whisper + kokoro + openwakeword + mlc

~/robot/ai_ws/src/
├── voice_pkg/
│   ├── wakeword_node    ← always-on CPU, openWakeWord "hey jarvis"
│   │                       publishes: /voice/wake_detected (Bool)
│   ├── stt_node         ← triggered by /voice/wake_detected
│   │                       Whisper small on GPU
│   │                       publishes: /voice/user_input (String)
│   └── tts_node         ← Kokoro on GPU
│                           subscribes: /voice/robot_speech (String)
│                           publishes:  /voice/tts_speaking (Bool) → mutes stt_node
│
├── vision_pkg/
│   ├── detector_node    ← YOLOv8 on GPU
│   │                       subscribes: /camera/color/image_raw
│   │                       publishes:  /vision/detections (Detection2DArray)
│   └── moondream_node   ← NanoLLM MLC INT4 (~0.8GB VRAM)
│                           subscribes: /vision/query (String)
│                           publishes:  /vision/query_result (String)
│                           note: handles object localisation via VLM — no spatial_node needed
│                                 nvblox handles 3D env mapping for Nav2
│
└── bringup_pkg/         ← launch files for all modes
```

Both containers: `network_mode: host` → ROS2 topics flow freely.
NITROS zero-copy applies inside Container 1 only.
One workspace: `ai_ws` → Container 2. Container 1 uses Isaac ROS packages only — no custom nodes.

## Full Data Flow

```
D555 (SafeDDS/ethernet)
  → /camera/depth/image_rect_raw  → Container 1: isaac_ros_visual_slam (SLAM)
  → /camera/color/image_raw       → Container 2: detector_node, moondream_node
  → /camera/imu                   → Container 1: isaac_ros_visual_slam

USB mic → wakeword_node (CPU)
  → /voice/wake_detected
  → stt_node (Whisper small GPU)
  → /voice/user_input
  → Pi5 LangGraph (subscribes over network)
      → Mac Mini Lamma Cpp HTTP (LLM/VLM reasoning)
      → /voice/robot_speech      → tts_node → Kokoro → USB speaker
      → /vision/query            → moondream_node → /vision/query_result → Pi5
      → /goal_pose               → nav_goal_node → Nav2

Nav2 → /cmd_vel → microros_agent → WiFi → ESP32 → wheels
```

## ROS2 Topic Map

| Topic | Type | From → To |
|---|---|---|
| `/camera/depth/image_rect_raw` | Image | D555 → visual_slam (NITROS) |
| `/camera/color/image_raw` | Image | D555 → detector_node, moondream_node |
| `/camera/imu` | Imu | D555 → visual_slam |
| `/visual_slam/tracking/odometry` | Odometry | Isaac ROS → Nav2, Pi5 |
| `/vision/detections` | Detection2DArray | detector_node → Pi5 (object awareness) |
| `/vision/query` | String | Pi5 → moondream_node |
| `/vision/query_result` | String | moondream_node → Pi5 |
| `/voice/wake_detected` | Bool | wakeword_node → stt_node |
| `/voice/user_input` | String | stt_node → Pi5, nav_goal_node |
| `/voice/robot_speech` | String | Pi5 → tts_node |
| `/voice/tts_speaking` | Bool | tts_node → stt_node (mute during playback) |
| `/goal_pose` | PoseStamped | Pi5 LangGraph → Nav2 (directly) |
| `/cmd_vel` | Twist | Nav2 → Pi5 micro-ROS agent → WiFi → ESP32 |

## Build Order

| Phase | What | Milestone |
|---|---|---|
| 1 | SLAM | D555 → visual_slam → odometry publishing |
| 2 | Nav2 | nvblox + Nav2 → robot drives to (x,y) goal |
| 3 | Voice loop | wake → STT → Pi5 → TTS → spoken response |
| 4 | Object nav | YOLO + spatial + moondream → "go to the chair" works |

## Isaac ROS Packages

| Phase | Package | Purpose |
|---|---|---|
| 1 | `isaac_ros_visual_slam` | VIO SLAM using D555 depth + IMU |
| 1 | `isaac_ros_image_pipeline` | Accelerated image processing |
| 2 | `isaac_ros_nvblox` | 3D voxel map for Nav2 costmap |
| 2 | `isaac_ros_nav2` | GPU path planning |

## AI Stack

| Component | Choice | VRAM | Notes |
|---|---|---|---|
| Wake word | openWakeWord "hey jarvis" | ~0 (CPU) | Always on, pre-trained |
| STT | faster-whisper small | ~0.6 GB | ~1s latency, good accuracy |
| TTS | Kokoro | ~0.4 GB | GPU-accelerated, natural voice |
| Detector | YOLOv8 | ~0.5 GB | 80 COCO classes |
| VLM | Moondream2 NanoLLM MLC INT4 | ~0.8 GB | Frequent visual queries |
| LLM | Mac Mini Ollama | 0 on Jetson | All conversation/reasoning offloaded |

## Memory Budget (8GB Unified)

| Workload | VRAM/RAM |
|---|---|
| Isaac ROS SLAM + Nav2 + nvblox | ~1.5 GB |
| YOLO detector | ~0.5 GB |
| Whisper small | ~0.6 GB |
| Kokoro TTS | ~0.4 GB |
| Moondream2 MLC INT4 | ~0.8 GB |
| Ubuntu 24.04 OS + overhead | ~1.0 GB |
| **Total** | **~4.8 GB** ✅ |
| **Headroom** | **~3.2 GB** |

**Critical:** Moondream must use NanoLLM MLC INT4 — NOT HuggingFace FP16 (~2GB).

## Storage Layout (256GB NVMe = root)

| Path | Purpose | Budget |
|---|---|---|
| `/` | Ubuntu + JetPack | ~40 GB |
| `/var/lib/docker` | Container images | ~80 GB |
| `~/robot/ros2_ws` | Container 1 workspace | ~10 GB |
| `~/robot/ai_ws` | Container 2 workspace | ~10 GB |
| `~/robot/data` | Rosbags, maps, logs | ~100 GB |
| Buffer | — | ~16 GB |

## Pi5 ↔ Jetson

- Same ROS2 Jazzy, ROS_DOMAIN_ID=0, same network
- Pi5 subscribes: `/voice/user_input`, `/vision/objects_3d`, `/vision/query_result`, `/visual_slam/tracking/odometry`
- Pi5 publishes: `/voice/robot_speech`, `/vision/query`, `/goal_pose`
- Pi5 → Mac Mini: HTTP REST to Ollama API
- Pi5 runs micro-ROS agent — subscribes `/cmd_vel` from Jetson Nav2, forwards to ESP32 over WiFi

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
