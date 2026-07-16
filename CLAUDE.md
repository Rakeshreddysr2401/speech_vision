# speech_vision (Jetson Orin) — project guide

Perception + speech I/O for the LangRobo home robot ("Rakhi"). The brain is a
separate repo on the Pi5 (`~/ros2_ws`, packages langrobo_core/langrobo_ros);
this repo provides STT/TTS/music/camera/YOLO nodes. The cross-machine topic
contract is documented in VOICE_PIPELINE.md here and mirrored in the Pi5
repo's ARCHITECTURE.md — never change it in one repo only.

Read VOICE_PIPELINE.md before touching voice_pkg.
Read NETWORKING.md before touching any DDS/network config (docker-compose env,
`/etc/hosts`, discovery server). Read DEPTH_CAMERA.md for the RealSense D555 +
Isaac ROS perception (cuVSLAM/nvblox/Nav2) plan.

## Layout

- `ai_ws/src/voice_pkg/` — stt_node (AEC mic → wake word → VAD → Whisper),
  tts_node (Kokoro → ec_speaker), music_node (yt-dlp/ffmpeg → ec_speaker),
  wake_engine.py, vad_backend.py, wake_gate.py, audio_capture.py, backends
- `ai_ws/src/vision_pkg/` — camera_node (Brio → compressed frames),
  target_node (YOLOv8n bearing/size for visual servoing)
- `ai_ws/src/bringup_pkg/` — robot.launch.py (everything)
- `config/99-echo-cancel.conf` — host PipeWire AEC config (install to
  `~/.config/pipewire/pipewire.conf.d/`)
- `/model_store` (= `~/robot/models`) — whisper cache, YOLO weights,
  `wake/` (openWakeWord + silero onnx models)

## Runtime — EVERYTHING runs in the `ai_stack` docker container

- `~/robot/ai_ws` is bind-mounted at `/workspaces/ai_ws`: edit on host,
  build/run in container.
- Host PipeWire socket is shared: audio targets `ec_mic`/`ec_speaker`
  (AEC virtual devices) resolve against the HOST PipeWire.
- Python: `/opt/venv/bin/python3` (jetson-containers venv: torch CUDA,
  whisper, kokoro_onnx editable install).

## Hard-won gotchas (violating these cost hours)

1. **Build with the venv python**:
   `/opt/venv/bin/python3 -m colcon build --packages-select voice_pkg`
   Plain `colcon build` writes `#!/usr/bin/python3` shebangs → the editable
   kokoro_onnx (a .pth in the venv) is invisible → tts_node dies on import.
2. **Restarting the stack leaves zombie children.** `kill -INT` on the
   detached `ros2 launch` process does not reliably kill its nodes. Always
   `ps aux | grep _node` after, and kill leftovers — a stale camera_node
   holds /dev/video0 and the new one loops "No camera available".
3. **Container pip is pinned to the jetson-ai-lab index** (CUDA wheels only).
   Generic packages: `PIP_INDEX_URL=https://pypi.org/simple ... pip install
   --no-deps <pkg>`. NEVER install without --no-deps — it will "upgrade"
   torch/onnxruntime with incompatible builds.
4. **Do not import the `silero_vad` package** — it imports torchaudio, which
   is broken here (CUDA version mismatch vs torch). vad_backend.py loads the
   onnx file directly with onnxruntime; keep it that way.
5. **All robot audio must play to `ec_speaker`** (pw-cat `--target`). Audio
   played to the default sink bypasses the AEC reference and the robot hears
   itself again.
6. **Host PipeWire restart drops the BT speaker** — reconnect with
   `bluetoothctl connect D6:AA:BB:59:EF:B6`.
7. **DDS uses a Discovery Server now, not the XML profiles.** Containers set
   `ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811` and must BLANK
   `FASTRTPS_DEFAULT_PROFILES_FILE` (the image bakes it; leaving it set breaks
   DS discovery). Recreate a container (`docker compose up -d <svc>`) after env
   edits — a merely-running container keeps stale env. See NETWORKING.md.
8. **The JioAirFiber router (used as a switch) drops jumbo frames**, so it
   cannot carry the RealSense D555 depth stream (needs MTU 9000). A
   jumbo-capable gigabit switch is required — see DEPTH_CAMERA.md / NETWORKING.md.
9. **D555 ROS integration: set LD_LIBRARY_PATH AFTER setup.bash.** The apt
   librealsense (2.58.1, no DDS) in `/opt/ros/jazzy/lib/aarch64-linux-gnu/` gets
   prepended by `setup.bash`. Set `LD_LIBRARY_PATH=/root/librealsense/install/lib:$LD_LIBRARY_PATH`
   AFTER sourcing `setup.bash` so the DDS build wins. `run_perception_real.sh` does this.
10. **realsense-ros ≥4.58.2 changed the default camera_namespace to `camera`.**
    Topics publish at `/camera/camera0/…` (not `/camera0/…` as in 4.58.1). All
    downstream remappings (rgbd_odometry, rtabmap, nvblox) must use `/camera/camera0/…`.
    `realsense2_camera` was rebuilt from source (v4.58.2) in the workspace against
    `/root/librealsense/install/lib`; apt package is overridden by `install/setup.bash`.
11. **~/.realsense-config.json must use `context.dds` wrapper, not top-level `dds`.**
    `{"context":{"dds":{"enabled":true,"domain":0}}}` works; top-level `dds` key is
    ignored by the RS2 context constructor. `run_perception_real.sh` writes this on start.
12. **D555 launch args for `depth_module.*` are silently DROPPED** (params only
    exist after device connect). Emitter-off and global-time-off are enforced at
    runtime by the perception scripts on every start — never assume a launch arg
    took effect; `ros2 param get` it. Same family: `align_depth.enable` /
    `pointcloud.enable` accept values but publish nothing on the DDS driver.
13. **Localization backend is a one-word switch** —
    `~/workspaces/isaac_ros-dev/src/langrobo_perception/config/localization`:
    `rtabmap` (production, verified under motion 2026-07-16) or `cuvslam`
    (PARKED: the Humble sidecar starves it of frames/TF across the
    Jazzy↔Humble DDS boundary — /tf_static never deserializes across distros).
14. **Motor dead zone**: below ~60% PWM (cmd <≈0.18 m/s) wheels hum but don't
    turn. Nav2 output flows collision-monitor → `/cmd_vel_nav` →
    `cmd_vel_deadband.py` → `/cmd_vel`. Delete the shim only after the ESP32
    firmware reflash carries the same remap.
15. **Nav goals must carry a ZERO timestamp** — Nav2 re-transforms the original
    stamp on every replan; `now()` stamps age out of the 10s TF cache mid-drive
    and abort the goal ("extrapolation into the past").
16. **Moved the rover by hand → `robot restart`** (fresh SLAM origin). Kidnaps
    corrupt any tracker's map.

## Commands

```bash
# build voice_pkg (in container, venv python — see gotcha 1)
docker exec ai_stack bash -c "source /opt/ros/jazzy/setup.bash && cd /workspaces/ai_ws && /opt/venv/bin/python3 -m colcon build --packages-select voice_pkg"

# restart stack (see gotcha 2 — check for zombies!)
docker exec ai_stack bash -c 'kill -INT $(pgrep -f "ros2 launch bringup_pkg")'; sleep 8
docker exec -d ai_stack bash -c "source /opt/ros/jazzy/setup.bash && source /workspaces/ai_ws/install/setup.bash && ros2 launch bringup_pkg robot.launch.py >> /data/robot_launch.log 2>&1"

# logs / tests
tail -f ~/robot/data/robot_launch.log
# more tests: VOICE_PIPELINE.md "Quick tests"
```

## Access

The Pi5 (192.168.2.10) has passwordless SSH here (rakhi24@192.168.2.20) —
agents on the Pi5 work on this repo remotely: edit via ssh/rsync, build and
restart via docker exec, test over ROS2 topics (both machines share
ROS_DOMAIN_ID=0 + fastdds unicast config).
