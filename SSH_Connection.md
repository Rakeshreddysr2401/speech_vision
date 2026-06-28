# SSH Access — Jetson & Pi5 from a Laptop

How to SSH into the two Linux brains of the robot (**Jetson Orin Nano** and
**Raspberry Pi 5**) from an external device such as your laptop.

> The Mac Mini (llama.cpp) and ESP32 are *not* SSH targets here — the Mac Mini is
> reached over HTTP and the ESP32 over micro-ROS. See [README.md](README.md) and
> [PI5_SETUP.md](PI5_SETUP.md) for the full topology.

---

## 1. Device & Network Reference

| Device | Hostname (mDNS) | WiFi IP (`192.168.1.x`) | Direct-cable IP (`192.168.2.x`) | User |
|--------|-----------------|--------------------------|----------------------------------|------|
| Jetson Orin Nano 8GB | `localhost.localdomain` * | `192.168.1.15` | `192.168.2.20` (iface `enP8p1s0`) | `rakhi24` |
| Raspberry Pi 5 | `raspberrypi.local` | `192.168.1.x` (DHCP) | `192.168.2.10` (eth) | `rakhi24` |

\* The Jetson's hostname is the default `localhost.localdomain`, so mDNS (`.local`)
is unreliable for it — **prefer its IP address**. Avahi/mDNS is running on both
devices.

**Two networks exist on purpose:**

- **`192.168.1.x` (WiFi)** — shared LAN with internet and your laptop. **This is the
  one your laptop uses to SSH in.**
- **`192.168.2.x` (direct Ethernet cable, Jetson ↔ Pi5 only)** — a private link used
  exclusively for ROS2 DDS traffic. Your laptop is **not** on this network and cannot
  reach `192.168.2.x` addresses unless plugged into that cable.

> ⚠️ WiFi IPs are DHCP-assigned and can change after a reboot. If a connection fails,
> re-check the current IP (see [Finding a changed IP](#finding-a-changed-ip)).

---

## 2. Prerequisites

- Your laptop and the robot must be on the **same WiFi network** (`192.168.1.x`).
- An SSH client:
  - **macOS / Linux** — built in (`ssh`).
  - **Windows** — use the built-in OpenSSH client in PowerShell/Terminal, or PuTTY.
- SSH server is **already running** on the Jetson (`ssh` service is `active`) and on
  the Pi5. Default port **22**, password authentication **enabled**.

Credentials:

| Device | Command target | Password |
|--------|----------------|----------|
| Jetson | `rakhi24@192.168.1.15` | *(Jetson login password)* |
| Pi5    | `rakhi24@<pi5-wifi-ip>` or `rakhi24@raspberrypi.local` | *(Pi5 login password)* |

---

## 3. Connect from your Laptop

### Jetson

```bash
ssh rakhi24@192.168.1.15
```

### Pi5

```bash
# by mDNS hostname (works because the Pi5 has a real hostname)
ssh rakhi24@raspberrypi.local

# or by IP
ssh rakhi24@192.168.1.<pi5-ip>
```

On first connection you'll be asked to accept the host key — type `yes`.

---

## 4. Passwordless Login (SSH Keys) — Recommended

Avoids typing passwords and is required for non-interactive tooling. Run these on
your **laptop**.

```bash
# 1. Generate a key if you don't already have one
ssh-keygen -t ed25519 -C "laptop"

# 2. Copy the public key to each device
ssh-copy-id rakhi24@192.168.1.15        # Jetson
ssh-copy-id rakhi24@raspberrypi.local   # Pi5
```

> No SSH keys are installed on the Jetson yet (`~/.ssh/*.pub` is empty), so the first
> `ssh-copy-id` will prompt for the password.

### Optional: `~/.ssh/config` shortcuts (laptop)

Add to `~/.ssh/config` so you can type `ssh jetson` / `ssh pi5`:

```sshconfig
Host jetson
    HostName 192.168.1.15
    User rakhi24

Host pi5
    HostName raspberrypi.local
    User rakhi24
```

Then simply:

```bash
ssh jetson
ssh pi5
```

---

## 5. Hopping Between Devices

The Jetson and Pi5 can also reach each other over the **direct Ethernet cable**
(`192.168.2.x`), which is independent of WiFi:

```bash
# From Jetson → Pi5 (over the direct cable)
sshpass -p '<pi5-password>' ssh rakhi24@192.168.2.10

# From Pi5 → Jetson (over the direct cable)
ssh rakhi24@192.168.2.20
```

To reach the Pi5 *through* the Jetson from your laptop without being on the cable
network, use a jump host:

```bash
ssh -J rakhi24@192.168.1.15 rakhi24@192.168.2.10
```

---

## 6. Useful Extras

### Copy files (SCP)

```bash
# laptop → Jetson
scp ./file.txt rakhi24@192.168.1.15:/home/rakhi24/robot/

# Pi5 → laptop
scp rakhi24@raspberrypi.local:/home/rakhi24/ros2_ws/log.txt ./
```

### Keep long sessions alive

ROS2 / Docker work can be long-running. Use `tmux` on the device so sessions survive
a dropped SSH connection:

```bash
ssh rakhi24@192.168.1.15
tmux new -s robot      # later: tmux attach -t robot
```

### Forward a port to your laptop (e.g. LangGraph Studio on the Pi5)

```bash
# View the Pi5's 127.0.0.1:2024 (dev.sh / LangGraph Studio) in your laptop browser
ssh -L 2024:127.0.0.1:2024 rakhi24@raspberrypi.local
# then open http://127.0.0.1:2024 on the laptop
```

---

## 7. Troubleshooting

### Finding a changed IP

WiFi IPs are DHCP and may change. To find the current IP, run **on the device**
(e.g. via a directly-attached keyboard/monitor):

```bash
ip -4 addr show wlP1p1s0    # Jetson WiFi interface
hostname -I                 # quick: prints all IPs
```

Or scan from the laptop:

```bash
# Linux/mac, replace subnet if needed
nmap -sn 192.168.1.0/24 | grep -i -B2 jetson
arp -a | grep -i -E '9c:c7:d3|raspberry'   # Jetson MAC: 9c:c7:d3:f6:b1:f1
```

### Common errors

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Connection timed out` | Laptop not on `192.168.1.x` WiFi, or IP changed — recheck IP. |
| `Connection refused` | SSH service down on target: `sudo systemctl status ssh`. |
| `No route to host` for a `192.168.2.x` address | That's the direct-cable network — your laptop isn't on it. Use the WiFi IP or a jump host (§5). |
| `Host key verification failed` after reimage/IP reuse | `ssh-keygen -R <ip-or-host>` on the laptop, then reconnect. |
| `raspberrypi.local` won't resolve | mDNS issue — use the Pi5's WiFi IP directly. |

### Verify SSH is listening (on the device)

```bash
systemctl is-active ssh        # → active
sudo ss -tlnp | grep :22       # confirm port 22 is listening
```

---

## 8. Security Notes

- Password auth is currently enabled on both devices. After setting up SSH keys
  (§4), consider disabling password login in `/etc/ssh/sshd_config`
  (`PasswordAuthentication no`) and restarting `ssh`.
- These devices are reachable to anything on the WiFi LAN — keep them on a trusted
  network, not an open/public one.
- Do not expose port 22 to the public internet without a VPN or key-only auth.
