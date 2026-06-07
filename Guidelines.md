# Isaac ROS Dev Environment — Complete Setup Guide

> From fresh JetPack flash to fully working `isaac-ros activate` shell.
> Hardware: Jetson Orin Nano 8GB, JetPack 7.2 (L4T 39.1, Ubuntu 24.04 Noble, CUDA 13.2)

---

## Concepts First — Read Before Running Anything

### The Three Tools and What They Do

| Tool | Made by | Purpose |
|---|---|---|
| **isaac-ros-cli** | NVIDIA (official) | Entry point for Isaac ROS containers — installs packages, manages workspace, enters dev shell |
| **jetson-containers** | dusty-nv (community) | Builds GPU AI containers (PyTorch, Whisper, Kokoro, MLC) — used for Container 2 only |
| **isaac_ros_dev** | NVIDIA | NOT a separate tool — it's the workspace directory + Docker image that isaac-cli manages |

They are not competing. Your robot uses both: isaac-cli for Container 1 (SLAM, Nav2, YOLOv8), jetson-containers for Container 2 (voice, VLM).

### The Isaac ROS Container Model

When you run `isaac-ros activate`, it:
1. Pulls the NVIDIA NGC base image (first time only, ~10-20 min)
2. Starts a Docker container with your workspace mounted at `/workspaces/isaac_ros-dev`
3. Drops you into a shell inside the container as user `admin`
4. Packages you install via `apt` inside **persist** between sessions (container is not destroyed on exit)

### Three Modes — Know Which You Are In

| Mode | Command | When to use |
|---|---|---|
| **Dev shell** | `isaac-ros activate` | Installing packages, testing, converting models, launching nodes |
| **Production** | `docker compose up` | Robot running autonomously, auto-restart on boot |
| **Custom image** | `isaac-ros activate --build-local` | Only if you need packages not in the NGC base image |

### Why `noble-jetpack` Not `noble`

The Isaac ROS apt repo has two distributions:
- `noble` — for x86 Ubuntu 24.04
- `noble-jetpack` — for Jetson aarch64 Ubuntu 24.04

Using `noble` on Jetson gives a 404. Always use `noble-jetpack`.

### Workspace Path

isaac-cli expects the workspace at `~/workspaces/isaac_ros-dev/` on host, mounted inside the container at `/workspaces/isaac_ros-dev`. This is the convention — don't change it or isaac-cli won't auto-mount it.

---

## PHASE 1 — Host System Setup (run on Jetson, not inside container)

### Step 1 — Verify the Flash

Confirm JetPack 7.2 is correctly flashed before doing anything else.

```bash
cat /etc/nv_tegra_release     # expect R39.x — JetPack 7.2
nvcc --version                # expect CUDA 13.2
docker --version              # expect Docker 29.x (already installed by JetPack)
sudo docker info | grep -i runtime  # expect nvidia runtime listed
```

### Step 2 — System Update

Bring the host OS fully up to date.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget python3-pip net-tools htop nvtop
```

### Step 3 — Add the Isaac ROS Apt Repository

The Isaac ROS packages live in NVIDIA's own apt repo — not in the default Ubuntu repos. You must add it before you can install anything Isaac ROS related.

```bash
# Download and install the GPG signing key
k="/usr/share/keyrings/nvidia-isaac-ros.gpg"
curl -fsSL https://isaac.download.nvidia.com/isaac-ros/repos.key | sudo gpg --dearmor \
    | sudo tee $k > /dev/null

# Add the repo — noble-jetpack is the correct dist for Jetson aarch64
echo "deb [signed-by=$k] https://isaac.download.nvidia.com/isaac-ros/release-4.4 noble-jetpack main" \
    | sudo tee /etc/apt/sources.list.d/nvidia-isaac-ros.list

# Update — you must see "noble-jetpack InRelease" in the output to confirm it worked
sudo apt-get update
``` 

Expected output line confirming success:
```
Get:X https://isaac.download.nvidia.com/isaac-ros/release-4.4 noble-jetpack InRelease
```

If you see a 404 — check the URL. Common mistake: using `noble` instead of `noble-jetpack`.

### Step 4 — Install isaac-ros-cli

```bash
sudo apt-get install -y isaac-ros-cli

# Verify — shows available commands
isaac-ros --help
```

This installs: the CLI itself, git-lfs, python3-venv. It creates a system group `isaac-ros-cli` and a venv at `/var/lib/isaac-ros-cli/isaac-ros`.

### Step 5 — Set Workspace Environment Variable

```bash
# Add to .bashrc so it persists across reboots and SSH sessions
echo 'export ISAAC_ROS_WS=~/workspaces/isaac_ros-dev' >> ~/.bashrc
source ~/.bashrc

# Create the workspace directory
mkdir -p ~/workspaces/isaac_ros-dev/src
```

### Step 6 — Add Your User to the Docker Group

Required so you can run `isaac-ros activate` without sudo.

```bash
sudo usermod -aG docker $USER
newgrp docker    # applies the group change in the current shell without reboot
```

### Step 7 — Set nvidia as the Default Docker Runtime

By default Docker uses the `runc` runtime. Isaac ROS containers need `nvidia-container-runtime` for GPU access. Setting it as default means every `docker run` uses it automatically — no need to pass `--runtime=nvidia` every time.

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
EOF

# Restart Docker to apply
sudo systemctl restart docker

# Verify nvidia is the default
docker info | grep -i "default runtime"
# Expect: Default Runtime: nvidia
```

Without this step, `isaac-ros activate` fails with:
```
invoking the NVIDIA Container Runtime Hook directly is not supported.
Please use the NVIDIA Container Runtime (specify --runtime=nvidia flag)
```

### Step 8 — Initialize isaac-cli Docker Mode

One-time init that tells isaac-cli to use Docker (vs venv or baremetal).

```bash
sudo isaac-ros init docker
# Output: Set environment mode to docker.
```

---

## PHASE 2 — Enter the Container and Install Packages

### Step 9 — Activate the Dev Container

This is your daily command. Run it every time you want to work inside Container 1.

```bash
isaac-ros activate
```

**First run:** Downloads the NGC base image (`nvcr.io/nvidia/isaac/ros:...-arm64-jetpack`) — takes 10-20 min depending on internet speed.

**Subsequent runs:** Instant — reuses the existing container.

You will land at a prompt like:
```
admin@localhost:/workspaces/isaac_ros-dev$
```

You are now inside the container. Your host workspace (`~/workspaces/isaac_ros-dev`) is mounted at `/workspaces/isaac_ros-dev` inside.

### Step 10 — Install Isaac ROS Packages Inside the Container

Run these inside the container (after `isaac-ros activate`). Packages persist between sessions.

```bash
sudo apt-get update

# SLAM — visual odometry using D555 depth + IMU (cuVSLAM library)
sudo apt-get install -y ros-jazzy-isaac-ros-visual-slam

# Image pipeline — accelerated camera image processing
sudo apt-get install -y ros-jazzy-isaac-ros-image-pipeline

# nvblox — 3D voxel reconstruction, provides costmap for Nav2
sudo apt-get install -y ros-jazzy-isaac-ros-nvblox

# Nav2 — GPU-accelerated path planning
sudo apt-get install -y ros-jazzy-isaac-ros-nav2

# YOLOv8 — object detection via NITROS zero-copy GPU pipeline
sudo apt-get install -y ros-jazzy-isaac-ros-yolov8
```

### Step 11 — Verify Packages Are Installed

```bash
source /opt/ros/jazzy/setup.bash
ros2 pkg list | grep isaac
```

You should see all five packages listed.

---

## PHASE 3 — Directory Structure (on host)

Run on the host (outside container).

```bash
# Container 1 workspace — managed by isaac-cli (already created in Step 5)
# ~/workspaces/isaac_ros-dev/src

# Container 2 workspace — AI stack (voice, VLM)
mkdir -p ~/robot/ai_ws/src/voice_pkg
mkdir -p ~/robot/ai_ws/src/vision_pkg   # moondream_node only
mkdir -p ~/robot/ai_ws/src/bringup_pkg

# Model weights — TensorRT .engine files (survive container rebuilds)
mkdir -p ~/robot/models

# Shared data
mkdir -p ~/robot/data/maps
mkdir -p ~/robot/data/rosbags
mkdir -p ~/robot/config
mkdir -p ~/robot/logs
```

---

## PHASE 4 — YOLOv8 TensorRT Conversion (run once)

TensorRT engine files are device-specific — must be built on the Jetson itself.

```bash
# On HOST: download YOLOv8s weights
pip3 install ultralytics
cd ~/robot/models
python3 -c "from ultralytics import YOLO; YOLO('yolov8s.pt')"
# yolov8s.pt now at ~/robot/models/yolov8s.pt

# Enter container
isaac-ros activate

# Inside container: convert to TensorRT engine (~5-10 min)
ros2 run isaac_ros_yolov8 isaac_ros_yolov8_converter \
    --input /models/yolov8s.pt \
    --output /models/yolov8s.engine

# Verify
ls -lh /models/yolov8s.engine
```

Use `yolov8s` (small) — fits VRAM budget, runs real-time on Orin Nano.

---

## PHASE 5 — Test SLAM (Phase 1 milestone)

```bash
# Enter container
isaac-ros activate

# Source ROS
source /opt/ros/jazzy/setup.bash

# Launch visual SLAM
ros2 launch isaac_ros_visual_slam isaac_ros_visual_slam.launch.py \
    camera_optical_frames:=[camera_depth_optical_frame] \
    enable_image_slam:=true

# In a second terminal — enter same container and verify odometry publishing
isaac-ros activate
source /opt/ros/jazzy/setup.bash
ros2 topic echo /visual_slam/tracking/odometry
# Expect: pose + velocity updating as robot moves
```

---

## Key Facts to Remember

| Fact | Detail |
|---|---|
| Isaac ROS apt dist name on Jetson | `noble-jetpack` (not `noble`) |
| Container entry command | `isaac-ros activate` (run every session) |
| Workspace on host | `~/workspaces/isaac_ros-dev/` |
| Workspace inside container | `/workspaces/isaac_ros-dev` |
| Packages installed inside container | Persist between sessions (container not destroyed on exit) |
| Default Docker runtime | Must be set to `nvidia` in `/etc/docker/daemon.json` |
| NGC image | `nvcr.io/nvidia/isaac/ros:...-arm64-jetpack` (JP7.1, ABI compatible with JP7.2) |
| Model weights | Store at `~/robot/models/` — mounted at `/models` in container |
| YOLOv8 runs in | Container 1 via `isaac_ros_yolov8` (NITROS zero-copy, not Container 2) |
| VLM split | Mac Mini llama.cpp (Llava) = complex/one-shot queries; Moondream on Jetson = fast repeated nav lookups |
| Nav2 package name | `ros-jazzy-nav2-bringup` (NOT `isaac-ros-nav2` — doesn't exist); `nvblox_nav2` ships with nvblox |

## Common Errors and Fixes

| Error | Cause | Fix |
|---|---|---|
| `Unable to locate package isaac-ros-cli` | Isaac ROS apt repo not added | Run Step 3 — add `noble-jetpack` repo first |
| `404 Not Found` on apt update | Used `noble` instead of `noble-jetpack` | Fix the sources.list entry — replace `noble` with `noble-jetpack` |
| `not a member of the docker group` | User not in docker group | `sudo usermod -aG docker $USER && newgrp docker` |
| `invoking NVIDIA Container Runtime Hook directly is not supported` | Default Docker runtime is `runc` not `nvidia` | Set `default-runtime: nvidia` in `/etc/docker/daemon.json`, restart Docker |
| `.gitconfig: Is a directory` warning | `.gitconfig` is a dir not a file | `rm -rf ~/.gitconfig && touch ~/.gitconfig` (cosmetic warning, doesn't block anything) |
