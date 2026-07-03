"""Music node — plays music on the robot speaker with instant local stop.

Contract with the Pi5 brain (langrobo_core.tools.music — already deployed):

  subscribe /audio/music_cmd  (String, JSON)
      {"action": "play",   "query": "shape of you", "t": <epoch>}
      {"action": "pause" | "resume" | "stop", "t": ...}
      {"action": "volume", "level": 0-100, "t": ...}
  publish  /audio/music_state (String, JSON) — on every change + 1Hz while playing
      {"playing": bool, "paused": bool, "title": str, "volume": int,
       "error": str|null, "stamp": <epoch>}   # stamp = wall clock (Pi5 waits on it)

Engine: yt-dlp resolves the query to a direct audio URL; ffmpeg decodes it to
raw PCM; this node applies volume/ducking in numpy and pipes the samples to
pw-cat targeting **ec_speaker** — music is part of the AEC reference, so the
mic keeps hearing the user (wake word + "stop") over the music.

Instant stop paths (fastest first):
  1. stt_node's stop spotter publishes {"action": "stop"} locally  (<400ms)
  2. /voice/tts_stop (any source) also stops playback here
  3. Pi5 stop_music tool / brain sweep

Ducking: while /voice/tts_speaking is True, volume drops to duck_percent so
the robot talks over quiet music (Alexa behaviour).
"""

import json
import shutil
import subprocess
import threading
import time

import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

_PW_CAT = '/usr/local/bin/pw-cat'
_RATE = 48000
_CHUNK = 4800            # 100ms of samples
_DUCK_RAMP_S = 0.15


class MusicNode(Node):
    def __init__(self):
        super().__init__('music_node')

        self.declare_parameter('audio_sink', 'ec_speaker')  # AEC sink ('' = default)
        self.declare_parameter('default_volume', 70)        # 0-100
        self.declare_parameter('duck_percent', 25)          # of current vol while TTS speaks

        self._sink   = self.get_parameter('audio_sink').value or None
        self._volume = int(self.get_parameter('default_volume').value)
        self._duck_pct = int(self.get_parameter('duck_percent').value)

        self._lock = threading.Lock()
        self._ffmpeg: subprocess.Popen | None = None
        self._pwcat:  subprocess.Popen | None = None
        self._player_gen = 0          # bumped on stop/new-play; stale threads exit
        self._paused = threading.Event()   # set = paused (player thread idles)
        self._ducked = False
        self._state = {'playing': False, 'paused': False, 'title': '',
                       'volume': self._volume, 'error': None}

        self._state_pub = self.create_publisher(String, '/audio/music_state', 10)
        self.create_subscription(String, '/audio/music_cmd', self._cmd_cb, 10)
        self.create_subscription(Bool, '/voice/tts_speaking', self._duck_cb, 10)
        self.create_subscription(String, '/voice/tts_stop', self._tts_stop_cb, 10)
        self.create_timer(1.0, self._tick)

        pwcat_ok = shutil.which(_PW_CAT) or shutil.which('pw-cat')
        self.get_logger().info(
            f'Music ready — sink={self._sink or "default"} vol={self._volume} '
            f'(pw-cat: {"ok" if pwcat_ok else "MISSING"})')

    # ── Command handling (spin thread) ─────────────────────────────────────

    def _cmd_cb(self, msg: String):
        try:
            cmd = json.loads(msg.data)
            action = cmd.get('action', '')
        except (ValueError, TypeError):
            self.get_logger().warning(f'Bad music_cmd JSON: {msg.data[:80]}')
            return
        self.get_logger().info(f'music_cmd: {cmd}')
        if action == 'play':
            threading.Thread(target=self._play, args=(cmd.get('query', ''),),
                             daemon=True).start()
        elif action == 'stop':
            self._stop()
        elif action == 'pause':
            self._paused.set()
            self._publish_state(paused=True)
        elif action == 'resume':
            self._paused.clear()
            self._publish_state(paused=False)
        elif action == 'volume':
            with self._lock:
                self._volume = max(0, min(100, int(cmd.get('level', self._volume))))
            self._publish_state(volume=self._volume)

    def _tts_stop_cb(self, msg: String):
        """Spoken 'stop' (or wake barge-in) — halt music unless it was a
        wake-word barge-in, which pauses via its own music_cmd instead."""
        if msg.data.startswith('[wake:'):
            return
        if self._state['playing']:
            self.get_logger().info('tts_stop received — stopping music')
            self._stop()

    def _duck_cb(self, msg: Bool):
        self._ducked = msg.data

    # ── Playback ────────────────────────────────────────────────────────────

    def _resolve(self, query: str) -> tuple[str, str]:
        """yt-dlp: query → (stream_url, title). Raises on failure."""
        import yt_dlp
        opts = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True,
                'no_warnings': True, 'default_search': 'ytsearch1'}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        if 'entries' in info:
            entries = [e for e in info['entries'] if e]
            if not entries:
                raise RuntimeError(f'no results for "{query}"')
            info = entries[0]
        return info['url'], info.get('title', query)

    def _play(self, query: str):
        if not query.strip():
            self._publish_state(error='empty query')
            return
        self._stop(publish=False)
        with self._lock:
            self._player_gen += 1
            gen = self._player_gen
        try:
            url, title = self._resolve(query)
        except Exception as e:
            self.get_logger().error(f'resolve failed: {e}')
            self._publish_state(playing=False, title='', error=str(e)[:200])
            return
        with self._lock:
            if gen != self._player_gen:
                return          # a newer play/stop superseded us mid-resolve
            ffmpeg = subprocess.Popen(
                ['ffmpeg', '-nostdin', '-loglevel', 'error',
                 '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                 '-i', url, '-f', 's16le', '-ar', str(_RATE), '-ac', '1', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            pw_cmd = [_PW_CAT, '--playback', '--format=s16',
                      f'--rate={_RATE}', '--channels=1']
            if self._sink:
                pw_cmd += ['--target', self._sink]
            pwcat = subprocess.Popen(pw_cmd + ['-'], stdin=subprocess.PIPE)
            self._ffmpeg, self._pwcat = ffmpeg, pwcat
        self._paused.clear()
        self._publish_state(playing=True, paused=False, title=title, error=None)
        self.get_logger().info(f'Playing: {title}')
        threading.Thread(target=self._pump, args=(gen, ffmpeg, pwcat),
                         daemon=True).start()

    def _pump(self, gen: int, ffmpeg: subprocess.Popen, pwcat: subprocess.Popen):
        """Decode → volume/duck in numpy → AEC sink. Runs until EOF or stop."""
        gain = self._effective_gain()
        try:
            while True:
                with self._lock:
                    if gen != self._player_gen:
                        return
                if self._paused.is_set():
                    time.sleep(0.1)
                    continue
                data = ffmpeg.stdout.read(_CHUNK * 2)
                if not data:
                    break
                target = self._effective_gain()
                # short linear ramp toward target gain — no zipper noise on duck
                if abs(target - gain) > 0.01:
                    steps = max(1, int(_DUCK_RAMP_S * _RATE / _CHUNK))
                    gain += (target - gain) / steps
                else:
                    gain = target
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                out = np.clip(samples * gain, -32768, 32767).astype(np.int16)
                pwcat.stdin.write(out.tobytes())
        except (BrokenPipeError, OSError):
            pass
        finally:
            with self._lock:
                current = gen == self._player_gen
            if current:
                self._cleanup_procs()
                self._publish_state(playing=False, paused=False, title='')
                self.get_logger().info('Playback finished')

    def _effective_gain(self) -> float:
        vol = self._volume / 100.0
        return vol * (self._duck_pct / 100.0) if self._ducked else vol

    def _stop(self, publish: bool = True):
        with self._lock:
            self._player_gen += 1
        self._paused.clear()
        self._cleanup_procs()
        if publish:
            self._publish_state(playing=False, paused=False, title='')

    def _cleanup_procs(self):
        for proc in (self._ffmpeg, self._pwcat):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._ffmpeg = self._pwcat = None

    # ── State publishing ────────────────────────────────────────────────────

    def _publish_state(self, **changes):
        self._state.update(changes)
        self._state['volume'] = self._volume
        out = {**self._state, 'stamp': time.time()}
        self._state_pub.publish(String(data=json.dumps(out)))

    def _tick(self):
        if self._state['playing']:
            self._publish_state()   # 1Hz heartbeat while playing


def main(args=None):
    rclpy.init(args=args)
    node = MusicNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
