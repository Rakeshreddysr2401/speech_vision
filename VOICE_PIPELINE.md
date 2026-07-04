# Voice Pipeline — how listening/speaking/music works (since dev-1.0.4)

Upgraded 2026-07-03 to Alexa-style full-duplex audio. Design rationale and
acceptance tests live in the Pi5 repo (`ros2_ws/JETSON_VOICE_UPGRADE.md`);
this file is how it works HERE and how to operate it.

## The pipeline

```
boAt speaker ◄── ec_speaker (virtual sink) ◄── TTS (tts_node) + music (music_node)
                     │ (playback = AEC reference)
boAt mic ──► PipeWire webrtc echo-cancel ──► ec_mic (virtual source: mic − robot audio)
                                                │
                                     stt_node audio loop (80ms chunks)
                                                │
                              openWakeWord — EVERY chunk, ~1ms CPU
                               ├─ wake hit → capture window opens (8s)
                               │    └─ during TTS → barge-in: halt TTS, keep listening
                               └─ no window open → chunk DISCARDED (never transcribed)
                                                │ (window open)
                              Silero VAD (onnx) — endpoint the utterance
                                                │
                              Whisper (whisper_cuda) → wake_gate strips the name
                                                │
                              /voice/user_input → Pi5 brain
```

- **AEC** — host PipeWire module (`config/99-echo-cancel.conf`, installed at
  `~/.config/pipewire/pipewire.conf.d/`). Creates `ec_mic` + `ec_speaker`.
  Everything the robot plays MUST target `ec_speaker` or the canceller can't
  subtract it. The container reaches these via the shared host PipeWire socket.
- **Wake word** — `voice_pkg/wake_engine.py`, openWakeWord ONNX models from
  `/model_store/wake/` (persistent host mount `~/robot/models/wake`).
  Currently `hey_jarvis_v0.1` (say "hey jarvis"); swap to `hey_rakhi` in
  `voice_params.yaml` once trained (openWakeWord Colab, ~1hr).
  If openwakeword/models are missing, stt_node automatically falls back to the
  legacy transcribe-everything + transcript-gate mode (logged at startup).
- **VAD** — `voice_pkg/vad_backend.py`. Silero run DIRECTLY with onnxruntime
  (model file: `/model_store/wake/silero_vad.onnx`). Do NOT import the
  `silero_vad` package — it imports torchaudio, which is broken in this
  container (torch/torchaudio CUDA mismatch). webrtcvad is the fallback.
- **Stop keyword** — while TTS or music plays, short speech bursts on the
  echo-cancelled stream are transcribed and checked for "stop". On hit:
  stop music (local `/audio/music_cmd`), then `/voice/tts_stop` (tts_node
  flushes; the Pi5 stops the wheels). Local-first = <400ms.
- **Music** — `voice_pkg/music_node.py`: yt-dlp resolves a query → ffmpeg
  decodes the stream → numpy applies volume/ducking → pw-cat to `ec_speaker`.
  Ducks to `duck_percent` (25%) while `/voice/tts_speaking` is true.
  Contract (`/audio/music_cmd` in, `/audio/music_state` out — on change + 1Hz
  while playing, `stamp` = wall clock) matches the Pi5 brain's music tools.

## Topics (Pi5 ⇄ Jetson contract — do not change unilaterally)

| Topic | Dir | Payload |
|---|---|---|
| `/voice/user_input` | →Pi5 | wake-gated transcript (name stripped) |
| `/voice/robot_speech` | ←Pi5 | sentence chunks, utterance ends with `<\|eou\|>` |
| `/voice/tts_speaking` | →Pi5 | True from first chunk until EOU played (also ducks music) |
| `/voice/tts_stop` | →Pi5 | stop keyword / `[wake:...]` barge-in marker |
| `/audio/music_cmd` | ←Pi5 | `{"action": play\|pause\|resume\|stop\|volume, "query"/"level", "t"}` |
| `/audio/music_state` | →Pi5 | `{"playing","paused","title","volume","error","stamp","cmd_t"}` — `cmd_t` echoes the play cmd's `t`; Pi5 confirms playback on it as an opaque token (never clock-compares `stamp`) |

## Operating it

Everything runs inside the `ai_stack` container; `~/robot/ai_ws` is
bind-mounted at `/workspaces/ai_ws` (edit on host = edit in container).

```bash
# Build (MUST use the venv python — plain colcon writes /usr/bin/python3
# shebangs and the editable kokoro_onnx install becomes invisible):
docker exec ai_stack bash -c "source /opt/ros/jazzy/setup.bash && cd /workspaces/ai_ws && /opt/venv/bin/python3 -m colcon build --packages-select voice_pkg"

# Restart the stack (kill the launch, THEN CHECK FOR SURVIVING CHILDREN —
# kill -INT on the detached launch does not always propagate; a stale
# camera_node keeps /dev/video0 busy and the new one can't open it):
docker exec ai_stack bash -c 'kill -INT $(pgrep -f "ros2 launch bringup_pkg")'
sleep 8
docker exec ai_stack bash -c 'ps aux | grep -E "_node" | grep -v grep'   # kill -9 leftovers
docker exec -d ai_stack bash -c "source /opt/ros/jazzy/setup.bash && source /workspaces/ai_ws/install/setup.bash && ros2 launch bringup_pkg robot.launch.py >> /data/robot_launch.log 2>&1"

# Logs
tail -f ~/robot/data/robot_launch.log

# Python deps (container pip is pinned to the jetson-ai-lab index, which
# lacks generic packages — override per-install, --no-deps to protect the
# CUDA-built torch/onnxruntime):
docker exec ai_stack bash -c "PIP_INDEX_URL=https://pypi.org/simple /opt/venv/bin/python3 -m pip install --no-deps <pkg>"
```

After a PipeWire restart on the host the boAt BT speaker may drop:
`bluetoothctl connect D6:AA:BB:59:EF:B6` — wireplumber re-attaches the
echo-cancel streams automatically once it's back.

## Quick tests

```bash
# ec nodes exist
pw-cli ls Node | grep -E "ec_mic|ec_speaker"
# wake model sanity (synthesized voice, no mic needed)
docker exec ai_stack bash -c "cd /workspaces/ai_ws/src/voice_pkg && /opt/venv/bin/python3 -c \"
import sys; sys.path.insert(0,'.')
import numpy as np, scipy.signal as sps
from kokoro_onnx import Kokoro
from voice_pkg.wake_engine import WakeEngine
k=Kokoro('/opt/kokoro-onnx/examples/kokoro-v1.0.onnx','/opt/kokoro-onnx/examples/voices-v1.0.bin')
w=WakeEngine(['hey_jarvis_v0.1'],0.5)
s,sr=k.create('hey jarvis',voice='af_heart',speed=1.0,lang='en-us')
pcm=(np.clip(sps.resample_poly(s,16000,sr),-1,1)*32767).astype(np.int16)
pcm=np.concatenate([np.zeros(16000,np.int16),pcm,np.zeros(16000,np.int16)])
print('triggers:',sum(bool(w.detect(pcm[i:i+1280])) for i in range(0,len(pcm)-1280,1280)))\""
# music round-trip (plays audio out loud!)
ros2 topic pub --once /audio/music_cmd std_msgs/msg/String '{data: "{\"action\":\"play\",\"query\":\"test song\",\"t\":0}"}'
ros2 topic echo --once /audio/music_state std_msgs/msg/String
ros2 topic pub --once /audio/music_cmd std_msgs/msg/String '{data: "{\"action\":\"stop\",\"t\":0}"}'
```

## Remaining work

1. Train `hey_rakhi` (openWakeWord Colab) → drop in `/model_store/wake/` →
   `wake_models: ["hey_rakhi"]` in voice_params.yaml → rebuild not needed
   (params only), restart the stack.
2. On-mic tuning: `wake_threshold` (0.3 sensitive ↔ 0.7 strict),
   stop-over-music range. The boAt BT mic is the weakest link — the planned
   USB conference speakerphone (hardware AEC + mic array) replaces both
   `ec_*` routing (select its device) and improves range.
