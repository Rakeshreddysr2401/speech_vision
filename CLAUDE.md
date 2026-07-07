# speech_vision (Jetson Orin) — project guide

Perception + speech I/O for the LangRobo home robot ("Rakhi"). The brain is a
separate repo on the Pi5 (`~/ros2_ws`, packages langrobo_core/langrobo_ros);
this repo provides STT/TTS/music/camera/YOLO nodes. The cross-machine topic
contract is documented in VOICE_PIPELINE.md here and mirrored in the Pi5
repo's ARCHITECTURE.md — never change it in one repo only.

Read VOICE_PIPELINE.md before touching voice_pkg.

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

## Fleet roles — this Jetson has two, one per robot body

`scripts/fleet_role.sh {voice|perception} {start|stop|status}` (called by the Pi5's
`scripts/fleet.sh` over ssh, or run here directly):

- **`voice`** = the **real-rover** role — starts the `ai_stack` container's STT/TTS/music/
  camera/YOLO launch (`ros2 launch bringup_pkg robot.launch.py`). Handles the zombie-node
  cleanup from gotcha 2 automatically.
- **`perception`** = the **simulation** role — brings up the `isaac_ros` container so its
  nvblox / visual-SLAM / nav pipelines can consume the sim's D555-style `/cam_1/*` topics.
  (The pipeline launches inside that container are still manual for now.)

The Pi5 picks the role: `fleet.sh sim` → `perception`, `fleet.sh rover` → `voice`. The real
rover has no depth cam / lidar / imu yet, so `rover` mode does not start `isaac_ros`.

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

The Pi5 has passwordless SSH here (`ssh rakhi24@rakhi-jetson.local`; the
192.168.2.x cable link was reported physically dead 2026-07-05) — agents on
the Pi5 work on this repo remotely: edit via ssh/rsync, build and restart via
docker exec, test over ROS2 topics. Cross-machine discovery is the Fast DDS
Discovery Server on the Pi5 (`ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811`,
see Pi5 `~/ros2_ws/NETWORKING.md`); the old fastdds unicast xml is retired.
All machines use ROS_DOMAIN_ID=0.

## Simulation laptop (rover_sim) — the stand-in robot body

Until the real rover exists, a Gazebo sim on the laptop (`rakhi24`) provides
the robot: mecanum X3 rover + lidar + RGBD camera in a furnished house world,
Nav2 + slam_toolbox on top. Repo: https://github.com/Rakeshreddysr2401/rover_sim
(laptop path `/workspace/ros2_ws/src/rover_sim`); its `docs/INTERFACE.md` is
the authoritative topic contract.

For THIS repo, what matters: the sim publishes RealSense-D555-style camera
topics natively — `/cam_1/color/image_raw`, `/cam_1/color/camera_info`,
`/cam_1/depth/image_rect_raw` (32FC1 m, 8 m range, frame
`cam_1_depth_optical_frame`), `/cam_1/depth/camera_info`,
`/cam_1/depth/color/points`, all 15 Hz sim-time — so nvblox / visual-SLAM /
YOLO pipelines in the isaac_ros container can be developed against the sim
without remapping. The sim laptop joins the same discovery server
(`ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811` exported there before
launch). Sim runs ≈0.1× real time in the house world — don't tune wall-clock
timeouts against it. Keep machine/interface details in sync across the three
CLAUDE.md files (Pi5 `~/ros2_ws`, this repo, rover_sim) — change all or none.
