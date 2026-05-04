import queue
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String


class BrainNode(Node):
    def __init__(self):
        super().__init__("brain_node")

        # ── Core params ──────────────────────────────────────────────
        self.declare_parameter("provider", "ollama")
        self.declare_parameter("use_vision", False)
        self.declare_parameter("history_turns", 6)
        self.declare_parameter("system_prompt",
                               "You are a helpful robot assistant. Keep answers short and clear.")

        # ── OpenAI / llama.cpp params (same backend, different base_url) ──
        self.declare_parameter("openai.api_key_env", "OPENAI_API_KEY")
        self.declare_parameter("openai.model", "gpt-4o-mini")
        self.declare_parameter("openai.base_url", "")      # empty → OpenAI default
        self.declare_parameter("openai.temperature", 0.7)
        self.declare_parameter("openai.max_tokens", 150)

        # ── llama.cpp params (alias of openai backend, no API key needed) ──
        self.declare_parameter("llamacpp.model", "gemma4")
        self.declare_parameter("llamacpp.base_url", "http://localhost:8080/v1")
        self.declare_parameter("llamacpp.temperature", 0.7)
        self.declare_parameter("llamacpp.max_tokens", 150)

        # ── Gemini params ────────────────────────────────────────────
        self.declare_parameter("gemini.api_key_env", "GEMINI_API_KEY")
        self.declare_parameter("gemini.model", "gemini-2.0-flash")
        self.declare_parameter("gemini.temperature", 0.7)
        self.declare_parameter("gemini.max_tokens", 150)

        # ── Ollama params ────────────────────────────────────────────
        self.declare_parameter("ollama.base_url", "http://localhost:11434")
        self.declare_parameter("ollama.model", "llama3.2")
        self.declare_parameter("ollama.temperature", 0.7)
        self.declare_parameter("ollama.max_tokens", 150)

        # ── Init ─────────────────────────────────────────────────────
        provider = self.get_parameter("provider").value
        self._use_vision = self.get_parameter("use_vision").value
        self._max_history = self.get_parameter("history_turns").value * 2  # user+assistant pairs
        self._system_prompt = self.get_parameter("system_prompt").value

        self._backend = self._load_backend(provider)
        self._history: list[dict] = []
        self._latest_frame: bytes | None = None
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=3)
        threading.Thread(target=self._worker_loop, daemon=True).start()

        # ── Publishers ───────────────────────────────────────────────
        self._pub_speech = self.create_publisher(String, "/voice/robot_speech", 10)
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)

        # ── Subscribers ──────────────────────────────────────────────
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)

        if self._use_vision:
            from cv_bridge import CvBridge
            from sensor_msgs.msg import Image
            self._bridge = CvBridge()
            self.create_subscription(Image, "/vision/image_raw", self._on_image, 1)
            self.get_logger().info("Vision mode enabled — subscribing to /vision/image_raw")

        self.get_logger().info(f"Brain node ready — provider: {provider}, vision: {self._use_vision}")

    # ── Backend loader ────────────────────────────────────────────────
    def _load_backend(self, provider: str):
        sp = self._system_prompt

        if provider == "openai":
            from .backends.llm_openai import OpenAIBackend
            return OpenAIBackend({
                "api_key_env": self.get_parameter("openai.api_key_env").value,
                "model":       self.get_parameter("openai.model").value,
                "base_url":    self.get_parameter("openai.base_url").value,
                "temperature": self.get_parameter("openai.temperature").value,
                "max_tokens":  self.get_parameter("openai.max_tokens").value,
                "system_prompt": sp,
            })

        elif provider == "llamacpp":
            # llama.cpp exposes an OpenAI-compatible /v1/chat/completions endpoint.
            # VLM works when server is started with --mmproj <mmproj.gguf>.
            from .backends.llm_openai import OpenAIBackend
            return OpenAIBackend({
                "api_key_env": None,   # not needed for local server
                "model":       self.get_parameter("llamacpp.model").value,
                "base_url":    self.get_parameter("llamacpp.base_url").value,
                "temperature": self.get_parameter("llamacpp.temperature").value,
                "max_tokens":  self.get_parameter("llamacpp.max_tokens").value,
                "system_prompt": sp,
            })

        elif provider == "gemini":
            from .backends.llm_gemini import GeminiBackend
            return GeminiBackend({
                "api_key_env": self.get_parameter("gemini.api_key_env").value,
                "model":       self.get_parameter("gemini.model").value,
                "temperature": self.get_parameter("gemini.temperature").value,
                "max_tokens":  self.get_parameter("gemini.max_tokens").value,
                "system_prompt": sp,
            })

        elif provider == "ollama":
            from .backends.llm_ollama import OllamaBackend
            return OllamaBackend({
                "base_url":    self.get_parameter("ollama.base_url").value,
                "model":       self.get_parameter("ollama.model").value,
                "temperature": self.get_parameter("ollama.temperature").value,
                "max_tokens":  self.get_parameter("ollama.max_tokens").value,
                "system_prompt": sp,
            })

        else:
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                "Choose from: openai | llamacpp | gemini | ollama"
            )

    # ── Callbacks ─────────────────────────────────────────────────────
    def _on_image(self, msg) -> None:
        import cv2
        try:
            cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            _, buf = cv2.imencode(".jpg", cv_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            with self._lock:
                self._latest_frame = buf.tobytes()
        except Exception as e:
            self.get_logger().warning(f"Frame encode error: {e}")

    def _on_user_input(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(text)
            self.get_logger().warning("Input queue full — dropped oldest item")

    def _worker_loop(self) -> None:
        while True:
            text = self._queue.get()
            self._process(text)
            self._queue.task_done()

    # ── Inference ─────────────────────────────────────────────────────
    def _process(self, text: str) -> None:
        self._pub_thinking.publish(Bool(data=True))
        try:
            with self._lock:
                history = list(self._history)
                frame = self._latest_frame

            if self._use_vision and frame is not None:
                response = self._backend.chat_with_image(history, text, frame)
            else:
                response = self._backend.chat(history, text)

            response = response.strip()
            if not response:
                return

            with self._lock:
                self._history.append({"role": "user", "content": text})
                self._history.append({"role": "assistant", "content": response})
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

            self.get_logger().info(f"Brain → TTS: {response[:100]}")
            self._pub_speech.publish(String(data=response))

        except Exception as e:
            self.get_logger().error(f"Brain inference error: {e}")
        finally:
            self._pub_thinking.publish(Bool(data=False))


def main(args=None):
    rclpy.init(args=args)
    node = BrainNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
