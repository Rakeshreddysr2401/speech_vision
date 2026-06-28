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
        ┌─────────────────┴──────────────────────────┐
        ▼                                            ▼
  target_node (YOLOv8n)                       Pi5 agent_node look()
  (object directions for nav)                 (Gemma multimodal over network)
```

- **`camera_node`** — single source of `/camera/color/image_raw`. Background grab
  thread keeps the latest frame; a timer republishes at a throttled rate to keep the
  Jetson↔Pi5 DDS link light. Auto-discovers the Logitech cam by V4L2 name (`device_name`),
  or set `device` to a `/dev/videoN` path / index. Builds the `Image`/`CompressedImage`
  by hand (no cv_bridge — its native OpenCV 4.6 runtime isn't on this image). The D555
  will publish these topics natively over SafeDDS later and replace this node.
- **`target_node`** — YOLOv8n "go near the cup" nav helper. The Pi5 agent names a COCO
  class on `/vision/target`; this node finds it in the freshest frame and publishes a
  bearing + relative-size signal on `/vision/target_result` (JSON) to steer the wheels.
  ~0.08 GB VRAM, ~33 ms/frame; idle until a target is set. (A local Moondream/VLM was
  evaluated and dropped — doesn't fit 8 GB with voice; rich description uses Gemma `look()`.)

| Topic | Type | Direction |
|-------|------|-----------|
| `/camera/color/image_raw` | `sensor_msgs/Image` | camera_node → target_node, Pi5 |
| `/camera/color/image_raw/compressed` | `sensor_msgs/CompressedImage` | camera_node → Pi5 `look()` |
| `/vision/target` | `std_msgs/String` | Pi5 → target_node (COCO class, `""`=stop) |
| `/vision/target_result` | `std_msgs/String` (JSON) | target_node → Pi5 (`{found, bearing_x, rel_size, conf}`) |

## Quick Start

### Easiest — one command (uses the `~/.bashrc` aliases)

The whole robot is two machines: the **Jetson** (vision + voice) and the **Pi5** (brain).

```bash
# ── On the JETSON ──────────────────────────────────────────────
docker compose up -d        # start containers (first time / after reboot)
robot-up                    # 🚀 activates EVERYTHING: camera + YOLOv8n target_node + STT + TTS
                            #    (Ctrl+C to stop, or `robot-stop` from another terminal)

# ── On the PI5 (the brain) ─────────────────────────────────────
cd ~/ros2_ws && ./prod.sh           # local Mac Mini Gemma  (free, private)
#   ...or...
cd ~/ros2_ws && ./prod.sh openai    # OpenAI gpt-4o-mini    (cloud, costs $; both do vision)
```

Then just **talk to it** — it's always listening (no wake word). Try: *"what do you see?"*,
*"go near the cup"*, *"move forward 20 centimeters"*.

### Jetson alias cheat-sheet (defined in `~/.bashrc`)

| Command | What it does |
|---|---|
| `robot-up` | Start **all** vision + voice |
| `vision-up` / `voice-up` | Start only vision / only voice |
| `robot-stop` | Stop the whole Jetson stack |
| `robot` | Shell into the `ai_stack` container (ROS sourced) |
| `yolo-test` | Live YOLO detection → prints objects, saves `~/robot/models/yolo_test.jpg` |
| `cam-hz` / `cam-raw` | Camera frame rate / image header |
| `vtarget cup` | Make YOLO hunt for "cup" (`vtarget ""` = stop) |
| `vresult` | Watch `/vision/target_result` JSON |
| `ros2c …` | Any `ros2` command inside the container, e.g. `ros2c node list` |
| `cbv-host` / `cbv-vision` | Rebuild `voice_pkg` / `vision_pkg` from the host |

### Manual (no aliases)

```bash
docker exec -it ai_stack bash
cd /workspaces/ai_ws && colcon build --symlink-install && source install/setup.bash
ros2 launch bringup_pkg robot.launch.py          # camera + target_node + stt + tts
#   single subsystem:  ros2 launch bringup_pkg robot.launch.py voice:=false   (vision only)
ros2 topic echo /voice/user_input                # watch transcriptions
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
