---
name: d555-view
description: Bring up the RealSense D555 depth camera and start the live visualization (RealSense Viewer GUI + depth/IMU readouts). Use when the user wants to see/start the camera, view depth, or check what the camera sees.
---

Bring the RealSense **D555** online and start the live visualization. The camera
must be **directly cabled to the Jetson `enP8p1s0`** (or on a jumbo-capable
switch) and USB-C powered. Full background: `Realsense_D555_Testing_Guidelines.md`.

Do the steps in order. Report each result briefly. Stop and tell the user if a
step fails (with the likely cause from the troubleshooting notes).

## 1. Restore the host network config (runtime-only; lost on reboot)

```bash
ip addr show enP8p1s0 | grep -q 192.168.11.70 || sudo ip addr add 192.168.11.70/24 dev enP8p1s0
sudo ip link set enP8p1s0 mtu 9000
ip route show 239.0.0.0/8 | grep -q enP8p1s0 || sudo ip route add 239.0.0.0/8 dev enP8p1s0
```

## 2. Confirm the camera is reachable

```bash
ping -c3 -W1 192.168.11.55
```
- 0% loss → good, continue.
- 100% loss → the camera isn't reachable. Likely: not powered / not cabled
  directly to the Jetson / still booting. Ask the user to check power + the direct
  cable, then retry. (A jumbo `ping -M do -s 8972` failing is NORMAL — ignore it.)

## 3. Confirm DDS discovery (proves the DDS-enabled SDK sees it)

```bash
docker exec isaac_ros bash -c '
  cd /root/librealsense/build/Release
  unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE
  export LD_LIBRARY_PATH=$PWD
  timeout 25 ./rs-enumerate-devices -s'
```
Expect a line: `Intel RealSense D555  <serial>  <fw>`. If "No device detected", see
troubleshooting.

## 4. Ask the user how they want to view it

Only the GUI needs a monitor. Ask (or infer from what they say):
- **GUI (RealSense Viewer)** — needs a monitor/VNC on the Jetson → step 5.
- **Text (over SSH, no monitor)** — live depth grid + IMU → step 6.

## 5. Launch the RealSense Viewer on the Jetson's monitor

Ensure the host build + DDS config exist, then launch:

```bash
# (re)create the host viewer build if missing
if [ ! -x ~/rs_build/realsense-viewer ]; then
  rm -rf ~/rs_build
  docker cp isaac_ros:/root/librealsense/build/Release ~/rs_build
  docker cp isaac_ros:/lib/aarch64-linux-gnu/libglfw.so.3.3 ~/rs_build/libglfw.so.3
fi
# enable DDS for the host user (CORRECT key is context.dds.enabled)
cat > ~/.realsense-config.json <<'JSON'
{ "context": { "dds": { "domain": 0, "enabled": true } } }
JSON
# detect the display + X auth cookie (usually :1)
DISP=$(loginctl show-session $(loginctl | awk '/seat0/{print $1; exit}') -p Display --value 2>/dev/null)
DISP=${DISP:-:1}
XAUTH=/run/user/$(id -u)/gdm/Xauthority
# close any stale viewer so re-runs don't stack windows
pkill -f 'rs_build/realsense-viewer' 2>/dev/null || true
# launch
cd ~/rs_build
unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE
DISPLAY="$DISP" XAUTHORITY="$XAUTH" LD_LIBRARY_PATH=~/rs_build nohup ./realsense-viewer >/tmp/viewer.log 2>&1 &
true   # keep the block's exit status clean
```
Confirm it started with `pgrep -f '[r]ealsense-viewer'` (the `[r]` avoids the
grep matching its own command line).

Then tell the user, on the monitor:
1. If it says **"Add Source (0 available)"** → open **gear ⚙ → Settings → tick
   "Enable DDS"**, Domain ID `0`, Apply, **restart the Viewer**. (Usually already
   enabled by the config above.)
2. **Add Source → Intel RealSense D555**.
3. Toggle **Stereo Module** = depth, **RGB Camera** = color, **Motion Module** = IMU.
4. Click **"3D"** (top-right) for the live point cloud.
5. **Hover** over depth for per-pixel meters, or use the **Measure** ruler (click 2
   points) to check distances. Ignore the "UDEV-Rules missing" warning.

## 6. Text visualization (headless / SSH)

```bash
# live depth grid in meters (Ctrl-C to stop):
docker exec -it isaac_ros bash -c 'LD_LIBRARY_PATH=/root/librealsense/build/Release /root/rsdist'
# live IMU:
docker exec -it isaac_ros bash -c 'LD_LIBRARY_PATH=/root/librealsense/build/Release /root/rsimu_live'
```
These need an interactive terminal, so give the user the command to run in their
own SSH shell rather than running it yourself (it streams until Ctrl-C).

## Troubleshooting

- **"No device detected" / "0 available":** (a) `ROS_DISCOVERY_SERVER` still set —
  unset it; (b) wrong config key — must be `context.dds.enabled` (top-level
  `dds.enabled` is ignored); (c) running the DDS-less apt SDK instead of the build
  in `/root/librealsense/build/Release` (verify: `nm -DC librealsense2.so | grep -c
  fastdds` must be > 0); (d) camera not on `192.168.11.x` (step 1/2).
- **Viewer window doesn't appear:** wrong `DISPLAY`/`XAUTHORITY`; confirm the
  active session's display and `/run/user/$(id -u)/gdm/Xauthority`.
- **Streams start then no frames:** MTU not 9000 on `enP8p1s0`, or a non-jumbo
  switch (e.g. the Jio router) in the path — use a direct cable or a jumbo switch.
- Deeper diagnostics + the whole story: memory `project_d555_bringup_findings`.
