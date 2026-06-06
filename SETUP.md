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
# Container 2 workspace (AI stack — only custom code lives here)
mkdir -p ~/robot/ai_ws/src/voice_pkg
mkdir -p ~/robot/ai_ws/src/vision_pkg
mkdir -p ~/robot/ai_ws/src/bringup_pkg

# Shared
mkdir -p ~/robot/data/maps
mkdir -p ~/robot/data/rosbags
mkdir -p ~/robot/config
mkdir -p ~/robot/logs
```
Container 1 uses Isaac ROS packages only — no custom workspace needed.

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

```bash
# Install Isaac ROS CLI
pip3 install isaac-ros-cli
isaac-ros-cli --version

# Initialize — pulls correct NGC JP7.1 container, sets up workspace mounts
sudo isaac-ros init docker

# Verify image pulled
docker images | grep isaac
```

> JP7.1 container runs on JP7.2 — ABI compatible (minor revision).
> When NVIDIA publishes JP7.2 images: `sudo isaac-ros init docker --version <new-tag>`

---

## STEP 8 — Build Container 2: AI Stack (via jetson-containers)

One combined image: ROS2 Jazzy + PyTorch + Whisper + Kokoro + openWakeWord + MLC (Moondream INT4).

**Build time: ~60-90 min. Run once, cached forever.**

```bash
cd ~/jetson-containers

jetson-containers build \
    ros:jazzy-ros-base \
    pytorch \
    faster-whisper \
    kokoro \
    openwakeword \
    mlc \
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
      - ${HOME}/robot/config:/config
      - ${HOME}/robot/data:/data
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
# From Windows machine:
scp -r C:\Projects\ai_experiments\robot\ai_ws rakhi24@192.168.55.1:~/robot/
```

Packages in `ai_ws`:
- `voice_pkg`: wakeword_node, stt_node, tts_node
- `vision_pkg`: detector_node, moondream_node
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
wakeword_node detects "hey jarvis"
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

- [ ] Mac Mini IP → replace `MAC_MINI_IP` in docker-compose.yml
- [ ] Write node code (STEP 10) — next session
- [ ] First SLAM map save: `ros2 run nav2_map_server map_saver_cli -f ~/robot/data/maps/home`
- [ ] nvblox + Nav2 costmap tuning (Phase 2)
- [ ] Custom wake word if desired (after system stable)
- [ ] systemd auto-start (after all steps pass clean)
