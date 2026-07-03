import json
import queue
import threading
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from voice_pkg.audio_device import find_output_device, list_devices
from voice_pkg.tts_backend import load_tts_backend

_POST_SPEECH_SILENCE = 0.4   # seconds to keep mic muted after speech ends

# End-of-utterance marker from the Pi5 brain (must match
# ai_agent/graph/utils/speech_stream.py). The brain streams a reply as 1..N
# sentence chunks followed by one message whose data is exactly this marker;
# /voice/tts_speaking stays True (mic muted) from the first chunk until the
# marker is played out, so the robot never hears itself between sentences.
_EOU_MARKER = '<|eou|>'


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        self.declare_parameter('speaker_preference', 'auto')
        # PipeWire playback target — 'ec_speaker' routes TTS through the AEC
        # echo-cancel sink (host 99-echo-cancel.conf). '' = default sink.
        self.declare_parameter('audio_sink', 'ec_speaker')
        self.declare_parameter('sample_rate', 22050)
        self.declare_parameter('tts_backend', 'kokoro')
        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)
        # Watchdog: if the brain dies mid-stream and the EOU marker never
        # arrives, release the mic after this many idle seconds.
        self.declare_parameter('eou_timeout', 8.0)
        # Keyword the stop spotter listens for — used here to ignore stop
        # signals triggered by the robot itself saying the word.
        self.declare_parameter('stop_keyword', 'stop')

        speaker_pref = self.get_parameter('speaker_preference').value
        sample_rate  = self.get_parameter('sample_rate').value
        backend_name = self.get_parameter('tts_backend').value

        backend_kwargs = {
            'kokoro': dict(
                voice = self.get_parameter('voice').value,
                speed = self.get_parameter('speed').value,
                pw_target = self.get_parameter('audio_sink').value or None,
            ),
        }.get(backend_name, {})

        self._backend     = load_tts_backend(backend_name, **backend_kwargs)
        self._sample_rate = sample_rate

        self.get_logger().info(f'TTS backend: {backend_name}')

        self.get_logger().info(list_devices())
        self._output_idx, output_name = find_output_device(speaker_pref)
        self.get_logger().info(
            f'Speaker: {output_name} (idx={self._output_idx}, pref="{speaker_pref}")')

        self._speaker_pref = speaker_pref

        self._eou_timeout  = self.get_parameter('eou_timeout').value
        self._stop_keyword = self.get_parameter('stop_keyword').value.lower()

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self._timing_pub   = self.create_publisher(String, '/diag/timing', 10)
        self.create_subscription(String, '/voice/robot_speech', self._speech_cb, 10)
        self.create_subscription(String, '/voice/tts_stop', self._stop_cb, 10)

        # ── Stop / stream bookkeeping (spin thread writes, worker reads) ─────
        self._stream_open       = False   # brain's EOU for the current utterance not yet received
        self._discard_until_eou = False   # stop requested — drop in-flight chunks
        self._current_text      = ''      # chunk the worker is playing right now

        # Single worker thread + ordered chunk queue. Sized for a full streamed
        # utterance (one entry per sentence + the EOU marker) — never drops
        # mid-utterance chunks.
        self._queue: queue.Queue[str] = queue.Queue(maxsize=64)
        self._speaking = False   # worker-thread view of /voice/tts_speaking
        threading.Thread(target=self._worker, daemon=True).start()

        self.create_timer(10.0, self._check_device)

        self.get_logger().info('TTS ready')

    def _check_device(self):
        new_idx, new_name = find_output_device(self._speaker_pref)
        if new_idx != self._output_idx:
            self.get_logger().info(f'Speaker switched: {new_name} (idx={new_idx})')
            self._output_idx = new_idx

    def _speech_cb(self, msg: String):
        text = msg.data.strip()
        if not text:
            return
        if self._discard_until_eou:
            # Stop was requested mid-stream: swallow the rest of this utterance.
            if text == _EOU_MARKER:
                self._discard_until_eou = False
                self._stream_open = False
                self._emit_timing('tts_eou_receive', discarded=True)
            return
        if text == _EOU_MARKER:
            self._stream_open = False
            self._emit_timing('tts_eou_receive')
        else:
            self._stream_open = True
            self._emit_timing('tts_receive', chars=len(text))
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            # Should never happen at 64 slots — dropping a mid-utterance chunk
            # (or the marker) is worse than logging loudly.
            self.get_logger().error(f'TTS queue full — dropped: "{text[:60]}"')

    def _stop_cb(self, msg: String):
        """Stop keyword heard while speaking — halt playback and flush."""
        if not self._speaking:
            return
        if self._stop_keyword in self._current_text.lower():
            # The robot itself is saying the keyword right now — likely self-echo.
            self.get_logger().info(f'Stop ignored (self-echo guard): "{msg.data}"')
            return
        self.get_logger().info(f'Stop keyword heard ("{msg.data}") — halting speech')
        self._emit_timing('tts_stopped')
        # If the brain is still streaming this utterance, swallow what's coming.
        self._discard_until_eou = self._stream_open
        while True:                       # flush queued chunks + any marker
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put_nowait(_EOU_MARKER)   # worker closes the utterance
        self._backend.stop()                  # unblocks mid-playback speak()

    def _worker(self):
        while True:
            try:
                # While an utterance is open, an idle gap means the next chunk
                # (or the EOU marker) is still being generated — but if the
                # brain died mid-stream, release the mic after eou_timeout.
                item = self._queue.get(timeout=self._eou_timeout if self._speaking else None)
            except queue.Empty:
                self.get_logger().warning(
                    f'No chunk or EOU marker for {self._eou_timeout}s — releasing mic')
                # Brain likely died mid-stream — don't let stale flags eat the
                # next utterance.
                self._discard_until_eou = False
                self._stream_open = False
                self._finish_utterance()
                continue

            if item == _EOU_MARKER:
                self._finish_utterance()
                continue

            if not self._speaking:
                self._speaking = True
                self._set_speaking(True)
            self.get_logger().info(f'Speaking: "{item}"')
            self._emit_timing('tts_synth_start', chars=len(item))
            self._current_text = item
            try:
                self._backend.speak(
                    item, self._output_idx, self._sample_rate,
                    on_audio_start=lambda: self._emit_timing('tts_audio_start'),
                )
            except Exception as e:
                self.get_logger().error(f'TTS error: {e}')
            finally:
                self._current_text = ''
                self._emit_timing('tts_end')

    def _finish_utterance(self):
        """Close the current utterance: brief guard silence, then unmute the mic."""
        if not self._speaking:
            return
        self._emit_timing('tts_utterance_end')
        time.sleep(_POST_SPEECH_SILENCE)
        self._speaking = False
        self._set_speaking(False)
        self.get_logger().info('Utterance complete')

    def _set_speaking(self, state: bool):
        msg = Bool()
        msg.data = state
        self._speaking_pub.publish(msg)

    def _emit_timing(self, stage: str, **fields):
        event = {'stage': stage, 't': time.time(), **fields}
        self._timing_pub.publish(String(data=json.dumps(event)))


def main(args=None):
    rclpy.init(args=args)
    node = TTSNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
