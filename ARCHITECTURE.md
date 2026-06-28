
# Robot Architecture — Decision Record

## Hardware

| Device | Role |
|---|---|
| Jetson Orin Nano 8GB | Vision, SLAM, Nav2, STT, TTS — pure perception |
| Logitech USB cam (Brio 100) | **Current** RGB source — `camera_node` publishes `/camera/color/image_raw` |
| RealSense D555 (PoE) | *Future depth upgrade* — Depth + RGB + IMU over SafeDDS (enables SLAM/Nav2/nvblox) |
| Raspberry Pi 5 8GB | LangGraph brain, ROS2↔LangGraph bridge, micro-ROS agent |
| Mac Mini 16GB | llama.cpp server — Gemma 3n E4B multimodal LLM/VLM over HTTP |
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
- DDS discovery: **Ethernet-only unicast** between Jetson and Pi5 over a direct
  cable — WiFi multicast was unreliable (router drops multicast between WiFi clients).
  - Jetson eth `192.168.2.20`, Pi5 eth `192.168.2.10` (static, no gateway)
  - Config: `config/fastdds_unicast.xml` (gitignored — recreate per `PI5_SETUP.md`),
    mounted into both containers at `/config/fastdds_unicast.xml`
  - `interfaceWhiteList` + `initialPeersList` lock all DDS to the cable
  - WiFi (`192.168.1.x`) stays for internet, Mac Mini HTTP, and ESP32 micro-ROS —
    none of which use DDS

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
│   ├── camera_node      ← Logitech USB cam (V4L2). Background grab thread + throttled
│   │                       publish so frames are always fresh, never buffered-stale.
│   │                       publishes: /camera/color/image_raw            (Image, bgr8, 640x480 @ ~5fps)
│   │                                  /camera/color/image_raw/compressed (CompressedImage, JPEG)
│   │                       Single source of the RGB feed until the D555 arrives.
│   └── target_node      ← YOLOv8n (~0.08GB VRAM, ~33ms/frame on Orin Nano)
│                           subscribes: /camera/color/image_raw (Image)
│                                       /vision/target (String — COCO class, "" = idle)
│                           publishes:  /vision/target_result (String JSON:
│                                       {target, found, bearing_x[-1..1], rel_size, conf, stamp})
│                           role: local "go near the cup" nav — gives the LangGraph
│                                 agent a bearing + proximity signal to drive toward a target.
│                           note: local Moondream/VLM was DROPPED — does not fit 8GB
│                                 alongside voice (see Memory Budget). Rich scene
│                                 description ("what do you see") is the Pi5/Mac-Mini
│                                 Gemma look() tool, not a local VLM.
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
Logitech USB cam → camera_node (Container 2)
  → /camera/color/image_raw       → Container 2: target_node (YOLOv8n nav directions)
                                  → Pi5: agent_node look() (Gemma multimodal over network)

D555 (SafeDDS/ethernet) — FUTURE depth upgrade
  → /camera/depth/image_rect_raw  → Container 1: isaac_ros_visual_slam (SLAM)
  → /camera/color/image_raw       → (replaces camera_node as RGB source)
  → /camera/imu                   → Container 1: isaac_ros_visual_slam

USB mic → wakeword_node (CPU)
  → /voice/wake_detected
  → stt_node (Whisper small GPU)
  → /voice/user_input
  → Pi5 LangGraph (subscribes over network)
      → Mac Mini llama.cpp HTTP (LLM + complex VLM: Llava)
      → /voice/robot_speech      → tts_node → Kokoro → USB speaker
      → /vision/target           → target_node (YOLOv8n) → /vision/target_result → Pi5
                                   (bearing + proximity → agent drives wheels toward target)
      → /goal_pose               → Nav2 directly

Nav2 → /cmd_vel → microros_agent → WiFi → ESP32 → wheels
```

## ROS2 Topic Map

| Topic | Type | From → To |
|---|---|---|
| `/camera/color/image_raw` | Image | **camera_node** (Logitech) → target_node, Pi5 `look()` |
| `/camera/color/image_raw/compressed` | CompressedImage | camera_node → Pi5 `look()` / low-bandwidth consumers |
| `/camera/depth/image_rect_raw` | Image | D555 → visual_slam (NITROS) *(future)* |
| `/camera/imu` | Imu | D555 → visual_slam *(future)* |
| `/visual_slam/tracking/odometry` | Odometry | Isaac ROS → Nav2, Pi5 |
| `/vision/target` | String | Pi5 LangGraph → **target_node** (COCO class to hunt, "" = stop) |
| `/vision/target_result` | String (JSON) | **target_node** → Pi5 LangGraph (`{target, found, bearing_x, rel_size, conf, stamp}`) |
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
| 3.5 | Object directions | YOLOv8n `target_node` → "go near the cup" (bearing + proximity) → LangGraph drives wheels | **Active** — mono cam, no depth/obstacle avoidance |
| 4 | Full object nav | Phase 3.5 + SLAM/Nav2 → metric "go to the chair" | Blocked — needs Phase 1 |

> Phase 3 is being developed first (USB/BT mic + speaker + Logitech camera via
> `camera_node`, which feeds `/camera/color/image_raw` to the Pi5 `look()` vision
> agent and to the local `target_node`). Phase 3.5 adds the YOLOv8n target_node so
> the agent can visually servo toward a named object (bearing + relative size, no
> metric distance) using only the mono webcam. Phases 1, 2, 4 resume when the D555
> arrives and takes over as the RGB+depth source.

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
| Nav detector | YOLOv8n (`target_node`, Container 2 / ai_stack) | ~0.08 GB | 80 COCO classes, ~33ms/frame, gives bearing+proximity for "go near X" |
| Rich VLM | Mac Mini / Pi5 Gemma 3n via `look()` (HTTP) | 0 on Jetson | Conversation, reasoning, "what do you see" scene description |
| ~~Local VLM~~ | ~~Moondream2~~ | — | **DROPPED**: FP16 ~3.75 GB won't fit 8GB w/ voice, ~13min load (thrashing); INT4/INT8 incompatible with its hand-rolled F.linear; nano_llm/MLC not built for JP7.2 |

## Memory Budget (8GB Unified)

Current (Phase 3 / 3.5 — voice + vision directions, no SLAM stack yet):

| Workload | VRAM/RAM |
|---|---|
| Whisper small (whisper_cuda) | ~0.6 GB |
| Kokoro TTS (ONNX) | ~0.4 GB |
| YOLOv8n (`target_node`) | ~0.08 GB |
| Ubuntu 24.04 OS + overhead | ~1.0 GB |
| **Total** | **~2.1 GB** ✅ |

**Critical (measured):** a local Moondream/VLM does NOT fit this 8GB board alongside
voice — FP16 is ~3.75 GB (larger than free RAM → ~13 min load from thrashing) and
OOMs once whisper is also resident; INT4/INT8 quant is incompatible with Moondream's
hand-rolled `F.linear`; and `nano_llm`/MLC has no build for JP7.2/L4T r39. Rich scene
understanding therefore lives off-board on the Pi5/Mac-Mini Gemma via `look()`; only
YOLOv8n (object directions) runs locally. When the D555 + Isaac ROS SLAM/Nav2/nvblox
stack lands (~2.0 GB), the budget is still comfortable.

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
- Pi5 subscribes: `/voice/user_input`, `/vision/target_result`, `/camera/color/image_raw/compressed` (for `look()`), `/visual_slam/tracking/odometry`
- Pi5 publishes: `/voice/robot_speech`, `/vision/target`, `/goal_pose`
- Pi5 → Mac Mini: HTTP REST to Ollama API
- Pi5 runs micro-ROS agent — subscribes `/cmd_vel` from Jetson Nav2, forwards to ESP32 over WiFi

## Vision Split — When to Use Which

| Query type | Route | Why |
|---|---|---|
| Rich description: "What room is this?", "Describe the scene", "Is the person happy?" | Pi5 → Mac Mini / Pi5 Gemma `look()` (HTTP, compressed frame) | Needs reasoning; latency OK; no local VLM fits 8GB |
| "Go near the cup / chair / bottle" directions during nav | Pi5 → **target_node** (YOLOv8n, Jetson) via `/vision/target` | Repeated, latency-critical (~33 ms local); gives bearing + proximity |
| Object presence/tracking each frame while driving | Pi5 → **target_node** | Local, no network round-trip per frame |

**Rule:** `target_node` (YOLOv8n) for anything in the 80 COCO classes that the agent
must localise repeatedly while driving. Gemma `look()` for one-shot open-vocabulary
reasoning per conversation turn. There is no local VLM (see Memory Budget).

Limitations of `target_node` (mono webcam): bearing is reliable; "proximity" is a
relative box-size proxy (`rel_size`), NOT metric distance; no obstacle avoidance.
Targets must be COCO classes — open-vocabulary objects need Gemma `look()` or the
future D555/SLAM stack.

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
