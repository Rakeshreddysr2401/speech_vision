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
Moondream VLM for image captioning and visual Q&A.

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
- **Webcam:** Logitech Brio 100 (video only — mic blacklisted)
- **Camera:** (tbd)
