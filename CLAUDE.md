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

The Pi5 (192.168.2.10) has passwordless SSH here (rakhi24@192.168.2.20) —
agents on the Pi5 work on this repo remotely: edit via ssh/rsync, build and
restart via docker exec, test over ROS2 topics (both machines share
ROS_DOMAIN_ID=0 + fastdds unicast config).
