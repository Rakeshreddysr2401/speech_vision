# Jetson Setup Plan — Step by Step

> Execute over SSH: `ssh rakhi24@192.168.55.1`
> See ARCHITECTURE.md for all decisions and reasoning.

---

## STEP 1 — Verify Flash ✓ Already Done

```bash
cat /etc/nv_tegra_release     # ✓ JetPack 7.2 (R39.x)
nvcc --version                # ✓ CUDA 13.2
docker --version              # ✓ Docker 29.5.3
sudo docker info | grep nvidia # ✓ nvidia runtime registered
# nvpmodel -m 0 + jetson_clocks already applied
```

Still to confirm when convenient (not blocking):
```bash
df -h | grep -E "/$|nvme"     # confirm NVMe is root partition
```

---

## STEP 2 — System Update

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget python3-pip net-tools htop nvtop
```

---

## STEP 3 — Install jetson-containers

```bash
git clone https://github.com/dusty-nv/jetson-containers ~/jetson-containers
cd ~/jetson-containers && bash install.sh
source ~/.bashrc
jetson-containers show    # verify — lists available packages
```

---

## STEP 4 — Verify GPU in Docker

```bash
docker run --rm --runtime nvidia ubuntu:24.04 nvidia-smi
# Expect: GPU visible, CUDA 13.2 inside container
```

---

## STEP 5 — Create Directory Structure

```bash
# Container 1 workspace (Isaac ROS — isaac-cli default path)
mkdir -p ~/workspaces/isaac_ros-dev/src

# Container 2 workspace (AI stack — only custom code lives here)
mkdir -p ~/robot/ai_ws/src/voice_pkg
mkdir -p ~/robot/ai_ws/src/vision_pkg    # moondream_node only — YOLOv8 is in Container 1
mkdir -p ~/robot/ai_ws/src/bringup_pkg

# Shared
mkdir -p ~/robot/data/maps
mkdir -p ~/robot/data/rosbags
mkdir -p ~/robot/config
mkdir -p ~/robot/logs
mkdir -p ~/robot/models    # TensorRT .engine files (survive container rebuilds)
```
Container 1 workspace is at `~/workspaces/isaac_ros-dev/` (isaac-cli default — mounted inside container at `/workspaces/isaac_ros-dev`). No custom nodes go here; Isaac ROS packages are installed via apt inside the container.

---

## STEP 6 — Configure D555 Camera Network

1. Set D555 static IP via router DHCP reservation: `192.168.1.100`
2. Confirm Jetson ethernet on same subnet:
```bash
ip addr show eth0     # expect 192.168.1.x
ping 192.168.1.100    # expect reply from D555
```

3. Check D555 topics appear (SafeDDS auto-discovery):
```bash
source /opt/ros/jazzy/setup.bash
ros2 topic list
# Expect: /camera/depth/image_rect_raw  /camera/color/image_raw  /camera/imu
```

4. If topics don't appear, create unicast DDS fallback:
```bash
cat > ~/robot/config/fastdds_unicast.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8" ?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <participant profile_name="unicast_connection" is_default_profile="true">
    <rtps>
      <defaultUnicastLocatorList>
        <locator>
          <udpv4><address>192.168.1.100</address></udpv4>
        </locator>
      </defaultUnicastLocatorList>
    </rtps>
  </participant>
</profiles>
EOF
# Add to ~/.bashrc so it persists
echo 'export FASTRTPS_DEFAULT_PROFILES_FILE=~/robot/config/fastdds_unicast.xml' >> ~/.bashrc
source ~/.bashrc
```

---

## STEP 7 — Set Up Container 1: Isaac ROS (via isaac-cli)

> `isaac-ros activate` is the daily entry point — run it every time you want to work inside Container 1.
> Packages installed inside persist between sessions as long as the container is not deleted.

```bash
# 7a. Add Isaac ROS apt repo on HOST (Jetson = noble-jetpack, not noble)
k="/usr/share/keyrings/nvidia-isaac-ros.gpg"
curl -fsSL https://isaac.download.nvidia.com/isaac-ros/repos.key | sudo gpg --dearmor \
    | sudo tee -a $k > /dev/null

f="/etc/apt/sources.list.d/nvidia-isaac-ros.list"
sudo touch $f
s="deb [signed-by=$k] https://isaac.download.nvidia.com/isaac-ros/release-4.4 noble-jetpack main"
grep -qxF "$s" $f || echo "$s" | sudo tee -a $f

sudo apt-get update

# 7b. Install CLI on HOST
sudo apt-get install -y isaac-ros-cli
isaac-ros --version

# 7c. Set workspace env var (add to ~/.bashrc so it persists)
echo 'export ISAAC_ROS_WS=~/workspaces/isaac_ros-dev' >> ~/.bashrc
source ~/.bashrc

# 7d. Enter the dev container
#     First run: pulls NGC base image (~10-20 min). Subsequent runs: instant.
#     Your workspace at $ISAAC_ROS_WS is auto-mounted at /workspaces/isaac_ros-dev inside.
isaac-ros activate

# ── Now you are INSIDE the container ──────────────────────────────────

# 7e. Install Isaac ROS packages via apt (persists inside container between sessions)
sudo apt-get update
sudo apt-get install -y ros-jazzy-isaac-ros-visual-slam
sudo apt-get install -y ros-jazzy-isaac-ros-image-pipeline
sudo apt-get install -y ros-jazzy-isaac-ros-nvblox
# nav2-bringup = standard ROS2 Nav2 stack; nvblox_nav2 costmap plugin comes with nvblox above
sudo apt-get install -y ros-jazzy-nav2-bringup
sudo apt-get install -y ros-jazzy-isaac-ros-yolov8

# 7f. Verify
source /opt/ros/jazzy/setup.bash
ros2 pkg list | grep isaac    # expect visual_slam, image_pipeline, nvblox, yolov8
ros2 pkg list | grep nvblox   # expect nvblox_nav2 here (ships with nvblox, not separately)
```

### Container 1 Modes Reference

| Mode | Command | When to use |
|---|---|---|
| Dev shell | `isaac-ros activate` | Installing packages, testing, launching nodes |
| Production | `docker compose up` | Robot running autonomously (Phase 2+) |
| Custom image | `isaac-ros activate --build-local` | Only if you need packages not in NGC base |

> JP7.1 NGC container is ABI compatible with JP7.2 — safe to use until NVIDIA publishes JP7.2 images.

---

## STEP 8 — Build Container 2: AI Stack (via jetson-containers)

One combined image: ROS2 Jazzy + PyTorch + Whisper + Kokoro ONNX.
`mlc` is deferred — only needed for Moondream VLM (D555 phase, ~1 week away).
`openwakeword` is installed via pip after the build (pure Python, no GPU compile needed).

**Build time: ~60-90 min. Run once, cached forever.**

```bash
# First: update jetson-containers to avoid known dependency resolution bugs
cd ~/jetson-containers && git pull

jetson-containers build \
    ros:jazzy-ros-base \
    pytorch \
    faster-whisper \
    kokoro-tts:onnx \
    --name ai_stack:jp7.2

docker images | grep ai_stack    # verify
```

---

## STEP 9 — Create docker-compose.yml

```bash
cat > ~/robot/docker-compose.yml << 'EOF'
services:

  # ── Container 1: Isaac ROS ──────────────────────────────────────
  isaac_ros:
    image: nvcr.io/nvidia/isaac/ros:aarch64-ros2_jazzy_2.1.0-jp7.1
    container_name: isaac_ros
    runtime: nvidia
    network_mode: host
    privileged: true
    stdin_open: true
    tty: true
    environment:
      - ROS_DOMAIN_ID=0
      - RMW_IMPLEMENTATION=rmw_fastrtps_cpp
      - FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_unicast.xml
    volumes:
      - /dev:/dev
      - ${HOME}/workspaces/isaac_ros-dev:/workspaces/isaac_ros-dev
      - ${HOME}/robot/config:/config
      - ${HOME}/robot/data:/data
      - ${HOME}/robot/models:/models
    restart: unless-stopped

  # ── Container 2: AI Stack ────────────────────────────────────────
  ai_stack:
    image: ai_stack:jp7.2
    container_name: ai_stack
    runtime: nvidia
    network_mode: host
    privileged: true
    stdin_open: true
    tty: true
    environment:
      - ROS_DOMAIN_ID=0
      - RMW_IMPLEMENTATION=rmw_fastrtps_cpp
      - FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_unicast.xml
      - WHISPER_MODEL=small
      - WAKE_WORD=hey_jarvis
      - OLLAMA_HOST=http://MAC_MINI_IP:11434
    volumes:
      - ${HOME}/robot/ai_ws:/workspaces/ai_ws
      - ${HOME}/robot/config:/config
      - ${HOME}/robot/data:/data
    devices:
      - /dev/snd:/dev/snd
    restart: unless-stopped

  # micro-ROS agent runs on Pi5 — not here
  # Pi5: ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
EOF
```

> Replace `MAC_MINI_IP` with actual Mac Mini IP once known.

---

## STEP 10 — Copy Node Code to Jetson

All node code is already written. Copy `robot/ai_ws/` from this machine to Jetson:

```bash
# From Windows machine (PowerShell):
scp -r C:\Users\rasingired\PycharmProjects\speech_vision\ai_ws rakhi24@192.168.55.1:~/robot/
```

Packages in `ai_ws`:
- `voice_pkg`: wakeword_node, stt_node, tts_node
- `vision_pkg`: moondream_node (YOLOv8 moved to Container 1 via isaac_ros_yolov8)
- `bringup_pkg`: launch files (voice/vision/robot)

---

## STEP 11 — Build ai_ws Inside Container 2

```bash
docker run --rm -it --runtime nvidia \
    -v ~/robot/ai_ws:/workspaces/ai_ws \
    ai_stack:jp7.2 bash

# Inside container:
source /opt/ros/jazzy/setup.bash
cd /workspaces/ai_ws
colcon build --symlink-install
```

Container 1 needs no build — only Isaac ROS packages, managed by isaac-cli.

---

## STEP 12 — Test Phase 1: SLAM

```bash
docker exec -it isaac_ros bash
source /opt/ros/jazzy/setup.bash

ros2 launch isaac_ros_visual_slam isaac_ros_visual_slam.launch.py \
    camera_optical_frames:=[camera_depth_optical_frame] \
    enable_image_slam:=true

# In another terminal:
ros2 topic echo /visual_slam/tracking/odometry
# Expect: pose + velocity publishing as robot moves
```

---

## STEP 12.5 — Convert YOLOv8 Weights to TensorRT (run once)

TensorRT engines are device-specific — must be built on the Jetson, not on your dev machine.

```bash
# On host: download YOLOv8s weights to models dir
pip3 install ultralytics
cd ~/robot/models
python3 -c "from ultralytics import YOLO; YOLO('yolov8s.pt')"
# yolov8s.pt now at ~/robot/models/yolov8s.pt

# Enter Container 1
isaac-cli activate

# Inside container: convert to TensorRT engine
cd /models
ros2 run isaac_ros_yolov8 isaac_ros_yolov8_converter \
    --input /models/yolov8s.pt \
    --output /models/yolov8s.engine

# Verify engine file exists
ls -lh /models/yolov8s.engine
```

> Conversion takes ~5-10 min on Orin. Engine is saved to `~/robot/models/` on the host — survives container rebuilds.
> Use `yolov8s` (small) — fits VRAM budget, runs real-time on Orin Nano.

---

## STEP 13 — Test Phase 2: Voice Loop

```bash
docker exec -it ai_stack bash
source /opt/ros/jazzy/setup.bash
source /workspaces/ai_ws/install/setup.bash

ros2 launch bringup_pkg voice.launch.py

# In another terminal, say "Hey Jarvis test":
ros2 topic echo /voice/user_input
# Expect: transcribed text appears
```

---

## STEP 14 — Full Loop Test

Speak: **"Hey Jarvis, go to the kitchen"**

```
wakeword_node detects "hey_jarvis"
  → publishes /voice/wake_detected: True
  → stt_node activates Whisper small
  → publishes /voice/user_input: "go to the kitchen"

Pi5 LangGraph receives /voice/user_input
  → queries Mac Mini Ollama for intent + plan
  → publishes /goal_pose to Jetson nav_goal_node
  → publishes /voice/robot_speech: "On my way to the kitchen"

nav_goal_node receives /goal_pose
  → forwards to Nav2
  → Nav2 publishes /cmd_vel
  → /cmd_vel → network → Pi5 micro-ROS agent → WiFi → ESP32 → wheels move

tts_node receives /voice/robot_speech
  → Kokoro synthesizes → USB speaker plays
```

---

## Pending Items

### Container 1 — DONE ✅
- [x] Isaac ROS apt repo added (noble-jetpack)
- [x] isaac-ros-cli installed
- [x] Docker group + nvidia default runtime configured
- [x] isaac-ros init docker + isaac-ros activate working
- [x] All packages installed: visual_slam, image_pipeline, nvblox, nav2-bringup, yolov8

### Voice Pipeline (Container 2) — TODO (in order)
- [ ] Bluetooth audio: pair via Jetson desktop UI (Settings → Bluetooth) — already have desktop so no terminal steps needed
- [ ] Create directory structure: `~/robot/ai_ws`, `~/robot/models`, `~/robot/data`, etc.
- [ ] Copy `ai_ws` code from Windows to Jetson (`scp -r`)
- [ ] Install jetson-containers (`git clone` + `bash install.sh`)
- [ ] `cd ~/jetson-containers && git pull` — always pull before building
- [ ] Build Container 2 image — `jetson-containers build ros:jazzy-ros-base pytorch faster-whisper kokoro-tts:onnx --name ai_stack:jp7.2` (~60–90 min)
- [ ] Install pip deps inside Container 2: `pip install sounddevice openwakeword`
- [ ] Build `ai_ws` inside Container 2 (`colcon build --symlink-install`)
- [ ] Verify audio devices: `python3 -c "import sounddevice; print(sounddevice.query_devices())"` — update `mic_preference` / `speaker_preference` in `voice_params.yaml` if needed
- [ ] Test voice pipeline: `ros2 launch bringup_pkg voice.launch.py` → say "Hey Jarvis" → check `/voice/user_input`

### SLAM / Nav2 (Container 1) — blocked on D555 camera (ETA: ~1 week)
- [ ] Configure D555 static IP (192.168.1.100 via router DHCP reservation)
- [ ] Test SLAM: `ros2 launch isaac_ros_visual_slam isaac_ros_visual_slam.launch.py`
- [ ] YOLOv8s TensorRT conversion (download weights on host, convert inside Container 1)
- [ ] nvblox + Nav2 costmap tuning
- [ ] Test SLAM map save: `ros2 run nav2_map_server map_saver_cli -f ~/robot/data/maps/home`

### Final Integration
- [ ] docker-compose.yml: replace `MAC_MINI_IP` with actual Mac Mini IP
- [ ] Test full voice loop: wake → STT → Pi5 → LLM → TTS end-to-end
- [ ] systemd auto-start (after all steps pass clean)

---

## STEP 15 — Bluetooth Audio Setup

Since the Jetson has a desktop UI, pair via **Settings → Bluetooth** — no terminal steps needed.

After pairing, verify the device is visible to PulseAudio:

```bash
pactl list short sinks    # output devices
pactl list short sources  # input devices
# Your BT device should appear in both lists
```

If neither command finds it, install the Bluetooth audio module and restart:

```bash
sudo apt install -y pulseaudio-module-bluetooth
pulseaudio -k && pulseaudio --start
```

Once paired, the device will appear in `sounddevice.query_devices()` inside Container 2. Set `mic_preference: "bluetooth"` and `speaker_preference: "bluetooth"` in `voice_params.yaml` to force it.

> **Container access to PulseAudio:** Container 2 needs to reach the host PulseAudio socket. The `docker-compose.yml` already passes `--device /dev/snd` and `network_mode: host`, which is sufficient on Jetson Ubuntu 24.04 with PulseAudio running as a user service.

---

## STEP 16 — Build Container 2: AI Stack

```bash
# If not already cloned:
git clone https://github.com/dusty-nv/jetson-containers ~/jetson-containers
cd ~/jetson-containers && bash install.sh
source ~/.bashrc
jetson-containers show    # verify

# Always pull before building — avoids known dependency resolution bugs
cd ~/jetson-containers && git pull

# Build (~60–90 min, run once, cached forever)
# kokoro-tts:onnx = ONNX Runtime + CUDA EP, lighter than HF variant
# openwakeword = pip install after build (pure Python, no GPU compile)
# mlc = deferred to D555 phase (Moondream VLM not needed yet)
jetson-containers build \
    ros:jazzy-ros-base \
    pytorch \
    faster-whisper \
    kokoro-tts:onnx \
    --name ai_stack:jp7.2

docker images | grep ai_stack    # verify
```

---

## STEP 17 — Install pip deps + Build ai_ws Inside Container 2

```bash
docker run --rm -it --runtime nvidia \
    -v ~/robot/ai_ws:/workspaces/ai_ws \
    --device /dev/snd \
    ai_stack:jp7.2 bash

# Inside container:
pip install sounddevice openwakeword
source /opt/ros/jazzy/setup.bash
cd /workspaces/ai_ws
colcon build --symlink-install
```

---

## STEP 18 — Check Audio Devices

Run inside Container 2 after the build:

```bash
source /opt/ros/jazzy/setup.bash
source /workspaces/ai_ws/install/setup.bash
python3 -c "import sounddevice; print(sounddevice.query_devices())"
```

Match the device name to `mic_preference` / `speaker_preference` in `voice_params.yaml`:

| Scenario | Setting |
|---|---|
| Any connected device, auto-pick | `auto` (default) |
| Force Bluetooth | `bluetooth` |
| Force USB headset | `usb` |
| Specific device by name | e.g. `"Jabra"` (case-insensitive substring) |

---

## STEP 19 — Test Voice Pipeline

```bash
# Terminal 1 — launch all three voice nodes
docker exec -it ai_stack bash
source /opt/ros/jazzy/setup.bash
source /workspaces/ai_ws/install/setup.bash
ros2 launch bringup_pkg voice.launch.py

# Terminal 2 — watch transcription output
docker exec -it ai_stack bash
source /opt/ros/jazzy/setup.bash
ros2 topic echo /voice/user_input
```

Say **"Hey Jarvis, hello"** — you should see the transcribed text appear in Terminal 2.

To test TTS independently (without Pi5):
```bash
ros2 topic pub --once /voice/robot_speech std_msgs/msg/String "data: 'Hello, I am your robot'"
# You should hear the spoken response through the speaker
```

---

## STEP 20 — Extending Container 2 Later (adding new packages)

Container 2 is built once and rebuilt when new AI packages are needed. The build is layered — jetson-containers caches each package layer, so adding one new package only rebuilds from that layer onward.

### When D555 arrives — add Moondream (MLC)

```bash
cd ~/jetson-containers && git pull

jetson-containers build \
    ros:jazzy-ros-base \
    pytorch \
    faster-whisper \
    kokoro-tts:onnx \
    mlc \
    --name ai_stack:jp7.2-vlm
```

`mlc` adds MLC LLM runtime (~2GB download). Moondream INT4 model loads inside the container at runtime — no separate build step. Always `git pull` before building to avoid dependency recursion bugs.

### General pattern — adding any new package

```bash
# 1. Update jetson-containers (avoids stale dependency graphs)
cd ~/jetson-containers && git pull

# 2. Check if the package exists
jetson-containers show | grep <package-name>

# 3. Rebuild with the new package appended, bump the image tag
jetson-containers build \
    ros:jazzy-ros-base \
    pytorch \
    faster-whisper \
    kokoro-tts:onnx \
    <new-package> \
    --name ai_stack:<new-tag>

# 4. Update docker-compose.yml image tag for ai_stack service
#    image: ai_stack:<new-tag>
```

### If a package is not in jetson-containers — use apt inside the container

For standard ROS2 or Python packages not in jetson-containers, install inside a running container and commit it:

```bash
docker run -it --runtime nvidia ai_stack:jp7.2 bash

# Inside: install what you need
sudo apt-get install -y ros-jazzy-<package>
pip install <python-package>
exit

# Commit the layer to a new image
docker commit <container-id> ai_stack:jp7.2-custom

# Update docker-compose.yml to use ai_stack:jp7.2-custom
```

> Prefer jetson-containers packages over manual `apt install` inside containers — they are pre-built for the Jetson GPU and tested against JetPack. Use `apt` only for pure ROS2 packages or Python-only libraries with no GPU component.
