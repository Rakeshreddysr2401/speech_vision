# Networking — how Pi5, Jetson (and the depth camera) talk

How the machines find each other and exchange ROS2 topics. Read this before
touching anything network-related (docker-compose env, `/etc/hosts`, DDS).
Mirrors the Pi5 repo's `~/ros2_ws/NETWORKING.md`.

## The machines

| | Ethernet (wired island) | WiFi (internet) | ROS runs in |
|---|---|---|---|
| **Jetson** | `enP8p1s0` **192.168.2.20** (static, no gateway) | `wlP1p1s0` 192.168.1.15 | `ai_stack` + `isaac_ros` containers (host net) |
| **Pi5** | `eth0` **192.168.2.10** (static, no gateway) | `wlan0` 192.168.1.16 | host |

- **`192.168.2.x` is a private, isolated wired island** — static IPs, **no
  gateway**. It carries only robot↔robot ROS2 traffic. Because there's no
  gateway and no dependency on the box in the middle, this island is
  **portable**: it works the same whether the two ports are joined by a direct
  cable, the JioAirFiber router used as a dumb switch, or a future dedicated
  switch mounted on the robot. Swapping the physical switch changes nothing.
- **Internet** (Telegram, cloud LLM, apt) comes from **Airtel WiFi**
  (`192.168.1.1`, the default route) — NOT from the JioAirFiber ethernet.
- **Physical today (2026-07):** both ethernet ports plug into the **JioAirFiber
  router, used purely as an L2 switch** (replaced the old flaky direct cable).

## DDS discovery — Fast DDS Discovery Server (NOT multicast)

All ROS2 nodes find each other through a single **"meeting point"**: a Fast DDS
Discovery Server that runs on the Pi5. Nobody uses multicast discovery.

```
                 Pi5 Discovery Server  (systemd: langrobo-discovery)
                 fastdds discovery -i 0 -p 11811   (listens 0.0.0.0)
                          ▲                 ▲
        127.0.0.1:11811   │                 │  rakhi24-desktop.local:11811
     ┌────────────────────┴───┐         ┌───┴──────────────────────────┐
   Pi5 nodes                          Jetson containers
   (brain, micro-ROS)                 (ai_stack voice, isaac_ros perception)
```

**Pi5 side** (`~/ros2_ws/scripts/*.sh`): `ROS_DISCOVERY_SERVER=127.0.0.1:11811`
(the server is local to the Pi5).

**Jetson side** (`docker-compose.yml`, both containers):
```yaml
- RMW_IMPLEMENTATION=rmw_fastrtps_cpp
- FASTRTPS_DEFAULT_PROFILES_FILE=      # blanked — see gotcha 1
- ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811
```
`rakhi24-desktop.local` is pinned to the **ethernet** IP in the Jetson host
`/etc/hosts`:
```
192.168.2.10 rakhi24-desktop.local   # ethernet via JioAirFiber switch
```
(Containers run with `network_mode: host`, so they inherit this hosts entry.)

## Gotchas (each cost real time)

1. **Blank, don't set, `FASTRTPS_DEFAULT_PROFILES_FILE`.** The Jetson container
   images bake `FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_unicast.xml`.
   With Discovery-Server mode that XML profile conflicts with client init and
   the container sees only `/rosout` `/parameter_events` — no cross-machine
   topics. Fix: set it to empty (`FASTRTPS_DEFAULT_PROFILES_FILE=`) in
   docker-compose to override the image value. (`config/fastdds_unicast.xml` is
   now **legacy/unused**; kept only for reference.) A harmless
   `XMLPARSER ... realpath failed` warning remains from the empty path — ignore.
2. **`ros2 topic list` / `ros2 node list` lie in DS mode on this Jazzy build** —
   they often return almost nothing even when pub/sub works. **Diagnose with
   real data**: echo a continuously-published topic (the camera) instead.
3. **Restarting only one container doesn't re-read compose env.** After editing
   docker-compose env, `docker compose up -d <svc>` to *recreate* that
   container. A container that's merely "running" keeps its old env (this bit us
   — `isaac_ros` kept a stale WiFi discovery IP until recreated).

## Verify it works

```bash
# link up?
ping -c2 192.168.2.10                       # Jetson → Pi5 over the switch

# cross-machine ROS2 (THE definitive test — echo live camera both directions):
# from Pi5:
ssh rakhi24@192.168.2.10 "source /opt/ros/jazzy/setup.bash && \
  ROS_DISCOVERY_SERVER=127.0.0.1:11811 RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  ros2 topic echo --once /camera/color/image_raw/compressed \
  sensor_msgs/msg/CompressedImage --field format"        # prints 'jpeg' = works
# from a Jetson container:
docker exec isaac_ros bash -c "source /opt/ros/jazzy/setup.bash && \
  ros2 topic echo --once /camera/color/image_raw/compressed \
  sensor_msgs/msg/CompressedImage --field format"
```

## Jumbo frames — the JioAirFiber router CANNOT carry the depth camera

The RealSense D555 streams depth in **9000-byte "jumbo" frames** (MTU 9000).
**Tested 2026-07-12:** with both `eth0`/`enP8p1s0` set to MTU 9000, a
`ping -M do -s 8972 192.168.2.10` through the JioAirFiber router = **100% loss**
(1500-byte frames are fine). Both NICs *support* jumbo (Pi5 `macb` maxmtu 10222,
Jetson maxmtu 9194) — **the router in the middle drops oversize frames.**

Consequence: **the JioAirFiber-as-switch will not work for the D555.** Options:
- **Buy a jumbo-frame-capable gigabit switch** (MTU ≥ 9000). PoE optional if the
  camera is powered by USB-C. This is the clean, permanent answer.
- **Direct D555 → Jetson `enP8p1s0`** (Jetson port does jumbo). Works for a quick
  test, but the Jetson has only ONE ethernet port, so the wired Pi5 link then
  falls back to WiFi during the test.

To re-run the jumbo test (needs Pi5 sudo; control eth0 via the WiFi IP so the
flap doesn't cut your SSH):
```bash
ssh rakhi24@192.168.1.16 "echo 'R@khi2430' | sudo -S -p '' bash -c \
  'ip link set eth0 down; ip link set eth0 mtu 9000; ip link set eth0 up'"
sudo ip link set enP8p1s0 mtu 9000
ping -c3 -M do -s 8972 192.168.2.10        # 0% loss = switch passes jumbo
# revert both to 1500 afterwards
```

## Files that hold this config

- `docker-compose.yml` — container DDS env (RMW, blank FASTRTPS, DISCOVERY_SERVER)
- Jetson host `/etc/hosts` — `rakhi24-desktop.local → 192.168.2.10` (NOT in git)
- Pi5 `~/ros2_ws/scripts/*.sh` + `langrobo-discovery.service` — the server + clients
- `config/fastdds_unicast.xml` — legacy, unused (profile path is blanked)
