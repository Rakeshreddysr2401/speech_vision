"""
Quick smoke-test for audio_device selection logic.

Run on host (no sounddevice needed — uses mock):
    python3 ai_ws/src/voice_pkg/voice_pkg/test_audio_device.py

Run inside container to see real devices:
    docker exec ai_stack python3 /workspaces/ai_ws/src/voice_pkg/voice_pkg/test_audio_device.py --real
"""
import sys
import types

REAL = '--real' in sys.argv

# ── Mock sounddevice with a fake device list ──────────────────────────────────
if not REAL:
    FAKE_DEVICES = [
        {'name': 'NVIDIA Jetson Orin Nano HDA: HDMI 0 (hw:0,3)', 'max_input_channels': 0, 'max_output_channels': 8},
        {'name': 'Plantronics Blackwire 3220 Seri',               'max_input_channels': 2, 'max_output_channels': 2},
        {'name': 'Brio 100',                                       'max_input_channels': 1, 'max_output_channels': 0},
        {'name': 'bluez_card.AA_BB_CC_DD_EE_FF (a2dp)',           'max_input_channels': 1, 'max_output_channels': 2},
    ]
    sd_mock = types.ModuleType('sounddevice')
    sd_mock.query_devices = lambda: FAKE_DEVICES
    sys.modules['sounddevice'] = sd_mock

import audio_device as ad  # noqa: E402  (must come after mock install)


def check(label, got_name, expected_substr, *, must_not_contain=None):
    ok = expected_substr.lower() in got_name.lower()
    if must_not_contain:
        ok = ok and must_not_contain.lower() not in got_name.lower()
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {label}: "{got_name}"')
    return ok


print('\n=== audio device selection test ===\n')

if REAL:
    print(ad.list_devices())
    print()

all_pass = True

# --- Input (mic) tests ---
print('-- Input (mic) --')
idx, name = ad.find_input_device('auto')
all_pass &= check('auto: picks Plantronics over BT and Brio', name, 'plantronics', must_not_contain='brio')

idx, name = ad.find_input_device('bluetooth')
all_pass &= check("preference='bluetooth': picks BT device", name, 'bluez' if not REAL else 'bt')

idx, name = ad.find_input_device('usb')
all_pass &= check("preference='usb': picks Plantronics", name, 'plantronics')

# Brio must never be selected
brio_selected = any(
    'brio' in d['name'].lower() or 'logitech' in d['name'].lower()
    for _, d in ad._devices_with_inputs()
    if not ad._is_blacklisted_input(d['name'])
)
status = 'PASS' if not brio_selected else 'FAIL'
all_pass &= not brio_selected
print(f'  [{status}] Brio/Logitech never passes blacklist filter')

# --- Output (speaker) tests ---
print('\n-- Output (speaker) --')
idx, name = ad.find_output_device('auto')
all_pass &= check('auto: picks Plantronics over BT', name, 'plantronics')

idx, name = ad.find_output_device('bluetooth')
all_pass &= check("preference='bluetooth': picks BT device", name, 'bluez' if not REAL else 'bt')

idx, name = ad.find_output_device('usb')
all_pass &= check("preference='usb': picks Plantronics", name, 'plantronics')

print(f'\n{"ALL PASS" if all_pass else "SOME FAILED"}\n')
sys.exit(0 if all_pass else 1)
