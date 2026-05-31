# =============================================================================
# example.bashrc — speech_vision (Jetson Orin)
#
# Copy the sections you need into your actual ~/.bashrc, then run:
#   source ~/.bashrc
#
# Or source this file directly for a one-time session:
#   source /path/to/speech_vision/example.bashrc
# =============================================================================


# ── ROS2 base setup ───────────────────────────────────────────────────────────
source /opt/ros/humble/setup.bash


# ── Workspace (update path if you cloned somewhere else) ─────────────────────
export ROBOT_WS=~/speech_vision
source "$ROBOT_WS/install/setup.bash"


# ── ROS2 domain — must match Pi5 ─────────────────────────────────────────────
export ROS_DOMAIN_ID=0

# Optional: use shared memory transport for lower latency on the same machine
# export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp


# ── CUDA / GPU (Jetson JetPack) ───────────────────────────────────────────────
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH


# ── Python path (needed if packages are not installed system-wide) ────────────
export PYTHONPATH="$ROBOT_WS/install/robot_voice_pkg/lib/python3.10/site-packages:$PYTHONPATH"
export PYTHONPATH="$ROBOT_WS/install/robot_vision_pkg/lib/python3.10/site-packages:$PYTHONPATH"


# =============================================================================
# BUILD ALIASES
# =============================================================================

# Build entire workspace
alias rb='cd $ROBOT_WS && colcon build --symlink-install && source install/setup.bash'

# Build a single package (usage: rbp robot_voice_pkg)
alias rbp='_rbp() { cd $ROBOT_WS && colcon build --symlink-install --packages-select "$1" && source install/setup.bash; }; _rbp'

# Build without tests (faster)
alias rbn='cd $ROBOT_WS && colcon build --symlink-install --cmake-args -DBUILD_TESTING=OFF && source install/setup.bash'

# Clean build (removes build/ install/ log/)
alias rbclean='cd $ROBOT_WS && rm -rf build install log && colcon build --symlink-install && source install/setup.bash'

# Re-source workspace (after a build done in another terminal)
alias rsrc='source $ROBOT_WS/install/setup.bash && echo "Workspace sourced"'


# =============================================================================
# LAUNCH ALIASES  —  full system (top-level bringup)
# =============================================================================

# Primary mode — STT + TTS + camera + depth + spatial + Moondream  [DEFAULT]
alias robot='ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant'

# Voice only — STT + TTS, no camera (lightest on VRAM)
alias robot-voice='ros2 launch robot_bringup_pkg robot.launch.py mode:=voice_only'

# Full pipeline — everything on
alias robot-full='ros2 launch robot_bringup_pkg robot.launch.py mode:=full'

# Vision pipeline only — no voice or brain
alias robot-vision='ros2 launch robot_bringup_pkg robot.launch.py mode:=perception_only'

# Change LLM provider at launch (default is gemini)
# Usage: robot-openai  /  robot-ollama  /  robot-llamacpp
alias robot-openai='ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant provider:=openai'
alias robot-ollama='ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant provider:=ollama'
alias robot-llamacpp='ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant provider:=llamacpp'


# =============================================================================
# LAUNCH ALIASES  —  individual packages
# =============================================================================

# Voice package only
alias launch-voice='ros2 launch robot_voice_pkg voice.launch.py'
alias launch-voice-piper='ros2 launch robot_voice_pkg voice.launch.py tts_engine:=piper'
alias launch-voice-kokoro='ros2 launch robot_voice_pkg voice.launch.py tts_engine:=kokoro'

# Vision package only (all nodes)
alias launch-vision='ros2 launch robot_vision_pkg vision.launch.py enable_detector:=true enable_depth:=true enable_spatial:=true enable_moondream:=true'

# Vision minimal (camera + detector only, no depth/VLM)
alias launch-vision-minimal='ros2 launch robot_vision_pkg vision.launch.py enable_detector:=true'

# Individual vision nodes
alias launch-camera='ros2 run robot_vision_pkg camera_node'
alias launch-detector='ros2 run robot_vision_pkg detector_node'
alias launch-depth='ros2 run robot_vision_pkg depth_node'
alias launch-spatial='ros2 run robot_vision_pkg spatial_node'
alias launch-moondream='ros2 run robot_vision_pkg moondream_node'
alias launch-scene='ros2 run robot_vision_pkg scene_node'
alias launch-tracker='ros2 run robot_vision_pkg tracker_node'

# Legacy brain (on Jetson — not used when Pi5 is the brain)
alias launch-brain='ros2 launch robot_brain_pkg brain.launch.py'
alias launch-brain-vision='ros2 launch robot_brain_pkg brain.launch.py use_vision:=true'


# =============================================================================
# DEBUG / MONITORING ALIASES
# =============================================================================

# List all active topics
alias rtopics='ros2 topic list'

# Echo key topics  (Ctrl+C to stop)
alias recho-speech-in='ros2 topic echo /voice/user_input'         # what user said
alias recho-speech-out='ros2 topic echo /voice/robot_speech'      # what robot says
alias recho-objects='ros2 topic echo /vision/objects_3d'          # 3D detections JSON
alias recho-query='ros2 topic echo /vision/query'                  # VLM question from Pi5
alias recho-query-result='ros2 topic echo /vision/query_result'   # Moondream answer
alias recho-thinking='ros2 topic echo /brain/thinking'            # Pi5 processing flag

# Topic bandwidth / hz
alias rhz-camera='ros2 topic hz /vision/image_raw'
alias rhz-objects='ros2 topic hz /vision/objects_3d'

# List all active nodes
alias rnodes='ros2 node list'

# Node info
alias rnode-stt='ros2 node info /stt_node'
alias rnode-tts='ros2 node info /tts_node'
alias rnode-moondream='ros2 node info /moondream_node'

# Check topic connections (who publishes / subscribes)
alias rcheck='ros2 topic info -v'    # usage: rcheck /voice/user_input

# GPU / VRAM usage (Jetson)
alias gpu='tegrastats --interval 2000'
alias vram='tegrastats --interval 1000 | grep -o "RAM [0-9]*/[0-9]*MB"'


# =============================================================================
# TEST / INJECT ALIASES  —  send test messages without real hardware
# =============================================================================

# Simulate user speech (bypasses microphone)
alias rtest-speak='ros2 topic pub --once /voice/user_input std_msgs/msg/String "{data: \"hello robot\"}"'

# Send a custom message (usage: rspeak "go to the door")
rspeak() {
    ros2 topic pub --once /voice/user_input std_msgs/msg/String "{data: \"$*\"}"
}

# Ask Moondream a question directly
rask-vision() {
    ros2 topic pub --once /vision/query std_msgs/msg/String "{data: \"$*\"}"
}

# Simulate robot speech output (test TTS)
rsay() {
    ros2 topic pub --once /voice/robot_speech std_msgs/msg/String "{data: \"$*\"}"
}


# =============================================================================
# DOCKER HELPERS  (if running inside a container)
# =============================================================================

# Allow GUI / RViz from inside Docker
alias xdock='xhost +local:docker'

# Check if ROS2 daemon is running
alias rcheck-daemon='ros2 daemon status'
alias rstart-daemon='ros2 daemon start'
alias rstop-daemon='ros2 daemon stop && ros2 daemon start'


# =============================================================================
# CONVENIENCE
# =============================================================================

# Go to workspace
alias ws='cd $ROBOT_WS'

# Tail latest colcon log
alias rlog='tail -f $ROBOT_WS/log/latest_build/events.log'

# Kill all ROS2 nodes
alias rkillall='pkill -f ros2 && pkill -f robot_ && echo "All ROS2 processes killed"'

# Show ROS2 environment variables
alias renv='env | grep -E "^ROS|^AMENT|^COLCON|^RMW"'

# Print a reminder of the most useful aliases
alias rhelp='echo "
BUILD:
  rb            — build whole workspace
  rbp <pkg>     — build one package
  rbclean       — clean + full rebuild
  rsrc          — re-source workspace

LAUNCH (full system):
  robot               — visual_assistant mode  [DEFAULT]
  robot-voice         — voice only (no camera)
  robot-full          — all nodes
  robot-vision        — vision pipeline only
  robot-openai/ollama/llamacpp  — change LLM provider

LAUNCH (individual):
  launch-voice        — STT + TTS
  launch-vision       — all vision nodes
  launch-camera/detector/depth/spatial/moondream  — single node

DEBUG:
  rtopics             — list topics
  rnodes              — list nodes
  recho-speech-in/out/objects/query/thinking
  gpu / vram          — Jetson resource usage

TEST:
  rspeak <text>       — simulate user speech
  rask-vision <q>     — send vision query
  rsay <text>         — test TTS output
"
'
