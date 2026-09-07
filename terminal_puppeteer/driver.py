"""pty/subprocess mechanics: draining output, spawning and driving the
target process, and the driver-side step forms that wait instead of
write (SLEEP <seconds>, WAIT-FOR:<pattern>) plus the optional canned
replies to terminal capability queries (--answer-queries). See
terminal_puppeteer.cli for the CLI surface that calls these, and
README.md's Timing/Exit behavior sections for what this is actually
implementing and why.
"""
import os
import pty
import re
import select
import subprocess
import sys
import time

from .ansi import ANSI
from .keys import encode_step, parse_wait_for_step, parse_wait_step

# Canned replies to the terminal capability queries a target may send its
# terminal on startup (--answer-queries). A bare pty answers nothing, and
# a target that probes-then-fallbacks can take a genuinely different code
# path with no reply than it would behind a real terminal -- which makes
# repros silently diverge from what a human sees, not just render
# differently (issue #43). These say "konsole-ish xterm" just well enough
# for such a target to take its normal-terminal path; they are canned
# lies, which is why this is opt-in. Each pattern is answered at most
# once per run. See NOTES.md's unanswered-queries gotcha for the default
# behavior this flag changes.
QUERY_RESPONDERS = [
    (re.compile(rb'\x1b\[>c'), b'\x1b[>85;3800;0c'),          # DA2 (terminal name/version)
    (re.compile(rb'\x1b\[c'), b'\x1b[?62;3c'),                # DA1
    (re.compile(rb'\x1b\[6n'), b'\x1b[1;1R'),                 # DSR cursor position
    (re.compile(rb'\x1b\[>0?q'), b'\x1bP>| Konsole (250203)\x1b\\'),  # XTVERSION
]

# The reply bytes are written into the target's input, so the pty's own
# echo bounces them into the captured output -- harmless (none of them
# re-match a query pattern above), and the same thing the hand-rolled
# responder this replaces did.
DRAIN_POLL = 0.2


class WaitForTimeout(Exception):
    """A WAIT-FOR step's pattern never showed up in the output within
    its budget. Failed loudly on purpose (issue #46): a pattern that
    never arrives must not silently become a slow no-op."""


def drain(controller, seconds, chunk=65536, answered=None):
    out = b''
    end = time.time() + seconds
    got_data = False
    while time.time() < end:
        r, _, _ = select.select([controller], [], [], DRAIN_POLL)
        if not r:
            if got_data:
                break
            continue
        try:
            data = os.read(controller, chunk)
        except OSError:
            break
        if not data:
            break
        out += data
        got_data = True
        if answered is not None:
            # Checked against the accumulated output, not just this chunk
            # -- a query can straddle two reads; the answered set keeps
            # each reply to once per run.
            for i, (query, reply) in enumerate(QUERY_RESPONDERS):
                if i not in answered and query.search(out):
                    os.write(controller, reply)
                    answered.add(i)
    return out


def wait_for(controller, seen, step, pattern, timeout, answered=None):
    """Drain until the ANSI-stripped output accumulated so far matches
    `pattern`, or timeout. Matching the stripped stream by default is
    the point (issue #46): matching raw bytes is where a line editor
    redrawing mid-marker puts cursor-positioning escapes between the
    characters of the thing being waited for. Stripping the whole
    accumulator each poll, not a tail: an escape sequence can straddle
    two reads, and repro-scale output is small."""
    deadline = time.time() + timeout
    while not pattern.search(ANSI.sub(b'', seen)):
        remaining = deadline - time.time()
        if remaining <= 0:
            tail = ANSI.sub(b'', seen)[-200:].decode('utf-8', 'replace')
            raise WaitForTimeout(
                f'WAIT-FOR step {step!r}: no match within {timeout:g}s -- '
                f'last output seen (ANSI-stripped): {tail!r}'
            )
        seen += drain(controller, min(DRAIN_POLL, remaining), answered=answered)
    return seen


def run(argv, steps, step_delay, settle, connect_wait, env=None, verbose=False,
        answer_queries=False, wait_for_timeout=10.0):
    # pty.openpty() calls these master/slave (matching the underlying POSIX
    # API and Python's own pty module docs) -- renamed here to the terms
    # this project already uses everywhere else: `controller` is our end,
    # `target` is the spawned program's end, same as README.md/NOTES.md's
    # own vocabulary for "the thing being driven".
    controller, target = pty.openpty()
    try:
        proc = subprocess.Popen(
            argv, stdin=target, stdout=target, stderr=target, close_fds=True,
            env=env,
        )
    except OSError:
        # argv[0] doesn't exist, isn't executable, etc. -- close both ends
        # ourselves (nothing else will) and let the caller decide how to
        # report it; see issue #21.
        os.close(target)
        os.close(controller)
        raise
    os.close(target)
    answered = set() if answer_queries else None
    try:
        out = drain(controller, connect_wait, answered=answered)
        for step in steps:
            if verbose:
                print(f'>> {step}', file=sys.stderr)
            wait = parse_wait_step(step)
            if wait is not None:
                # A driver-side wait sends nothing -- that's the point
                # (issue #44): keep draining, write no bytes.
                out += drain(controller, wait, answered=answered)
                continue
            pattern = parse_wait_for_step(step)
            if pattern is not None:
                out = wait_for(controller, out, step, pattern,
                               wait_for_timeout, answered=answered)
                continue
            os.write(controller, encode_step(step))
            out += drain(controller, step_delay, answered=answered)
        out += drain(controller, settle, answered=answered)
    finally:
        # Runs on a clean finish same as on a Ctrl+C/exception mid-drain --
        # without this, interrupting the tool itself orphans the target
        # process instead of the "only ever terminates the target" README
        # promises. See issue #8.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(controller)
    return out
