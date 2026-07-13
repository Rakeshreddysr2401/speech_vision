#!/usr/bin/env bash
# LangRobo Jetson role launcher — called by the Pi5's scripts/fleet.sh over ssh.
#
#   fleet_role.sh voice      {start|stop|status}          # ai_stack STT/TTS/music
#   fleet_role.sh perception {start|stop|status} [sim|real]  # isaac_ros stack
#
# Compute decision (DEPTH_CAMERA.md, 2026-07-12): perception has priority on
# the 8GB Orin. Voice stays OFF while perception runs — talk to the robot via
# Telegram. perception mode defaults: sim (until the D555 is verified, then
# flip the DEFAULT_PERCEPTION_MODE below or pass 'real').
set -eo pipefail

ROLE="${1:?usage: fleet_role.sh voice-or-perception start-stop-status [sim-or-real]}"
ACTION="${2:?usage: fleet_role.sh voice-or-perception start-stop-status [sim-or-real]}"
DEFAULT_PERCEPTION_MODE=sim
MODE="${3:-$DEFAULT_PERCEPTION_MODE}"

VOICE_LAUNCH='ros2 launch bringup_pkg robot.launch.py'
PERC_SIM=/workspaces/isaac_ros-dev/src/langrobo_perception/scripts/run_perception_sim.sh
PERC_REAL=/workspaces/isaac_ros-dev/src/langrobo_perception/scripts/run_perception_real.sh

# Zombie sweep (gotcha 2 in ~/robot/CLAUDE.md): kill -INT on the detached
# launch does not reliably kill its nodes — sweep leftovers by pattern.
sweep() {  # sweep <container> <pattern...>
    local c="$1"; shift
    for pat in "$@"; do
        docker exec "$c" bash -c "pkill -INT -f '$pat' 2>/dev/null" || true
    done
    sleep 5
    for pat in "$@"; do
        docker exec "$c" bash -c "pkill -9 -f '$pat' 2>/dev/null" || true
    done
}

case "$ROLE" in
voice)
    case "$ACTION" in
    start)
        docker start ai_stack >/dev/null
        if docker exec ai_stack pgrep -f "$VOICE_LAUNCH" >/dev/null 2>&1; then
            echo "jetson voice: already running"
        else
            docker exec -d ai_stack bash -c \
                "source /opt/ros/jazzy/setup.bash && source /workspaces/ai_ws/install/setup.bash && $VOICE_LAUNCH >> /data/robot_launch.log 2>&1"
            echo "jetson voice: started (log ~/robot/data/robot_launch.log)"
        fi
        ;;
    stop)
        if docker ps -q -f name='^ai_stack$' | grep -q .; then
            sweep ai_stack 'ros2 launch bringup_pkg' 'stt_node|tts_node|music_node|camera_node|target_node'
            docker stop ai_stack >/dev/null
        fi
        echo "jetson voice: stopped"
        ;;
    status)
        if docker ps -q -f name='^ai_stack$' | grep -q . \
           && docker exec ai_stack pgrep -f "$VOICE_LAUNCH" >/dev/null 2>&1; then
            echo "jetson voice: RUNNING"
        else
            echo "jetson voice: stopped"
        fi
        ;;
    esac
    ;;
perception)
    case "$ACTION" in
    start)
        docker start isaac_ros >/dev/null
        if docker exec isaac_ros pgrep -f 'perception.launch.py' >/dev/null 2>&1; then
            echo "jetson perception: already running"
        else
            if [ "$MODE" = real ]; then
                docker exec -d isaac_ros bash -c "$PERC_REAL >> /data/perception_launch.log 2>&1"
            else
                docker exec -d isaac_ros bash -c "$PERC_SIM >> /data/perception_launch.log 2>&1"
            fi
            echo "jetson perception: started mode=$MODE (log ~/robot/data/perception_launch.log)"
        fi
        ;;
    stop)
        if docker ps -q -f name='^isaac_ros$' | grep -q .; then
            sweep isaac_ros 'perception.launch.py' \
                'nvblox_node|visual_slam|component_container|realsense2_camera|detections_3d|nav2|controller_server|planner_server|bt_navigator|behavior_server|smoother_server|velocity_smoother|collision_monitor|lifecycle_manager|waypoint_follower|docking_server|cmd_vel_stamper|sim_camera_relay|static_transform_publisher'
            docker stop isaac_ros >/dev/null
        fi
        echo "jetson perception: stopped"
        ;;
    status)
        if docker ps -q -f name='^isaac_ros$' | grep -q . \
           && docker exec isaac_ros pgrep -f 'perception.launch.py' >/dev/null 2>&1; then
            echo "jetson perception: RUNNING"
        else
            echo "jetson perception: stopped"
        fi
        ;;
    esac
    ;;
*)
    echo "unknown role: $ROLE" >&2; exit 2
    ;;
esac
