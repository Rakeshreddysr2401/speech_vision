# Robot — Jetson Orin Nano 8GB

ROS2 Jazzy AI stack running on a Jetson Orin Nano 8GB (JetPack 7.2, aarch64).

## Architecture

Two Docker containers share a ROS2 network (`ROS_DOMAIN_ID=0`, host networking):

| Container | Image | Role |
|-----------|-------|------|
| `isaac_ros` | NVIDIA Isaac ROS | Navigation, perception, hardware drivers |
| `ai_stack` | `ai_stack:dev-0.0.9` | Voice, vision, LLM inference |

## AI Stack (`ai_stack`)

### Voice Pipeline (`voice_pkg`)

```
Mic — priority: Plantronics USB headset > BT (EVM EnGroove) > never Brio 100
    │
    ▼
WebRTC VAD ──► silence detected ──► openai-whisper (CUDA, base model)
                                          │
                                          ▼
                                  /voice/user_input  (std_msgs/String)
                                          │
                                      [brain node]
                                          │
                                          ▼
                                  /voice/robot_speech (std_msgs/String)
                                          │
                                          ▼
                                   Kokoro TTS (ONNX, CPU)
                                          │
                                          ▼
                        Speaker — same device as mic (USB headset or BT)
```

**Audio device selection** (`audio_device.py`):
- Plantronics connected → use it for both mic and speaker (ALSA `hw:` device)
- No headset → fall back to BT via PipeWire (`pw-cat` / `pw-play` subprocess)
- Brio 100 webcam mic → always blacklisted, never used as input
- Hot-swap: nodes re-detect every 10 s and switch automatically

| Topic | Type | Direction |
|-------|------|-----------|
| `/voice/user_input` | `std_msgs/String` | STT → brain |
| `/voice/robot_speech` | `std_msgs/String` | brain → TTS |
| `/voice/tts_speaking` | `std_msgs/Bool` | TTS → STT (mute mic while speaking) |

**STT latency:** ~0.4s end-to-end (silence timeout 0.8s + whisper base model on CUDA)

### Vision (`vision_pkg`)

```
Logitech USB cam ──► camera_node (V4L2, 640x480 @ ~5fps)
                          │
                          ▼
                 /camera/color/image_raw  (sensor_msgs/Image, bgr8)
                 /camera/color/image_raw/compressed (JPEG)
                          │
        ┌─────────────────┼──────────────────────────┐
        ▼                 ▼                          ▼
  moondream_node   isaac_ros_yolov8 (Container 1)  Pi5 agent_node look()
  (on-demand VLM)  (object detection)              (Gemma multimodal over network)
```

- **`camera_node`** — single source of `/camera/color/image_raw`. Background grab
  thread keeps the latest frame; a timer republishes at a throttled rate to keep the
  Jetson↔Pi5 DDS link light. Auto-discovers the Logitech cam by V4L2 name (`device_name`),
  or set `device` to a `/dev/videoN` path / index. The D555 will publish these topics
  natively over SafeDDS later and replace this node.
- **`moondream_node`** — Moondream VLM (NanoLLM MLC INT4) for on-demand image Q&A
  (`/vision/query` → `/vision/query_result`).

| Topic | Type | Direction |
|-------|------|-----------|
| `/camera/color/image_raw` | `sensor_msgs/Image` | camera_node → moondream, YOLO, Pi5 |
| `/camera/color/image_raw/compressed` | `sensor_msgs/CompressedImage` | camera_node → low-bandwidth consumers |
| `/vision/query` | `std_msgs/String` | Pi5 → moondream |
| `/vision/query_result` | `std_msgs/String` | moondream → Pi5 |

## Quick Start

```bash
# Start containers
docker compose up -d

# Enter AI stack
docker exec -it ai_stack bash

# Build workspace (first time or after code changes)
cd /workspaces/ai_ws && colcon build --symlink-install && source install/setup.bash

# Launch voice nodes
ros2 launch voice_pkg voice.launch.py

# Monitor transcription output
ros2 topic echo /voice/user_input
```

## Image Management

The `ai_stack` image is built via `docker commit`, not `docker build` (see `Dockerfile.ai` for full history). After installing new packages inside the container:

```bash
docker commit ai_stack ai_stack:<new-tag>
# then update image: in docker-compose.yml
```

## Hardware

- **Robot computer:** Jetson Orin Nano 8GB (JetPack 7.2)
- **Microphone / Speaker:** Plantronics Blackwire 3220 USB headset (primary) / EVM EnGroove BT (fallback)
- **Camera:** Logitech Brio 100 USB webcam (video via `camera_node`; its mic is blacklisted in the voice stack)
- **Depth camera:** RealSense D555 (PoE) — *future upgrade*, will replace `camera_node` as the RGB+depth source and enable SLAM/Nav2
