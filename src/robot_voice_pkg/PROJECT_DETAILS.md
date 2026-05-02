# Robot Voice Package (Speech-to-Text & Text-to-Speech)

This project provides a robust, low-latency voice interface for robots, running on **NVIDIA Jetson Orin Nano**. It leverages modern neural networks for high-quality speech synthesis and accurate transcription.

## 🚀 Overview
The system consists of two primary ROS 2 nodes:
1.  **`stt_node`**: Transcribes microphone input into text using **Faster-Whisper**.
2.  **`tts_node`**: Converts text into high-quality speech using the **Kokoro** or **Piper** engines.

---

## 🏗 System Architecture

### **Container Environment**
The project runs inside a Docker container (`voice_tts_dev`) to ensure all dependencies (CUDA, ROS 2 Humble, Python Venv) are perfectly managed.
- **Base Image**: `speech_vision:voice_tts`
- **Key Dependencies**: 
    - `faster-whisper` (GPU accelerated)
    - `kokoro-onnx` / `kokoro`
    - `webrtcvad` (Voice Activity Detection)
    - `pyaudio` & `sounddevice`

### **Workflow Logic**
1.  **Capture**: `stt_node` listens to the microphone via PulseAudio.
2.  **VAD**: Audio is processed locally by WebRTC VAD to detect when a human starts and stops talking.
3.  **STT**: Once an utterance is captured, it is processed by the **Whisper Base** model on the GPU.
4.  **Publish**: The resulting text is published to `/voice/user_input`.
5.  **Subscribe**: The `tts_node` listens to `/voice/robot_speech`.
6.  **Synthesize**: When text is received, the **Kokoro** engine generates neural audio on the GPU.
7.  **Play**: The audio is played back through the headset.

---

## 🛠 Command Shortcuts (Inside Container)

We have optimized the `.bashrc` with powerful functions to make development easy:

| Command | Description | Example |
| :--- | :--- | :--- |
| `ws` | Jump to the workspace directory. | `ws` |
| `build` | Build the project and source the environment. | `build` or `build --packages-select pkg` |
| `voice` | Launch both STT and TTS nodes together. | `voice` or `voice tts_engine:=piper` |
| `killvoice` | Emergency stop for all voice nodes. | `killvoice` |
| `listen` | Monitor live transcriptions from the mic. | `listen` |
| `speaking` | Check if the robot is currently talking. | `speaking` |

---

## ⚙️ Configuration

Settings are managed in `src/robot_voice_pkg/config/voice_params.yaml`.

### **Microphone & Speaker**
- **`input_device_index: -1`**: Uses the system default (PulseAudio), allowing multiple apps to use the mic.
- **`output_device_index: 0`**: Targets the **Plantronics Blackwire** headset for speech output.

### **STT Settings**
- **`model_size: "base.en"`**: Balanced for speed and accuracy on Jetson.
- **`compute_type: "float16"`**: Maximizes GPU efficiency.

---

## 🧪 Testing Locally

### **1. Start the Voice System**
```bash
# On host machine
speech

# Inside container
voice
```

### **2. Test Text-to-Speech (Make the Robot talk)**
```bash
ros2 topic pub /voice/robot_speech std_msgs/msg/String "{data: 'Hello, I am ready to assist.'}" -1
```

### **3. Test Speech-to-Text**
Simply speak into your headset. Open a second terminal and run:
```bash
listen
```
You will see your transcribed text appear in real-time.

---

## 📂 Repository Structure
```text
src/robot_voice_pkg/
├── config/             # YAML parameters (Device IDs, Models)
├── launch/             # ROS 2 Launch files
├── robot_voice_pkg/    # Main Python source
│   ├── backends/       # TTS Engines (Kokoro, Piper)
│   ├── stt_node.py     # Speech-to-Text Logic
│   ├── tts_node.py     # Text-to-Speech Logic
│   └── vad_utils.py    # Voice Activity Detection logic
└── package.xml         # Package metadata
```
