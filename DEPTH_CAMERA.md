# Depth camera (RealSense D555) + perception stack

Plan and how-it-works for adding the Intel RealSense **D555** depth camera and
the NVIDIA Isaac ROS perception stack (cuVSLAM + nvblox + Nav2) to the robot.
Status as of 2026-07-14: camera **delivered and fully bring-up-tested** — depth,
color, dual IR, IMU and jumbo streaming all verified on the Jetson (see "Bring-up
result" below). See `NETWORKING.md` for the network side.

> **Correction (2026-07-14):** earlier revisions of this doc claimed the apt
> `librealsense2` "supports the D555 + DDS" and that the camera defaults to DHCP.
> **Both were wrong.** No prebuilt arm64 librealsense (ROS deb *or* Intel apt deb)
> includes DDS — it must be **built from source with `-DBUILD_WITH_DDS=ON`**. And
> the D555 ships with a **factory-static IP `192.168.11.55/24`**, not DHCP.
> Details corrected inline below.

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
- **To set up + integrate: yes — but you MUST build librealsense with DDS.**
  - `rs-eth-config` (renamed from `rs-dds-config` in SDK 2.58) — read/change the
    camera's IP + DDS/eth settings. The D555 ships with a **factory-static IP
    `192.168.11.55 / 255.255.255.0`** (NOT DHCP). To reach it, put the host NIC on
    that subnet (e.g. `192.168.11.70`) at MTU 9000; later move the camera onto the
    `192.168.2.x` island if desired.
  - `realsense2_camera` wrapper — gives the exact topic names, `camera_info`,
    and TF frames that Isaac ROS (cuVSLAM/nvblox) expects.
- **The apt `ros-jazzy-librealsense2` (and Intel's arm64 apt debs) are built
  WITHOUT DDS** — verified `nm`: zero FastDDS/RTPS symbols. They can't see the
  D555 at all (a DDS-only network camera). DDS enable in `~/.realsense-config.json`
  uses the key **`context.dds.enabled: true`** (top-level `dds.enabled` is ignored).
  The working SDK is a **from-source build** — see "Bring-up result" below.

## Connection options (decision)

| How | Works? | Note |
|---|---|---|
| Ethernet → JioAirFiber router | ❌ | router drops jumbo frames (tested) |
| Ethernet-to-USB adapter → Jetson | ❌ | D555 doesn't support USB-Ethernet |
| **Direct D555 → Jetson `enP8p1s0`** | ✅ | **used for bring-up — works fully** incl. jumbo streaming. Steals the Pi5 wired port (Pi5 falls back to WiFi). |
| Jumbo-capable gigabit switch (all devices on it) | ✅ | only needed to run camera + Pi5 wired *simultaneously* or for multiple cameras — NOT required for one camera direct. PoE optional (USB-C powers it). |

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
2. **Network**: **direct D555 → Jetson `enP8p1s0` works** (tested — no jumbo switch
   needed for a single camera). Put the host NIC on the camera's subnet at MTU 9000:
   `sudo ip addr add 192.168.11.70/24 dev enP8p1s0 && sudo ip link set enP8p1s0 mtu 9000`.
   NOTE: a jumbo *ping* (`ping -M do -s 8972`) FAILS — the camera ignores oversized
   ICMP — but real streaming pushes genuine 8968-byte jumbo frames fine (r8168 NIC
   handles it). Don't use ping to judge jumbo; verify by streaming.
3. **Camera IP**: factory default is **static `192.168.11.55`** (not DHCP). Sanity:
   `ping 192.168.11.55`. Change it later with `rs-eth-config` if moving to the
   `192.168.2.x` island. (No RJ45 LED ever lights — that's normal, not a fault.)
4. **See it** (needs the DDS build — step in "Bring-up result"): with
   `~/.realsense-config.json` = `{"context":{"dds":{"enabled":true,"domain":0}}}`,
   run that build's `rs-enumerate-devices` (unset `ROS_DISCOVERY_SERVER` first).
5. **Bring up wrapper**: `ros2 launch realsense2_camera rs_launch.py` (or the
   `isaac_ros_realsense` launch fragment) — decide direct-DDS vs wrapper here.
6. **Then** stand up cuVSLAM → nvblox → Nav2 (separate phases).

## What's DONE vs PENDING (updated 2026-07-16)

- ✅ **FIRST FULLY AUTONOMOUS MISSION 2026-07-16**: pixel-grounded goal → Nav2
  → drive → "arrived". Localization = **RTAB-Map** (cuVSLAM parked — motion
  explosions from cross-distro sidecar starvation). Full post-mortem:
  `langrobo_perception/ISSUES_AND_SOLUTIONS.md` Part 9; gotchas 12-16 in
  `CLAUDE.md` here.

- ✅ Network: `192.168.11.70/24` + MTU 9000 on `enP8p1s0` **now persistent** via
  NetworkManager (`nmcli` — survives reboot). D555 reachable at 192.168.11.55.
- ✅ **ROS integration COMPLETE (2026-07-15)**: `realsense2_camera` rebuilt from
  source (v4.58.2) in the workspace against the DDS librealsense install tree
  (`/root/librealsense/install/lib`). `run_perception_real.sh` prepends that path
  to `LD_LIBRARY_PATH` AFTER `setup.bash` to beat the apt lib. Config:
  `~/.realsense-config.json` written by the script on each launch.
  Topics at `/camera/camera0/…` (realsense-ros 4.58.2 defaults namespace to
  `camera`; all downstream remappings updated).
- ✅ nvblox + Nav2: prebuilt Jazzy debs work (NO source build needed). Nav2 ready.
- ❌ **cuVSLAM does NOT run on this Orin Nano**: the noble-jetpack (JP7) debs
  ship `libcuvslam.so` built for Thor-class ARMv9 — SIGILL in the static
  initialiser on Cortex-A78AE (gdb-confirmed on releases 4.3 and 4.4,
  2026-07-13; re-confirmed fresh apt install 2026-07-15: md5 `a5b6f71...` Apr 28
  build = Thor binary). **Do not retry until NVIDIA ships an Orin build.**
  Localization is RTAB-Map instead.
- ✅ **PERCEPTION STACK LIVE (2026-07-15)**: full real pipeline smoke-tested:
  D555 → `realsense2_camera_node` (DDS) → RTAB-Map `rgbd_odometry` → `/odom`
  (quality 460-512 features, ~12 Hz, 80ms update) → `rtabmap` SLAM (building
  map) → nvblox (running, needs robot TF for pointcloud). Camera delivers
  color@18 Hz + depth@13 Hz over DDS. Nav2 brought up separately (needs Pi5
  discovery server for full routing).
- ✅ Voice off Jetson in rover mode.
- ✅ D555 bring-up COMPLETE (2026-07-14) — see "Bring-up result" below.
- ⏳ Hardware: servo wiring (ESP32 GPIO 18/19) + camera-mount measurement.
- ⏳ Timestamp sync: D555 delivers color/depth ~33ms apart via DDS (network
  jitter, not configurable); RTAB-Map `approx_sync` handles it, quality unaffected.
- ⏳ Nav2 full test: needs Pi5 discovery server running (test with robot connected).
- Bring-up runbook + acceptance tests: Pi5 repo `JETSON_D555_SETUP.md`.

## Bring-up result (2026-07-14) — the working recipe

**Problem:** no prebuilt arm64 librealsense has DDS (ROS deb *and* Intel apt deb
verified DDS-less), so the D555 was invisible. **Fix:** build librealsense from
source with DDS, in the `isaac_ros` container:

```bash
git clone --depth 1 --branch v2.58.2 https://github.com/realsenseai/librealsense /root/librealsense
apt-get install -y build-essential cmake libssl-dev libusb-1.0-0-dev libudev-dev \
  libtinyxml2-dev libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev libgtk-3-dev
cd /root/librealsense && mkdir build && cd build
cmake .. -DBUILD_WITH_DDS=ON -DBUILD_TOOLS=ON -DBUILD_GRAPHICAL_EXAMPLES=ON \
  -DFORCE_RSUSB_BACKEND=ON -DCHECK_FOR_UPDATES=OFF -DCMAKE_BUILD_TYPE=Release
make -j6            # ~38 min on Orin Nano; builds Fast-DDS too
# outputs in build/Release/ (lib has ~10k DDS symbols). Committed as -1.3-dds.
```

**Verified working** with that build (unset `ROS_DISCOVERY_SERVER`;
`~/.realsense-config.json` = `{"context":{"dds":{"enabled":true,"domain":0}}}`):
discovery, full control plane, **depth** 896×504 + 1280×720@30, **color** 640×360 +
1280×800@30, **dual IR**, **IMU** (`RS2_STREAM_MOTION` COMBINED_MOTION @200Hz —
decode with `get_combined_motion_data()`, gravity ~9.8 on one accel axis), and real
**jumbo** streaming (8968-byte frames). GUI: `realsense-viewer` (from this build)
runs natively on the Jetson host (`DISPLAY=:1`) after copying the build tree +
`libglfw.so.3` to `~/rs_build`. CLI depth/IMU test tools in container `/root`:
`rsdist`, `rsimu_live`. See memory `project_d555_bringup_findings`.
