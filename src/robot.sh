#!/usr/bin/env bash
# robot.sh — one-command launcher
#
# Usage:
#   ./robot.sh                          # visual_assistant + gemini
#   ./robot.sh visual_assistant gemini
#   ./robot.sh voice_only ollama
#   ./robot.sh full openai
#   ./robot.sh perception_only
#
# Modes:
#   visual_assistant   STT + TTS + camera + brain(VLM)   [default]
#   voice_only         STT + TTS + brain, no camera
#   full               every node
#   perception_only    vision pipeline only
#
# Providers (brain backend):
#   gemini   ollama   openai   llamacpp
set -e
set +u

MODE=${1:-visual_assistant}
PROVIDER=${2:-gemini}

# Get the directory where the script is located (src/)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The workspace root is one level up from src/
WS_ROOT="$(dirname "$SRC_DIR")"

echo "Robot starting — mode: $MODE  provider: $PROVIDER"
echo "Workspace root: $WS_ROOT"

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash

if [ -f "$WS_ROOT/install/setup.bash" ]; then
    source "$WS_ROOT/install/setup.bash"
else
    echo "Error: Could not find $WS_ROOT/install/setup.bash"
    echo "Please run 'colcon build' in $WS_ROOT first."
    exit 1
fi

exec ros2 launch robot_bringup_pkg robot.launch.py \
    mode:="$MODE" \
    provider:="$PROVIDER"
