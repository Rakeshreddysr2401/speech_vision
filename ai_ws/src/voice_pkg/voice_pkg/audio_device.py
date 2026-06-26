import sounddevice as sd

# Priority 3 — Bluetooth (wireless, usually best isolation)
_BT_KEYWORDS = {'bluetooth', 'bluez', 'bluealsa', 'a2dp', 'hsp', 'hfp', 'bt ', ' bt'}
# Priority 2 — named headset/mic brands (dedicated audio hardware, not webcam mics)
_HEADSET_BRANDS = {'plantronics', 'jabra', 'sennheiser', 'logitech', 'bose',
                   'sony', 'hyperx', 'steelseries', 'blackwire', 'poly'}
# Priority 1 — generic USB/mic keyword (catches webcams, generic USB audio, etc.)
_GENERIC_MIC = {'headset', 'microphone', 'mic', 'usb audio'}


def _score(name: str) -> int:
    lower = name.lower()
    if any(k in lower for k in _BT_KEYWORDS):
        return 3
    if any(k in lower for k in _HEADSET_BRANDS):
        return 2
    if any(k in lower for k in _GENERIC_MIC):
        return 1
    return 0


def _devices_with_inputs():
    return [(i, d) for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] >= 1]


def _devices_with_outputs():
    return [(i, d) for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] >= 1]


def _auto_select_input() -> tuple[int | None, str]:
    best_idx, best_score, best_name = None, 0, 'system default'
    for i, d in _devices_with_inputs():
        s = _score(d['name'])
        if s > best_score:
            best_score, best_idx, best_name = s, i, d['name']
    return best_idx, best_name


def _auto_select_output() -> tuple[int | None, str]:
    best_idx, best_score, best_name = None, 0, 'system default'
    for i, d in _devices_with_outputs():
        s = _score(d['name'])
        if s > best_score:
            best_score, best_idx, best_name = s, i, d['name']
    return best_idx, best_name


def _select_by_substr(substr: str, inputs: bool) -> tuple[int | None, str]:
    pool = _devices_with_inputs() if inputs else _devices_with_outputs()
    lower = substr.lower()
    for i, d in pool:
        if lower in d['name'].lower():
            return i, d['name']
    return None, f'system default (no match for "{substr}")'


def find_input_device(preference: str = 'auto') -> tuple[int | None, str]:
    """Return (device_index, name) for the best available input device.

    preference:
      'auto'      — BT > USB headset > system default
      'bluetooth' — force BT; falls back to auto if none found
      'usb'       — force USB headset; falls back to auto if none found
      any string  — matched as a case-insensitive substring of the device name
    """
    if preference == 'auto':
        return _auto_select_input()

    if preference == 'bluetooth':
        for i, d in _devices_with_inputs():
            if _score(d['name']) == 3:
                return i, d['name']
        return _auto_select_input()

    if preference == 'usb':
        for i, d in _devices_with_inputs():
            if _score(d['name']) == 2:
                return i, d['name']
        return _auto_select_input()

    return _select_by_substr(preference, inputs=True)


def find_output_device(preference: str = 'auto') -> tuple[int | None, str]:
    """Return (device_index, name) for the best available output device.

    preference: same semantics as find_input_device.
    """
    if preference == 'auto':
        return _auto_select_output()

    if preference == 'bluetooth':
        for i, d in _devices_with_outputs():
            if _score(d['name']) == 3:
                return i, d['name']
        return _auto_select_output()

    if preference == 'usb':
        for i, d in _devices_with_outputs():
            if _score(d['name']) == 2:
                return i, d['name']
        return _auto_select_output()

    return _select_by_substr(preference, inputs=False)


def list_devices() -> str:
    """Return a formatted string of all audio devices — useful for debugging."""
    lines = ['Available audio devices:']
    for i, d in enumerate(sd.query_devices()):
        tag = []
        if d['max_input_channels'] >= 1:
            tag.append('IN')
        if d['max_output_channels'] >= 1:
            tag.append('OUT')
        lines.append(f'  [{i:2d}] {"/".join(tag):6s}  {d["name"]}')
    return '\n'.join(lines)
