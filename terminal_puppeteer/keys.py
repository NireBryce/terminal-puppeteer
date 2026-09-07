"""The step vocabulary: named keys (TAB, ENTER, CTRL-<letter>, the
arrow/navigation keys, ...), RAW:<escapes> byte strings, and the
driver-side step forms (SLEEP <seconds>, WAIT-FOR:<pattern>) -- and how
a step turns into the bytes actually sent to the pty, or into a wait the
driver performs instead of a write. See README.md's Steps section and
--list-keys for the human-facing reference this module's data backs --
both draw from the same SPECIAL_KEYS/CTRL_KEY_RE this module defines,
not a second transcription of the same table.
"""
import difflib
import math
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

# A RAW:-prefixed step goes through decode_raw()'s bind/readline-style
# escape parser instead of being sent as literal text -- the explicit
# prefix is the whole design: a literal step was never supposed to grow
# escape syntax (plain text starting with a backslash would silently
# change meaning), so an opt-in form keeps both unambiguous. See issue #45.
RAW_PREFIX = 'RAW:'

# decode_raw()'s single-character escapes -- the same set bind and `echo
# -e` understand, minus the ones this vocabulary has no use for (\c, \0).
RAW_CHAR_ESCAPES = {
    'a': b'\a', 'b': b'\b', 't': b'\t', 'n': b'\n', 'v': b'\v',
    'f': b'\f', 'r': b'\r', 'e': b'\x1b', '\\': b'\\',
}

# SLEEP <seconds> / WAIT <seconds> -- a driver-side pause, not a write:
# the driver drains for N seconds and sends nothing. A target-side
# 'sleep 5' ENTER step instead runs a command *in the session under
# test* (history, PROMPT_COMMAND hooks, transcript noise), which is
# exactly what this form avoids. See issue #44.
WAIT_STEP_RE = re.compile(r'^(SLEEP|WAIT)\s+(.+)$', re.IGNORECASE)

# WAIT-FOR:<pattern> -- drain until the ANSI-stripped output accumulated
# so far matches: a literal substring, or a regex when wrapped in
# /.../ (the regex form is what survives a line editor redrawing mid-
# marker, where cursor-position escapes interleave with the characters
# of the thing being waited for). See issue #46.
WAIT_FOR_PREFIX = 'WAIT-FOR:'


def decode_raw(text):
    # bind/readline-style escapes -> bytes. Unknown escapes stay literal
    # (backslash + the character), same as `echo -e` -- predictability
    # over silent reinterpretation of plain text.
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c != '\\':
            out += c.encode()
            i += 1
            continue
        if i + 1 >= len(text):  # a lone trailing backslash stays literal
            out += b'\\'
            break
        n = text[i + 1]
        if n in RAW_CHAR_ESCAPES:
            out += RAW_CHAR_ESCAPES[n]
            i += 2
        elif n in 'Cc' and i + 3 < len(text) and text[i + 2] == '-' and (
                text[i + 3].isalpha() or text[i + 3] in r'@[\]^_'):
            # \C-x / \c-x: ord(key) & 0x1f is the control-code formula
            # bind itself uses -- covers letters both cases plus @[\]^_,
            # e.g. \C-[ = \x1b, same byte as \e.
            out.append(ord(text[i + 3].upper()) & 0x1f)
            i += 4
        elif n == 'x':
            digits = text[i + 2:i + 4]
            hexrun = ''
            for ch in digits:
                if ch in string.hexdigits and len(hexrun) < 2:
                    hexrun += ch
                else:
                    break
            if hexrun:
                out.append(int(hexrun, 16))
                i += 2 + len(hexrun)
            else:
                out += b'\\x'  # \x with no hex digits stays literal
                i += 2
        else:
            out += f'\\{n}'.encode()
            i += 2
    return bytes(out)


def parse_wait_step(step):
    """SLEEP/WAIT <seconds> -> the number of seconds to drain for; None
    if the step isn't a wait step at all; ValueError if it is one but
    the duration is unusable (not a number, negative, non-finite)."""
    m = WAIT_STEP_RE.match(step)
    if not m:
        return None
    try:
        seconds = float(m.group(2))
    except ValueError:
        raise ValueError(
            f"{m.group(1).upper()} duration {m.group(2)!r} isn't a number of seconds"
        ) from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"{m.group(1).upper()} duration {m.group(2)!r} must be a finite, non-negative number of seconds")
    return seconds


def parse_wait_for_step(step):
    """WAIT-FOR:<pattern> -> a compiled bytes regex matching the
    ANSI-stripped output (substrings get re.escape'd); None if the step
    isn't a WAIT-FOR step; ValueError for an empty pattern or a bad
    regex."""
    if not step.upper().startswith(WAIT_FOR_PREFIX):
        return None
    pattern = step[len(WAIT_FOR_PREFIX):]
    if not pattern:
        raise ValueError(f'{WAIT_FOR_PREFIX}needs a pattern to wait for')
    if len(pattern) > 1 and pattern.startswith('/') and pattern.endswith('/'):
        try:
            return re.compile(pattern[1:-1].encode())
        except re.error as e:
            raise ValueError(f'{WAIT_FOR_PREFIX} regex {pattern!r}: {e}') from None
    return re.compile(re.escape(pattern.encode()))


def encode_step(step):
    if step.upper().startswith(RAW_PREFIX):
        return decode_raw(step[len(RAW_PREFIX):])
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
    # The other step forms are their own vocabulary, matched exactly by
    # prefix/parse -- a near-miss warning against the *key* table is noise
    # for them ('RAW:CR' is three-quarters CR by difflib's math, and is
    # not a typo of it). Malformed SLEEP/WAIT-FOR steps are a usage error
    # at validation time regardless, so nothing to warn about here either.
    if (key.startswith(RAW_PREFIX) or key.startswith(WAIT_FOR_PREFIX)
            or WAIT_STEP_RE.match(step)):
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
    # parallel list to keep in sync. The non-key step forms get rows too:
    # this is the closest thing to a one-screen step-vocabulary reference.
    groups = {}
    for name, value in SPECIAL_KEYS.items():
        groups.setdefault(value, []).append(name)
    extra_rows = [
        ('RAW:<escapes>', "bind-style escapes, e.g. 'RAW:\\e[I' = \\x1b[I"),
        ('SLEEP <seconds>', 'wait N seconds, sending nothing (driver-side)'),
        ('WAIT-FOR:<pattern>', 'drain until stripped output matches (substring, or /regex/)'),
    ]
    width = max(
        [len(', '.join(names)) for names in groups.values()]
        + [len('CTRL-<A-Z>')] + [len(label) for label, _ in extra_rows]
    )
    for value, names in groups.items():
        print(f"{', '.join(names):<{width}}  {value!r}")
    print(f"{'CTRL-<A-Z>':<{width}}  any letter, e.g. CTRL-C = {bytes([3])!r}")
    for label, description in extra_rows:
        print(f"{label:<{width}}  {description}")
