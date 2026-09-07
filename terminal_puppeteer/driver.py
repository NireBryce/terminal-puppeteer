"""pty/subprocess mechanics: draining output, spawning and driving the
target process, and the driver-side step forms that wait instead of
write (SLEEP <seconds>, WAIT-FOR:<pattern>) plus the optional canned
replies to terminal capability queries (--answer-queries). See
terminal_puppeteer.cli for the CLI surface that calls these, and
README.md's Timing/Exit behavior sections for what this is actually
implementing and why.
"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

from .ansi import ANSI
from .keys import encode_step, parse_wait_for_step, parse_wait_step


def _claim_target_tty():
    """Runs in the child, between fork() and exec() (Popen preexec_fn -- a
    single-threaded parent is a precondition, and this tool has no
    threads). Gives the target its own session with the pty as its
    controlling terminal: setsid() alone only detaches it from ours,
    dup2-ing the slave fd onto 0/1/2 does not acquire controlling status
    (only opening a tty can, post-setsid), so TIOCSCTTY is what makes
    tcgetpgrp/tcsetpgrp work in the target. Without it, bash/zsh print
    their 'no job control' warnings and run degraded (no SIGINT to
    foreground jobs, no CTRL-Z/fg), and fish exits outright with rc=1
    before drawing a prompt (issue #37). subprocess guarantees fds 0/1/2
    are already the pty by preexec_fn time, so fd 0 is the slave."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _termcap_reply(match):
    # Reply to a DCS termcap query (\eP+q<capname-as-hex>\e\\) with the
    # same capname and no value -- the shape verified live against fish
    # 4.8.1 (issue #37): enough for a probing target to treat the
    # capability as absent and move on instead of waiting for a reply
    # that never comes. Reply depends on which capability was asked, so
    # this is a callable rather than fixed bytes.
    return b'\x1bP1+r' + match.group(1) + b'\x1b\\'

# Canned replies to the terminal capability queries a target may send its
# terminal on startup (--answer-queries). A bare pty answers nothing, and
# a target that probes-then-fallbacks can take a genuinely different code
# path with no reply than it would behind a real terminal -- which makes
# repros silently diverge from what a human sees, not just render
# differently (issue #43). These say "konsole-ish xterm" just well enough
# for such a target to take its normal-terminal path; they are canned
# lies, which is why this is opt-in. The kitty/OSC 11/termcap entries
# exist because fish 4.x probes exactly those on startup and hangs before
# its first prompt without replies even when it has a controlling
# terminal (verified live, issue #37). A reply may be fixed bytes or a
# callable taking the match (termcap: the reply echoes the queried
# capname). Queries are answered on EVERY occurrence, not once per run:
# a real terminal answers each time, and a line editor re-sends its
# capability queries on every prompt redraw -- an unanswered repeat sends
# the target down its no-reply fallback path mid-session (fish times
# out, redraws, and flushes its input buffer, eating keystrokes typed
# into the race window). Matches are checked against the accumulated
# output because a query can straddle two reads. See NOTES.md's
# unanswered-queries gotcha for the default behavior this flag changes.
QUERY_RESPONDERS = [
    (re.compile(rb'\x1b\[>c'), b'\x1b[>85;3800;0c'),          # DA2 (terminal name/version)
    (re.compile(rb'\x1b\[0?c'), b'\x1b[?62;3c'),              # DA1 (bare, or param 0 -- fish sends \e[0c)
    (re.compile(rb'\x1b\[6n'), b'\x1b[1;1R'),                 # DSR cursor position
    (re.compile(rb'\x1b\[>0?q'), b'\x1bP>| Konsole (250203)\x1b\\'),  # XTVERSION
    (re.compile(rb'\x1b\[\?u'), b'\x1b[?1u'),                 # kitty keyboard protocol query
    (re.compile(rb'\x1b\]11;\?'), b'\x1b]11;rgb:0000/0000/0000\x1b\\'),  # OSC 11 background color
    (re.compile(rb'\x1bP\+q([0-9a-f]*)\x1b\\'), _termcap_reply),  # DCS termcap query
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


class TargetDied(Exception):
    """The target process exited while steps remained to be sent -- a
    failed repro, raised instead of typing the remaining steps into a
    pty nobody is reading (issue #37's masquerade: the pty echoes the
    steps anyway, so a dead target looked like a quiet successful run).
    `.output` carries the capture up to that point; it is diagnostic
    (usually the target's own dying words), so the CLI still prints it.
    Dying after the LAST step is the normal clean-logout shape (`'exit'
    ENTER` as the final step) and is deliberately not an error."""

    def __init__(self, message, output):
        super().__init__(message)
        self.output = output


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
            # -- a query can straddle two reads. `answered[i]` counts how
            # many occurrences of pattern i have been replied to; every
            # new occurrence gets its own reply, same as a real terminal
            # answering a target that re-queries on every prompt redraw.
            for i, (query, reply) in enumerate(QUERY_RESPONDERS):
                matches = list(query.finditer(out))
                for m in matches[answered.get(i, 0):]:
                    os.write(controller,
                             reply(m) if callable(reply) else reply)
                answered[i] = len(matches)
    return out


def _exit_status_why(rc):
    """Human words for a child's exit status: signal name where the
    target was killed, plain status where it exited on its own."""
    if rc < 0:
        try:
            return f'killed by signal {-rc} ({signal.Signals(-rc).name})'
        except ValueError:
            return f'killed by signal {-rc}'
    return f'exited with status {rc}'


def wait_for(controller, proc, seen, step, pattern, timeout, answered=None):
    """Drain until the ANSI-stripped output accumulated so far matches
    `pattern`, or timeout. Matching the stripped stream by default is
    the point (issue #46): matching raw bytes is where a line editor
    redrawing mid-marker puts cursor-positioning escapes between the
    characters of the thing being waited for. Stripping the whole
    accumulator each poll, not a tail: an escape sequence can straddle
    two reads, and repro-scale output is small. The target dying
    mid-wait fails fast (TargetDied) instead of burning the whole
    budget: with its last fd closed the pattern cannot arrive any
    more, so waiting is guaranteed futile. The pattern is re-checked
    before the liveness test, so a marker in the target's dying output
    burst still matches normally."""
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
        if proc.poll() is not None:
            raise TargetDied(
                f'target {_exit_status_why(proc.returncode)} while '
                f'WAIT-FOR step {step!r} was still waiting -- the '
                f'pattern cannot arrive any more',
                seen,
            )
    return seen


def run(argv, steps, step_delay, settle, connect_wait, env=None, verbose=False,
        answer_queries=False, wait_for_timeout=10.0, rows=24, cols=80):
    # The two pty fds are named controller/target (our end / the spawned
    # program's end) -- the same vocabulary README.md and NOTES.md use
    # for the thing being driven. pty.openpty()'s own parameter names
    # mirror the POSIX API's traditional terms; this project
    # deliberately does not carry them.
    controller, target = pty.openpty()
    # A fresh real terminal hands the target a window size (xterm's 80x24
    # convention); pty.openpty() leaves the tty at 0x0. Some targets paper
    # over it (interactive bash falls back to its own 80x24 default), but
    # anything that reads the tty directly -- stty, ncurses, fish's line
    # editor -- sees zero columns and behaves like no other terminal
    # would (surfaced during the #37 chase: fish's cursor math ran on a
    # zero-width line). Override the 24x80 default with --rows/--cols.
    fcntl.ioctl(target, termios.TIOCSWINSZ,
                struct.pack('HHHH', rows, cols, 0, 0))
    try:
        proc = subprocess.Popen(
            argv, stdin=target, stdout=target, stderr=target, close_fds=True,
            env=env, preexec_fn=_claim_target_tty,
        )
    except OSError:
        # argv[0] doesn't exist, isn't executable, etc. -- close both ends
        # ourselves (nothing else will) and let the caller decide how to
        # report it; see issue #21.
        os.close(target)
        os.close(controller)
        raise
    os.close(target)
    answered = {} if answer_queries else None
    try:
        out = drain(controller, connect_wait, answered=answered)
        for i, step in enumerate(steps):
            if proc.poll() is not None:
                # The target is gone but steps remain -- every one of
                # them would land in a pty nobody is reading, with the
                # pty's own echo making the run LOOK like it worked
                # (issue #37). Fail with the capture attached: it is
                # the diagnostic, typically the target's dying words.
                raise TargetDied(
                    f'target {_exit_status_why(proc.returncode)} after '
                    f'{i} of {len(steps)} steps; '
                    f'{len(steps) - i} not sent, next would be {step!r} -- '
                    f'the captured output is the pty echo of your steps, '
                    f'not the target responding',
                    out,
                )
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
                out = wait_for(controller, proc, out, step, pattern,
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
            proc.wait()  # reaps; an interactive shell ignores SIGTERM, so
            # the kill path is the normal one for bash -i targets
        os.close(controller)
    return out
