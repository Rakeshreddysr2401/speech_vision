"""Wake-word gate — pure logic, no ROS imports (unit-testable off-robot).

The mic hears everything in the room; without a gate the robot answers every
utterance anyone says. An utterance is forwarded to the brain only if:

  1. it starts with (or ends with) a wake alias — "Rakhi, what's the weather",
     "hey Rakhi", "what time is it, Rakhi" — the alias is stripped before
     forwarding; or
  2. the attention window is open — the robot spoke or was addressed within the
     last N seconds, so follow-ups don't need the name repeated.

Aliases are a list because Whisper spells an uncommon name many ways
("Rakhi" / "Rocky" / "Raki" / …). Tune the list from real transcripts in the
Orin log — every ignored utterance is logged with its text.
"""

_STRIP = ".,!?…'\"“”‘’"


def _norm(word: str) -> str:
    return word.strip(_STRIP).lower()


def wake_gate(text: str, aliases: set[str], attention_active: bool) -> tuple[bool, str]:
    """Return (forward, forwarded_text).

    forwarded_text has the wake alias stripped; a bare wake call ("Rakhi?")
    forwards the original text so the brain can respond to being called.
    """
    words = text.split()
    if not words:
        return False, text

    # Alias in the first three words ("Rakhi …", "hey Rakhi …", "ok so Rakhi …")
    for i, w in enumerate(words[:3]):
        if _norm(w) in aliases:
            rest = " ".join(words[i + 1:]).lstrip(" ,.!?")
            return True, (rest if rest else text)

    # Trailing alias ("what time is it, Rakhi")
    if len(words) > 1 and _norm(words[-1]) in aliases:
        rest = " ".join(words[:-1]).rstrip(" ,.!?")
        return True, (rest if rest else text)

    if attention_active:
        return True, text

    return False, text
