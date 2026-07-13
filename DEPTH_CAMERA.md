# Depth camera (RealSense D555) + perception stack

Plan and how-it-works for adding the Intel RealSense **D555** depth camera and
the NVIDIA Isaac ROS perception stack (cuVSLAM + nvblox + Nav2) to the robot.
Status as of 2026-07-12: camera **about to be delivered**; software prep in
progress. See `NETWORKING.md` for the network side.

## What the D555 is (and why it's not a normal webcam)

- **Network camera, not USB.** It has an onboard **Vision V5 SoC** that computes
  depth *on the camera* and publishes ROS2 topics directly over Ethernet using
  **SafeDDS** (interoperable with Fast DDS / ROS2). The Jetson does NOT run the
  stereo depth pipeline — big win for the 8GB budget.
- Global shutter, integrated **IMU**, depth up to 1280×720@60, RGB 1280×800@60.
- **Connectivity rules:**
  - Native **Ethernet only** — **USB-to-Ethernet adapters do NOT work**.
  - Power via **PoE** (from a PoE switch) **or** its **USB-C** input.
  - Requires **MTU 9000 (jumbo frames)** end-to-end — see `NETWORKING.md`
    (the JioAirFiber router drops these; a jumbo-capable switch is required).

## Do we need the RealSense "driver"?

- **To receive images: no.** The SoC publishes ROS2 topics on its own; any node
  on the DDS graph can subscribe.
- **To set up + integrate: yes, the SDK tools.**
  - `rs-dds-config` — assign the camera a **static IP** (`192.168.2.30`). Its
    default is DHCP, which on our island would leave it unreachable.
  - `realsense2_camera` wrapper — gives the exact topic names, `camera_info`,
    and TF frames that Isaac ROS (cuVSLAM/nvblox) expects.
- Installed via apt in the `isaac_ros` container: `ros-jazzy-realsense2-camera`
  (4.58.x supports the D555 + DDS) + `ros-jazzy-librealsense2` +
  `ros-jazzy-isaac-ros-realsense`.

## Connection options (decision)

| How | Works? | Note |
|---|---|---|
| Ethernet → JioAirFiber router | ❌ | router drops jumbo frames (tested) |
| Ethernet-to-USB adapter → Jetson | ❌ | D555 doesn't support USB-Ethernet |
| **Jumbo-capable gigabit switch** (all devices on it) | ✅ | **the buy.** PoE optional (USB-C powers it) |
| Direct D555 → Jetson `enP8p1s0` | ✅ | quick test only; steals the Pi5 wired port |

## The perception pipeline (the goal)

```
D555  (depth + color + IMU, over Ethernet/DDS)
  │
  ▼   isaac_ros container (Jetson, ROS2 Jazzy)
  ├─ realsense2_camera   → camera topics onto the DDS graph
  ├─ cuVSLAM (visual_slam)→ color + IMU → robot pose + /tf        (GPU)
  ├─ nvblox              → depth + pose → 3D reconstruction + costmap (GPU)
  └─ Nav2                → costmap + goal → /cmd_vel
                                  │
                  Pi5 brain / micro-ROS → ESP32 wheels
```

- `isaac_ros` is **ROS2 Jazzy / Ubuntu 24.04** — same distro as `ai_stack` and
  the Pi5 brain, so **no cross-distro message issues**.
- nvblox + cuVSLAM are Jazzy+Jetson compatible, BUT **nvblox has no prebuilt
  Debian yet → must be built from source** (one-time, in the container).
- The `langrobo_perception` package (in `isaac_ros-dev/src`) already carries
  `nvblox_sim.yaml` + `nav2_sim.yaml` from sim work — the real D555 replaces the
  simulated camera feed.

## Compute budget — the key constraint (DECIDED)

Jetson is an **Orin Nano 8GB (~7.4GB usable)**. The voice stack alone
(`ai_stack`: Whisper 2.3GB + Kokoro + YOLO) already uses **~6.5GB**. cuVSLAM +
nvblox will not fit on top.

**Decision (2026-07-12): prioritize the NVIDIA perception stack on the Jetson.**
Voice moves off the Jetson — for now interact via **Telegram** (the Pi5 brain
already supports it) so Whisper/Kokoro aren't loaded; later, optionally move
STT/TTS onto the Pi5. With voice off the Jetson, the 8GB is free for perception.
(Time-share fallback: run perception only while navigating.)

## Bring-up runbook (when the camera arrives)

1. **Power** the D555 via USB-C (or PoE if using a PoE switch).
2. **Network**: plug it into a **jumbo-capable switch** with the Jetson (or
   directly into the Jetson for a quick test). Set the Jetson port to MTU 9000:
   `sudo ip link set enP8p1s0 mtu 9000`. Confirm the switch passes jumbo (see
   `NETWORKING.md` jumbo test).
3. **Camera IP**: `rs-dds-config` → static **192.168.2.30 / 255.255.255.0**.
   Add to Jetson `/etc/hosts` if you want to reach it by name.
4. **See it**: `docker exec isaac_ros bash -c "source /opt/ros/jazzy/setup.bash &&
   rs-enumerate-devices"` (or `ros2 topic list` for its topics — remember the
   DS-mode caveat; echo a real topic to confirm).
5. **Bring up wrapper**: `ros2 launch realsense2_camera rs_launch.py` (or the
   `isaac_ros_realsense` launch fragment) — decide direct-DDS vs wrapper here.
6. **Then** stand up cuVSLAM → nvblox → Nav2 (separate phases).

## What's DONE vs PENDING (updated 2026-07-13)

- ✅ Network: `isaac_ros` container on the ethernet DDS graph (verified).
- ✅ RealSense SDK 2.58.1 + wrapper 4.58.1 + isaac-ros-realsense installed;
  `rs-eth-config`/`rs-enumerate-devices` present. Image committed as
  `isaac_ros:langrobo-nav-stack-1.2` and compose points at it. (The earlier
  claim that this was already installed was wrong — the running container was
  a bare bootstrap because compose pointed at the base image; fixed.)
- ✅ nvblox + Nav2: prebuilt Jazzy debs work (NO source build needed — this
  section's earlier note is outdated). Full real profile in
  `langrobo_perception` (`mode:=real`) smoke-tested camera-less: zero dead
  nodes.
- ❌ **cuVSLAM does NOT run on this Orin Nano**: the noble-jetpack (JP7) debs
  ship `libcuvslam.so` built for Thor-class ARMv9 — SIGILL in the static
  initialiser on Cortex-A78AE (gdb-verified on releases 4.3 and 4.4,
  2026-07-13). **Localization is RTAB-Map instead** (rgbd_odometry +
  rtabmap_slam debs installed; CPU; persistent map at /data/rtabmap.db —
  saved locations survive reboots). Do not retry cuVSLAM until NVIDIA ships
  an Orin build.
- ✅ Voice off Jetson in rover mode: `fleet_role.sh` (new, ~/robot/scripts/)
  starts ONLY the perception role; Pi5 fleet.sh rover matches. Telegram is
  the interface; detections_3d serves the brain's look() camera feed.
- ⏳ Hardware: jumbo switch (order) + D555 (arriving) + servo wiring
  (ESP32 GPIO 18/19) + camera-mount measurement.
- Bring-up runbook + acceptance tests: Pi5 repo `JETSON_D555_SETUP.md`.
