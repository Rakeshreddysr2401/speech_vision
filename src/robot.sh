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
set -euo pipefail

MODE=${1:-visual_assistant}
PROVIDER=${2:-gemini}
WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Robot starting — mode: $MODE  provider: $PROVIDER"

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
source "$WS/install/setup.bash"

exec ros2 launch robot_bringup_pkg robot.launch.py \
    mode:="$MODE" \
    provider:="$PROVIDER"
