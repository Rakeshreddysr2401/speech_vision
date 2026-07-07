#!/usr/bin/env bash
# Jetson fleet roles — called by the Pi5's scripts/fleet.sh over ssh, or run
# directly on the Jetson:
#
#   ./fleet_role.sh voice      start|stop|status   # ai_stack: STT/TTS/music/camera/YOLO
#   ./fleet_role.sh perception start|stop|status   # isaac_ros: nvblox/visual-SLAM/nav pipelines
#
# voice  = the real-rover role (speech I/O; rover has no depth cam/lidar yet).
# perception = the sim role (consumes rover_sim's D555-style /cam_1/* topics).
#
# Wraps the procedures documented in CLAUDE.md — especially the zombie-node
# gotcha: killing the detached `ros2 launch` does NOT reliably kill its nodes.

set -eo pipefail
ROLE="${1:-}"
CMD="${2:-status}"

LAUNCH_PATTERN='ros2 launch bringup_pkg'

voice_start() {
    docker start ai_stack >/dev/null 2>&1 || true
    if ! docker inspect -f '{{.State.Running}}' ai_stack 2>/dev/null | grep -q true; then
        echo "fleet_role: ai_stack container failed to start" >&2; exit 1
    fi
    # Already launched?
    if docker exec ai_stack pgrep -f "$LAUNCH_PATTERN" >/dev/null 2>&1; then
        echo "fleet_role: voice already running"; return
    fi
    # Zombie cleanup before a fresh launch (CLAUDE.md gotcha 2)
    docker exec ai_stack bash -c 'pkill -9 -f "[_]node" 2>/dev/null; true'
    sleep 2
    docker exec -d ai_stack bash -c "source /opt/ros/jazzy/setup.bash && source /workspaces/ai_ws/install/setup.bash && ros2 launch bringup_pkg robot.launch.py >> /data/robot_launch.log 2>&1"
    echo "fleet_role: voice launching (log: ~/robot/data/robot_launch.log)"
}

voice_stop() {
    if docker inspect -f '{{.State.Running}}' ai_stack 2>/dev/null | grep -q true; then
        docker exec ai_stack bash -c "kill -INT \$(pgrep -f '$LAUNCH_PATTERN') 2>/dev/null; true"
        sleep 8
        docker exec ai_stack bash -c 'pkill -9 -f "[_]node" 2>/dev/null; true'
    fi
    echo "fleet_role: voice stopped"
}

voice_status() {
    if docker exec ai_stack pgrep -f "$LAUNCH_PATTERN" >/dev/null 2>&1; then
        N=$(docker exec ai_stack bash -c 'pgrep -cf "[_]node"' 2>/dev/null || echo 0)
        echo "fleet_role: voice RUNNING ($N nodes)"
    else
        echo "fleet_role: voice NOT running"; exit 1
    fi
}

perception_start() {
    docker start isaac_ros >/dev/null 2>&1 || true
    if docker inspect -f '{{.State.Running}}' isaac_ros 2>/dev/null | grep -q true; then
        echo "fleet_role: perception container up (isaac_ros)."
        echo "  NOTE: pipelines (nvblox / visual SLAM / yolov8) are launched"
        echo "  manually inside the container for now — see CLAUDE.md."
    else
        echo "fleet_role: isaac_ros container failed to start" >&2; exit 1
    fi
}

perception_stop() {
    docker stop isaac_ros >/dev/null 2>&1 || true
    echo "fleet_role: perception container stopped"
}

perception_status() {
    if docker inspect -f '{{.State.Running}}' isaac_ros 2>/dev/null | grep -q true; then
        echo "fleet_role: perception container RUNNING (isaac_ros)"
    else
        echo "fleet_role: perception container NOT running"; exit 1
    fi
}

case "$ROLE:$CMD" in
    voice:start)       voice_start ;;
    voice:stop)        voice_stop ;;
    voice:status)      voice_status ;;
    perception:start)  perception_start ;;
    perception:stop)   perception_stop ;;
    perception:status) perception_status ;;
    *) echo "usage: $0 {voice|perception} {start|stop|status}"; exit 2 ;;
esac
