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
ros2 node list      # → /stt_node /tts_node /agent_node /rover_esp32
ros2 topic list     # → /voice/*, /cmd_vel, /ir_obstacle, /servo_angle

ping -c2 192.168.2.20   # Jetson over cable (~0.3ms)
```

---

## Network summary

| Device | Interface | IP | Carries |
|---|---|---|---|
| Jetson | enP8p1s0 | 192.168.2.20 | DDS (voice topics, future camera) |
| Pi5 | eth0 | 192.168.2.10 | DDS |
| Pi5 | wlan0 | 192.168.1.14 | internet, Mac Mini HTTP, ESP32 UDP |
| Mac Mini | wifi | 192.168.1.7 | llama.cpp HTTP :8080 |
| ESP32 | wifi | ~192.168.1.12 | micro-ROS UDP 8888 → Pi5 |
