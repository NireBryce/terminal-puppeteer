"""The step vocabulary: named keys (TAB, ENTER, CTRL-<letter>, the
arrow/navigation keys, ...) and how a step turns into the bytes actually
sent to the pty. See README.md's Steps section and --list-keys for the
human-facing reference this module's data backs -- both draw from the
same SPECIAL_KEYS/CTRL_KEY_RE this module defines, not a second
transcription of the same table.
"""
import difflib
import re
import string
import sys

SPECIAL_KEYS = {
    'TAB': b'\t',
    'ENTER': b'\r',
    'RET': b'\r',
    'CR': b'\r',
    'NEWLINE': b'\n',
    'ESC': b'\x1b',
    'BACKSPACE': b'\x7f',
    'UP': b'\x1b[A',
    'DOWN': b'\x1b[B',
    'RIGHT': b'\x1b[C',
    'LEFT': b'\x1b[D',
    'HOME': b'\x1b[H',
    'END': b'\x1b[F',
    'DELETE': b'\x1b[3~',
    'PGUP': b'\x1b[5~',
    'PGDN': b'\x1b[6~',
}

# CTRL-<letter> is computed, not enumerated: any A-Z, not just the C/D/R this
# used to hardcode. ord(letter) - 64 is the standard terminal control-code
# formula (CTRL-C -> 67-64 -> 3 -> \x03, same value the old hardcoded entry
# had; CTRL-A, CTRL-U, CTRL-L, CTRL-W, etc. all now work the same way).
CTRL_KEY_RE = re.compile(r'^CTRL-([A-Z])$')

# For the near-miss warning below, and for --list-keys.
ALL_KEY_NAMES = sorted(SPECIAL_KEYS) + [f'CTRL-{c}' for c in string.ascii_uppercase]


def encode_step(step):
    key = step.upper()
    if key in SPECIAL_KEYS:
        return SPECIAL_KEYS[key]
    m = CTRL_KEY_RE.match(key)
    if m:
        return bytes([ord(m.group(1)) - 64])
    return step.encode()


def warn_if_near_miss(step):
    # A step that doesn't match any known key already got sent as literal
    # text by encode_step, unchanged -- this only adds a heads-up to
    # stderr for the case that's actually a typo'd key name, not intended
    # literal text: 'CTRLC' (missing hyphen), 'ENTR', 'HOEM'. Advisory
    # only, never blocks -- a false positive costs a stray warning line,
    # not a broken run.
    key = step.upper()
    if key in SPECIAL_KEYS or CTRL_KEY_RE.match(key):
        return
    match = difflib.get_close_matches(key, ALL_KEY_NAMES, n=1, cutoff=0.8)
    if match:
        print(
            f"terminal-puppeteer.py: warning: step {step!r} doesn't match a "
            f"known key but looks close to {match[0]!r} -- sending it as "
            f"literal text, in case that's a typo",
            file=sys.stderr,
        )


def print_key_list():
    # Grouped by byte value in SPECIAL_KEYS's own definition order (ENTER/
    # RET/CR land in one row this way) -- single source of truth, no
    # parallel list to keep in sync.
    groups = {}
    for name, value in SPECIAL_KEYS.items():
        groups.setdefault(value, []).append(name)
    width = max([len(', '.join(names)) for names in groups.values()] + [len('CTRL-<A-Z>')])
    for value, names in groups.items():
        print(f"{', '.join(names):<{width}}  {value!r}")
    print(f"{'CTRL-<A-Z>':<{width}}  any letter, e.g. CTRL-C = {bytes([3])!r}")
