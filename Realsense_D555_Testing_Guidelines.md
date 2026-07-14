# RealSense D555 — Testing Guidelines (from scratch → live visual)

Step-by-step to bring up a **brand-new Intel RealSense D555** on the Jetson Orin
and get to the live depth/point-cloud/IMU view in the RealSense Viewer — exactly
the path we validated on 2026-07-14. Follow top to bottom the first time.

Companion docs: `DEPTH_CAMERA.md` (design + perception plan), `NETWORKING.md`
(DDS/network). This guide is the **hands-on runbook**.

---

## 0. Know these 5 facts first (they save hours)

1. **The D555 is a NETWORK camera, not USB.** It computes depth on-board and
   publishes over Ethernet using **DDS**. Your host needs a **DDS-capable
   librealsense** to talk to it.
2. **No prebuilt librealsense for ARM/Jetson has DDS.** Both the ROS deb and
   Intel's arm64 apt deb are built *without* DDS → they will say
   "No device detected." **You must build librealsense from source with
   `-DBUILD_WITH_DDS=ON`** (Step 4). This is the crux.
3. **Factory IP is static `192.168.11.55`** (NOT DHCP). Put your host NIC on that
   subnet to reach it.
4. **No RJ45 LED ever lights up.** That is normal on the D555 — not a fault.
5. **Needs MTU 9000 (jumbo) to stream.** A jumbo *ping* fails (camera ignores big
   ICMP) — that's a red herring; real streaming works. Don't judge jumbo by ping.

---

## 1. Hardware hookup

- **Power:** the D555 via its **USB-C** input (or PoE if you have a PoE+jumbo
  switch). A standard USB-C supply (e.g. a Pi5 charger) works.
- **Data:** run an Ethernet cable **directly from the D555 to the Jetson
  `enP8p1s0`** port. Direct connect is fully sufficient for one camera — no jumbo
  switch required. (This temporarily takes the Jetson's only wired port; the Pi5
  link falls back to WiFi during testing — fine.)
- Give it ~1 minute to boot after powering. Confirm the physical link:
  ```bash
  cat /sys/class/net/enP8p1s0/carrier      # 1 = link up
  ethtool enP8p1s0 | grep -i "Link detected"
  ```

---

## 2. Host network config (temporary — see §9 to persist)

Put `enP8p1s0` on the camera's subnet at MTU 9000 (keeps the existing
`192.168.2.x` island IP too):

```bash
sudo ip addr add 192.168.11.70/24 dev enP8p1s0
sudo ip link set enP8p1s0 mtu 9000
```

## 3. Confirm the camera is alive

```bash
ping -c3 192.168.11.55        # should reply (this proves it booted + is reachable)
```
If it replies, the camera is healthy. (A *jumbo* ping `ping -M do -s 8972
192.168.11.55` will FAIL — ignore it, that's expected.)

---

## 4. Build librealsense WITH DDS (one-time, ~40 min)

Everything runs in the **`isaac_ros` container**. This is the step that makes the
camera visible.

```bash
docker exec -it isaac_ros bash

# inside the container:
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y build-essential cmake git pkg-config \
  libssl-dev libusb-1.0-0-dev libudev-dev libtinyxml2-dev \
  libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev libgtk-3-dev

git clone --depth 1 --branch v2.58.2 \
  https://github.com/realsenseai/librealsense /root/librealsense
cd /root/librealsense && mkdir build && cd build

cmake .. -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_WITH_DDS=ON -DBUILD_TOOLS=ON -DBUILD_GRAPHICAL_EXAMPLES=ON \
  -DFORCE_RSUSB_BACKEND=ON -DCHECK_FOR_UPDATES=OFF

make -j6            # ~38 min on Orin Nano; it also builds Fast-DDS
```
Binaries land in `/root/librealsense/build/Release/` (the lib gains ~10k DDS
symbols). Match the tag (`v2.58.2`) to your camera firmware line if it differs.

> **Already built?** The image `isaac_ros:langrobo-nav-stack-1.3-dds` already
> contains this build — skip Step 4 if you're on that image.

---

## 5. Enable DDS + first discovery

DDS is **off by default** and enabled via `~/.realsense-config.json`. The key is
`context.dds.enabled` (top-level `dds.enabled` is ignored!):

```bash
# inside the container (HOME=/root):
cat > /root/.realsense-config.json <<'JSON'
{ "context": { "dds": { "domain": 0, "enabled": true } } }
JSON

cd /root/librealsense/build/Release
export LD_LIBRARY_PATH=$PWD
unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE   # avoid DS-mode interference
./rs-enumerate-devices -s
```
Expect:
```
Device Name              Serial Number     Firmware Version
Intel RealSense D555     <serial>          7.56.x
```
If you get "No device detected": you're likely on a **DDS-less** librealsense
(check `nm -DC librealsense2.so | grep -c fastdds` → must be non-zero), or
`ROS_DISCOVERY_SERVER` is still set, or the config key is wrong.

---

## 6. CLI streaming / distance tests (headless, over SSH)

Quick numeric verification without a screen. (Prebuilt in the `-1.3-dds` image at
`/root/rsdist`, `/root/rsimu_live`.)

```bash
# live depth grid in meters — point the camera at things, read distances:
docker exec -it isaac_ros bash -c \
 'LD_LIBRARY_PATH=/root/librealsense/build/Release /root/rsdist'

# live IMU (hold still → ~9.8 on one axis; rotate → gyro spikes):
docker exec -it isaac_ros bash -c \
 'LD_LIBRARY_PATH=/root/librealsense/build/Release /root/rsimu_live'
```
`Ctrl-C` to stop. Source for these tools: `/root/rstest.cpp`, `/root/rsfeat.cpp`,
`/root/rsdist.cpp`, `/root/rsimu_live.cpp` (compile with
`g++ x.cpp -std=c++14 -I/root/librealsense/include -L/root/librealsense/build/Release -lrealsense2 -lpthread`).

---

## 7. The live VISUAL — RealSense Viewer on the Jetson's monitor

The Viewer we built runs on the **container's** librealsense, but the X display is
on the **host** and the X socket isn't shared into the container. Cleanest path:
**copy the build to the host and run the Viewer natively.** (Needs a monitor
connected to the Jetson.)

```bash
# on the HOST:
# 1. copy the built viewer + libs out of the container
rm -rf ~/rs_build
docker cp isaac_ros:/root/librealsense/build/Release ~/rs_build
docker cp isaac_ros:/lib/aarch64-linux-gnu/libglfw.so.3.3 ~/rs_build/libglfw.so.3  # host lacks glfw

# 2. enable DDS in the HOST user's config (SAME key: context.dds.enabled)
cat > ~/.realsense-config.json <<'JSON'
{ "context": { "dds": { "domain": 0, "enabled": true } } }
JSON

# 3. find your X display + auth cookie
#    DISPLAY is usually :1; cookie: /run/user/$(id -u)/gdm/Xauthority
echo "$DISPLAY"; ls /run/user/$(id -u)/gdm/Xauthority

# 4. launch it on the monitor
cd ~/rs_build
unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE
DISPLAY=:1 XAUTHORITY=/run/user/$(id -u)/gdm/Xauthority \
  LD_LIBRARY_PATH=~/rs_build ./realsense-viewer &
```

In the Viewer window on the monitor:
1. If the top-left says **"Add Source (0 available)"**, DDS is off in the Viewer's
   own setting. Open the **gear ⚙ → Settings → tick "Enable DDS"**, Domain ID `0`,
   Apply, then **restart the Viewer** (it writes `context.dds.enabled` to
   `~/.realsense-config.json`; changes need a restart). It should then show
   **"1 available."** *(Our config file in step 2 usually pre-enables this.)*
2. Click **Add Source → Intel RealSense D555**.
3. Toggle **Stereo Module** on = live **depth** (near=warm, far=cool); **RGB
   Camera** on = color; **Motion Module** on = IMU.
4. Click **"3D"** (top-right) for the live **point cloud** — drag to orbit.
5. Ignore the "UDEV-Rules missing" warning — that's for USB cameras only.

---

## 8. Verify accuracy

- **Hover** the mouse over the depth image → per-pixel distance (meters) shows at
  the cursor.
- Use the **Measure** (ruler) tool → click two points → it prints the distance.
- Ground-truth check: place the camera a **tape-measured** distance from a flat
  wall, point straight on, compare. D555 is typically within ~1–2% at these ranges
  (1.00 m should read ~0.98–1.02 m).

---

## 9. Make it permanent

The build, network, and config above are otherwise lost on restart/reboot.

- **Image (done):** the build is committed as `isaac_ros:langrobo-nav-stack-1.3-dds`
  and `docker-compose.yml` points at it. To re-commit after further changes:
  ```bash
  docker commit isaac_ros isaac_ros:langrobo-nav-stack-1.x-dds
  # then update the image: line in docker-compose.yml
  ```
- **Network (still runtime-only):** the `192.168.11.70` + MTU 9000 on `enP8p1s0`
  don't survive a reboot. For permanent use add them via netplan/NetworkManager,
  and/or move the camera onto the `192.168.2.x` island with `rs-eth-config`.
- **ROS integration (pending):** the ROS `realsense2_camera` wrapper still links
  the DDS-less lib. To feed cuVSLAM/RTAB-Map/nvblox/Nav2, rebuild/point the wrapper
  at the DDS build. Separate task.

---

## 10. Troubleshooting quick table

| Symptom | Cause / fix |
|---|---|
| `rs-enumerate-devices`: "No device detected" | DDS-less lib (build from source, §4); or `ROS_DISCOVERY_SERVER` still set (unset it); or wrong config key (use `context.dds.enabled`). |
| No RJ45 LED | Normal — D555 LEDs never light. Check `carrier`/`ping` instead. |
| `ping 192.168.11.55` fails | Host NIC not on `192.168.11.0/24` (§2), or camera still booting, or cable/port. |
| Jumbo `ping -M do -s 8972` fails | Expected — camera ignores oversized ICMP. Verify jumbo by actually streaming, not pinging. |
| Viewer "0 available" | Enable DDS in Viewer Settings (gear) + restart; ensure host `~/.realsense-config.json` has `context.dds.enabled:true`. |
| Viewer won't open on screen | Wrong `DISPLAY`/`XAUTHORITY`; confirm `echo $DISPLAY` and `/run/user/$(id -u)/gdm/Xauthority`. |
| Stream starts then no frames | MTU not 9000 on `enP8p1s0`, or a non-jumbo switch in the path (use direct connect). |

---

*Validated end-to-end on 2026-07-14: discovery, depth (up to 1280×720), color
(up to 1280×800), dual IR, IMU (200 Hz), jumbo streaming, and the live Viewer.
See memory `project_d555_bringup_findings` for the full diagnostic trail.*
