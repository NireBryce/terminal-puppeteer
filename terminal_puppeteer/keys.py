"""The step vocabulary: named keys (TAB, ENTER, CTRL-<letter>, the
arrow/navigation keys, ...), RAW:<escapes> and PASTE:<text> byte strings,
and the driver-side step forms (SLEEP <seconds>, RESIZE <rows> <cols>,
WAIT-FOR:<pattern>, WAIT-FOR-NEXT:<pattern>) -- and how a step turns into
the bytes actually sent to the pty, or into a wait/resize the driver
performs instead of a write. See README.md's Steps section and
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

# A PASTE:-prefixed step is the payload wrapped in real bracketed-paste
# framing (\e[200~ ... \e[201~), sent as one write, with the payload run
# through decode_raw() too -- the only way to express a multi-line paste
# in one steps-file line. A plain multi-line write is NOT the same thing
# (each embedded newline submits the line before it); with the framing, a
# line editor that has enabled bracketed-paste mode treats the whole
# payload as one paste and holds its newlines literal. See NOTES.md.
PASTE_PREFIX = 'PASTE:'

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
# marker, where cursor-positioning escapes interleave with the characters
# of the thing being waited for). See issue #46.
WAIT_FOR_PREFIX = 'WAIT-FOR:'

# WAIT-FOR-NEXT:<pattern> -- the same matching, but only over output that
# arrives after the step starts. WAIT-FOR matches the whole transcript,
# which includes the echo of your own typing (NOTES.md's gotcha) and any
# prompt that already contained the marker; NEXT can't match either. No
# prefix ambiguity to resolve: WAIT-FOR-NEXT: does not start with
# WAIT-FOR: (the seventh character differs), so each parse function keys
# on its own prefix alone.
WAIT_FOR_NEXT_PREFIX = 'WAIT-FOR-NEXT:'

# RESIZE <rows> <cols> -- a driver-side resize of the pty mid-run, not a
# write: TIOCSWINSZ on the pty, then the kernel delivers SIGWINCH to the
# target's foreground process group itself. --rows/--cols set the size
# the pty starts with; this retimes one mid-run, for repros that are
# size-sensitive in a way that only shows up after a resize.
RESIZE_STEP_RE = re.compile(r'^RESIZE\s+(.+)$', re.IGNORECASE)

# The winsize struct's fields are 16-bit unsigned shorts -- the ceiling
# TIOCSWINSZ can actually carry, not an arbitrary one.
WINSIZE_MAX = 0xFFFF

# Lowercase 'sleep 30'/'wait 30' matches WAIT_STEP_RE (case-insensitive),
# so it's a driver-side wait that sends nothing -- but it *reads* like a
# command being typed, which is exactly the mistake NOTES.md documents.
# This regex deliberately has no IGNORECASE: the uppercase spellings are
# the documented driver form and get no advisory, only the
# looks-like-a-typed-command shape does.
TYPED_WAIT_RE = re.compile(r'^(sleep|wait)\s+\d')


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


def parse_wait_for_next_step(step):
    """WAIT-FOR-NEXT:<pattern> -> a compiled bytes regex matching only
    the ANSI-stripped output that arrives after the step starts (the
    driver measures where "after" begins; see driver.wait_for); None if
    the step isn't a WAIT-FOR-NEXT step; ValueError for an empty pattern
    or a bad regex. Pattern forms are exactly WAIT-FOR's."""
    if not step.upper().startswith(WAIT_FOR_NEXT_PREFIX):
        return None
    pattern = step[len(WAIT_FOR_NEXT_PREFIX):]
    if not pattern:
        raise ValueError(f'{WAIT_FOR_NEXT_PREFIX}needs a pattern to wait for')
    if len(pattern) > 1 and pattern.startswith('/') and pattern.endswith('/'):
        try:
            return re.compile(pattern[1:-1].encode())
        except re.error as e:
            raise ValueError(
                f'{WAIT_FOR_NEXT_PREFIX} regex {pattern!r}: {e}'
            ) from None
    return re.compile(re.escape(pattern.encode()))


def parse_paste_step(step):
    """PASTE:<text> -> the payload text (in decode_raw's escape form --
    the bytes it decodes to are the paste's contents); None if the step
    isn't a PASTE step; ValueError for an empty payload. Validation is
    the CLI's usage-error gate (same shape as WAIT-FOR's); encode_step
    keeps its own identical guard for direct callers."""
    if not step.upper().startswith(PASTE_PREFIX):
        return None
    payload = step[len(PASTE_PREFIX):]
    if not payload:
        raise ValueError(f'{PASTE_PREFIX}needs text to paste')
    return payload


def parse_resize_step(step):
    """RESIZE <rows> <cols> -> the (rows, cols) tuple; None if the step
    isn't a resize step; ValueError unless exactly two integers, each at
    least 1 (a 0x0 tty is exactly what --rows/--cols exist to prevent)
    and at most 65535 (the winsize struct field is a 16-bit short --
    beyond it the driver's TIOCSWINSZ pack would raise struct.error, so
    the ceiling is validated here where it can be a usage error)."""
    m = RESIZE_STEP_RE.match(step)
    if not m:
        return None
    parts = m.group(1).split()
    if len(parts) != 2:
        raise ValueError(
            f'RESIZE takes exactly two integers (rows cols), e.g. '
            f'RESIZE 40 120 -- got {m.group(1)!r}'
        )
    try:
        rows, cols = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f'RESIZE rows/cols must be integers, e.g. RESIZE 40 120 -- '
            f'got {m.group(1)!r}'
        ) from None
    if rows < 1 or cols < 1:
        raise ValueError(f'RESIZE rows/cols must be at least 1, got {rows}x{cols}')
    if rows > WINSIZE_MAX or cols > WINSIZE_MAX:
        raise ValueError(
            f'RESIZE rows/cols must be at most {WINSIZE_MAX} '
            f'(16-bit winsize fields), got {rows}x{cols}'
        )
    return rows, cols


def encode_step(step):
    if step.upper().startswith(RAW_PREFIX):
        return decode_raw(step[len(RAW_PREFIX):])
    if step.upper().startswith(PASTE_PREFIX):
        payload = step[len(PASTE_PREFIX):]
        if not payload:
            # Same guard parse_paste_step enforces at validation time --
            # this one catches direct driver.run() callers that never
            # went through the CLI.
            raise ValueError(f'{PASTE_PREFIX}needs text to paste')
        # One write: framing and payload together, so the target's
        # bracketed-paste parser sees them as a single paste.
        return b'\x1b[200~' + decode_raw(payload) + b'\x1b[201~'
    key = step.upper()
    if key in SPECIAL_KEYS:
        return SPECIAL_KEYS[key]
    m = CTRL_KEY_RE.match(key)
    if m:
        return bytes([ord(m.group(1)) - 64])
    return step.encode()


def warn_if_near_miss(step):
    # Two advisory-only heads-ups, neither ever blocking or changing what
    # encode_step sends -- a false positive costs a stray stderr line,
    # not a broken run:
    #
    # 1. A step that doesn't match any known key already got sent as
    #    literal text, unchanged -- this flags the case that's actually a
    #    typo'd key name, not intended literal text: 'CTRLC' (missing
    #    hyphen), 'ENTR', 'HOEM'.
    # 2. A lowercase 'sleep 30'/'wait 30' IS a recognized step (WAIT_STEP_RE
    #    is case-insensitive) -- a driver-side wait that sends nothing, when
    #    it reads like a command being typed into the target. That's the
    #    exact mistake NOTES.md's gotcha documents (the repro whose sleep
    #    was never sent); the advisory points at the RAW: form that types
    #    it for real. Uppercase spellings are the documented driver form
    #    and stay silent.
    key = step.upper()
    if TYPED_WAIT_RE.match(step):
        print(
            f"terminal-puppeteer.py: note: step {step!r} is a driver-side "
            f"wait -- it sends nothing; to type that command in the target "
            f"instead, use 'RAW:{step}\\r'",
            file=sys.stderr,
        )
    if key in SPECIAL_KEYS or CTRL_KEY_RE.match(key):
        return
    # The other step forms are their own vocabulary, matched exactly by
    # prefix/parse -- a near-miss warning against the *key* table is noise
    # for them ('RAW:CR' is three-quarters CR by difflib's math, and is
    # not a typo of it). Malformed forms among them are a usage error
    # at validation time regardless, so nothing to warn about here either.
    if (key.startswith(RAW_PREFIX) or key.startswith(PASTE_PREFIX)
            or key.startswith(WAIT_FOR_PREFIX)
            or key.startswith(WAIT_FOR_NEXT_PREFIX)
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
        ('PASTE:<text>', 'bracketed-paste wrap (\\e[200~ text \\e[201~), one write; escapes decoded'),
        ('SLEEP <seconds>', 'wait N seconds, sending nothing (driver-side)'),
        ('RESIZE <rows> <cols>', 'resize the pty mid-run; the kernel SIGWINCHes the target'),
        ('WAIT-FOR:<pattern>', 'drain until stripped output matches (substring, or /regex/)'),
        ('WAIT-FOR-NEXT:<pattern>', 'same, but only output arriving after this step (ignores echoes)'),
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
