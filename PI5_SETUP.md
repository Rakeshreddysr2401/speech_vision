# Jetson ↔ Pi5 Networking & Runtime (Deployed)

Status: **working end-to-end** (voice → LLM → speech + motion verified across all 4 devices).

## Topology

```
Jetson (STT/TTS, ai_stack container)  ──eth 192.168.2.20──┐
                                                          │ direct cable (DDS only)
Pi5 (LangGraph brain + micro-ROS)     ──eth 192.168.2.10──┘
   │  └─wifi 192.168.1.x → internet, Mac Mini HTTP, ESP32 micro-ROS
Mac Mini (llama.cpp gemma)  ── wifi 192.168.1.7:8080  (HTTP, not DDS)
ESP32 (wheels/servo/IR)     ── wifi → Pi5 micro-ROS UDP 8888  (not DDS)
```

ROS2 DDS is **Ethernet-only** between Jetson and Pi5. WiFi carries everything that
isn't DDS. This avoids the WiFi-multicast problem (consumer routers drop multicast
between wireless clients, so ROS2 discovery silently failed over WiFi).

---

## Start the robot (everyday)

Two machines, two terminals:

```bash
# 1) JETSON — vision + voice  (aliases live in ~/.bashrc)
docker compose up -d        # only after a reboot
robot-up                    # camera + YOLOv8n target_node + STT + TTS   (Ctrl+C / robot-stop to stop)

# 2) PI5 — the brain
cd ~/ros2_ws && ./prod.sh           # local Mac Mini Gemma (free, private)
#   or: ./prod.sh openai            # OpenAI gpt-4o-mini (cloud, $) — both support vision/look()
```

Pre-reqs: Mac Mini llama.cpp running on `0.0.0.0:8080` (for `prod.sh`), a Bluetooth
speaker connected to the Jetson (for TTS), ESP32 powered (for movement).

Then just talk — it's always listening (no wake word): *"what do you see?"*,
*"go near the cup"*, *"move forward 20 centimeters"*.

**Stop everything:** Jetson `robot-stop`; Pi5 `pkill -f brain_launch`.

See the Jetson `README.md` "Quick Start" for the full alias cheat-sheet.

---

## Jetson side (done)

### 1. Static Ethernet IP — `enP8p1s0` → `192.168.2.20/24`, no gateway
```bash
sudo nmcli connection modify "Wired connection 1" \
  ipv4.addresses 192.168.2.20/24 ipv4.method manual ipv4.never-default yes
sudo nmcli connection up "Wired connection 1"
```

### 2. FastDDS config — `~/robot/config/fastdds_unicast.xml`
**Gitignored** (machine-specific). Mounted into both containers at `/config/fastdds_unicast.xml`;
both already set `FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_unicast.xml` in `docker-compose.yml`.
Whitelists the Jetson eth interface and lists both peers:
```xml
<?xml version="1.0" encoding="UTF-8" ?>
<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <profiles>
    <transport_descriptors>
      <transport_descriptor>
        <transport_id>eth_only</transport_id>
        <type>UDPv4</type>
        <interfaceWhiteList><address>192.168.2.20</address></interfaceWhiteList>
      </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="unicast_profile" is_default_profile="true">
      <rtps>
        <userTransports><transport_id>eth_only</transport_id></userTransports>
        <useBuiltinTransports>false</useBuiltinTransports>
        <builtin>
          <initialPeersList>
            <locator><udpv4><address>192.168.2.10</address></udpv4></locator>
            <locator><udpv4><address>192.168.2.20</address></udpv4></locator>
          </initialPeersList>
        </builtin>
      </rtps>
    </participant>
  </profiles>
</dds>
```

### 3. Voice stack
`ros2 launch voice_pkg voice.launch.py` inside `ai_stack` — `stt_node` (whisper_cuda) + `tts_node` (kokoro).
Restart after changing the DDS config so nodes pick up the new file.

---

## Pi5 side (done)

The Pi5 mirror config lives at `~/ros2_ws/fastdds_unicast.xml` (whitelist `192.168.2.10`,
same peer list). Static eth IP `192.168.2.10/24`, no gateway. Run scripts (see Pi5 repo
`README.md`):
- `./prod.sh` — production voice loop + LangSmith tracing (default Mac Mini gemma; `./prod.sh openai` for cloud)
- `./dev.sh` — LangGraph Studio (text/visual debugging)

Never run both — they share micro-ROS UDP 8888 and both publish `/cmd_vel`.

---

## Verify

```bash
# from Pi5, over ethernet DDS — should list Jetson nodes/topics
ros2 node list      # → /stt_node /tts_node /agent_node /rover_esp32 /camera_node /target_node
ros2 topic list     # → /voice/*, /camera/*, /vision/*, /cmd_vel, /ir_obstacle, /servo_angle

ping -c2 192.168.2.20   # Jetson over cable (~0.3ms)
```

---

## Vision — Jetson → Pi5 contract

The Jetson runs two vision nodes (`vision_pkg`, in the `ai_stack` container). The Pi5
LangGraph agent consumes them as tools.

### 1. Camera frames — for the `look()` tool

| Topic | Type | Direction |
|---|---|---|
| `/camera/color/image_raw` | `sensor_msgs/Image` (bgr8, 640×480 @ ~5 fps) | Jetson `camera_node` → Pi5 |
| `/camera/color/image_raw/compressed` | `sensor_msgs/CompressedImage` (JPEG) | Jetson `camera_node` → Pi5 |

The `look()` tool should subscribe to the **compressed** topic (lighter over DDS),
JPEG-decode it, and send the frame to Gemma 3n for open-vocabulary scene description
("what do you see"). Frames are published continuously while `camera_node` runs.

### 2. Object directions — the "go near the cup" nav tool

`target_node` (YOLOv8n) turns a named object into a steering signal. It is **idle**
until the agent sets a target, so it costs no GPU when unused.

| Topic | Type | Direction |
|---|---|---|
| `/vision/target` | `std_msgs/String` | Pi5 → Jetson `target_node` |
| `/vision/target_result` | `std_msgs/String` (JSON) | Jetson `target_node` → Pi5 |

**To start hunting:** publish the COCO class name (lower-case) on `/vision/target`,
e.g. `cup`, `bottle`, `chair`, `person`. Publish `""` (empty) to stop.

**Result** (published at ~5 Hz while a target is set):

```json
{
  "target": "cup",
  "found": true,         // false if not in frame this tick
  "bearing_x": -0.42,    // [-1..1]: -1 far left, 0 centred, +1 far right
  "rel_size": 0.18,      // box area / frame area [0..1] — proximity proxy (bigger = closer)
  "conf": 0.81,          // detection confidence
  "stamp": 1782636907.07 // image capture time (epoch s)
}
```

**Suggested agent control loop:**
- Turn toward target until `|bearing_x|` is small (e.g. `< 0.1`) → publish `/cmd_vel` angular.z proportional to `-bearing_x`.
- Drive forward while `rel_size` is below a stop threshold (e.g. `< 0.4`) → linear.x.
- Stop when `rel_size` ≥ threshold ("close enough") or `found` goes false for N ticks.

**Limits (mono webcam):** `rel_size` is relative, **not** metric distance; there is no
obstacle avoidance; only COCO classes work. Open-vocabulary targets ("the red mug")
need `look()` (Gemma) or the future D555/SLAM stack.

> Quick manual test from the Pi5:
> ```bash
> ros2 topic pub /vision/target std_msgs/msg/String "{data: person}"   # in one shell
> ros2 topic echo /vision/target_result                                # in another
> ```

---

## Network summary

| Device | Interface | IP | Carries |
|---|---|---|---|
| Jetson | enP8p1s0 | 192.168.2.20 | DDS (voice + camera + vision topics) |
| Pi5 | eth0 | 192.168.2.10 | DDS |
| Pi5 | wlan0 | 192.168.1.14 | internet, Mac Mini HTTP, ESP32 UDP |
| Mac Mini | wifi | 192.168.1.7 | llama.cpp HTTP :8080 |
| ESP32 | wifi | ~192.168.1.12 | micro-ROS UDP 8888 → Pi5 |
