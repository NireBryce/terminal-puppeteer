"""The Session core: a pty, a spawned target, and the per-step dispatch
(named keys, RAW:<escapes>, PASTE:<text>, the driver-side forms SLEEP
<seconds>, RESIZE <rows> <cols>, WAIT-FOR:<pattern>,
WAIT-FOR-NEXT:<pattern>) -- the machinery driver.run() drives one-shot
and cli's --repl drives interactively, so there is one code path, not
two that drift. Importable for library use:

    session = Session(['bash', '-i']).start()
    session.send_step('echo hi')
    session.send_step('ENTER')
    session.expect(re.compile(b'hi'))
    session.close()

start() opens the pty, spawns the target with it as controlling
terminal, and drains the connect-wait window; send_step() dispatches
one step of the vocabulary; expect() drains until the ANSI-stripped
output matches; resize() changes the pty's size mid-run; drain() waits
plainly; close() reaps the target, closes fds, and writes --capture-out
if one was configured (idempotent, safe on exception paths). See
README.md's Steps section for the vocabulary and driver.py for the
one-shot wrapper this backs.
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

from .ansi import ANSI, Stripper
from .keys import (
    encode_step,
    parse_resize_step,
    parse_wait_for_next_step,
    parse_wait_for_step,
    parse_wait_step,
)


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
    """The target process exited while work remained -- steps un-sent
    or a WAIT-FOR still waiting. A failed repro, raised instead of
    typing into a pty nobody is reading (issue #37's masquerade: the
    pty echoes the steps anyway, so a dead target looked like a quiet
    successful run). `.output` carries the capture up to that point; it
    is diagnostic (usually the target's own dying words), so the CLI
    still prints it. Dying after the LAST step is the normal clean-
    logout shape (`'exit' ENTER` as the final step) and is deliberately
    not an error."""

    def __init__(self, message, output):
        super().__init__(message)
        self.output = output


def _answer_queries(controller, out, replied):
    """Write a canned reply for every query in `out` not yet answered.
    Checked against the accumulated output, not just the latest chunk --
    a query can straddle two reads. `replied[idx]` counts how many
    occurrences of pattern idx in THIS drain window have been replied to;
    every new occurrence gets its own reply, same as a real terminal
    answering a target that re-queries on every prompt redraw."""
    for idx, (query, reply) in enumerate(QUERY_RESPONDERS):
        matches = list(query.finditer(out))
        for match in matches[replied.get(idx, 0):]:
            os.write(controller, reply(match) if callable(reply) else reply)
        replied[idx] = len(matches)


def drain(controller, seconds, chunk=65536, respond_queries=False):
    out = b''
    end = time.time() + seconds
    got_data = False
    # Reply bookkeeping is per-call, not carried across calls: `out` only
    # holds this call's reads, so a count persisted from an earlier
    # drain() indexed a match list it didn't correspond to -- a query
    # arriving in a later window (a line editor re-querying on a prompt
    # redraw after the startup window answered the first batch) sliced
    # against a stale count and went unanswered, which is exactly the
    # #37 fallback this flag exists to prevent. Within one call,
    # `replied` keeps matches found by earlier reads from being answered
    # twice: a query can straddle two reads, and the full-window rescan
    # below sees the earlier occurrences again on every read.
    replied = {}
    while time.time() < end:
        readable, _, _ = select.select([controller], [], [], DRAIN_POLL)
        if not readable:
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
        if respond_queries:
            _answer_queries(controller, out, replied)
    return out


def _exit_status_why(returncode):
    """Human words for a child's exit status: signal name where the
    target was killed, plain status where it exited on its own."""
    if returncode < 0:
        try:
            return (f'killed by signal {-returncode} '
                    f'({signal.Signals(-returncode).name})')
        except ValueError:
            return f'killed by signal {-returncode}'
    return f'exited with status {returncode}'


# Driver-side step forms, tried in order: (parser, performer). A match is
# performed instead of written. A driver-side wait sends nothing -- that's
# the point (issue #44): keep draining, write no bytes. WAIT-FOR-NEXT is
# parsed before plain WAIT-FOR (no prefix overlap, but the ordering
# documents which form wins if one is ever added that shares a prefix).
_DRIVER_STEP_FORMS = (
    (parse_wait_step,
     lambda session, step, seconds: session.drain(seconds)),
    (parse_wait_for_next_step,
     lambda session, step, pattern: session.expect(
         pattern, step=step, only_new=True)),
    (parse_wait_for_step,
     lambda session, step, pattern: session.expect(pattern, step=step)),
    (parse_resize_step,
     lambda session, step, size: session.resize(*size)),
)


class Session:
    """One pty, one target process, one accumulated raw capture, and the
    whole step vocabulary. The two pty fds are named controller/target
    (our end / the spawned program's end) -- the same vocabulary
    README.md and NOTES.md use for the thing being driven.

    The lifecycle is start() -> send_step()/expect()/resize()/drain()
    -> close(). close() is idempotent and safe after a failed start(),
    so callers can put it in a finally unconditionally; that is what
    keeps the no-fd-leak/no-orphan guarantees of driver.run() intact
    for every other driver of this class."""

    def __init__(self, argv, *, step_delay=1.5, settle=1.5, connect_wait=3.0,
                 env=None, verbose=False, answer_queries=False,
                 wait_for_timeout=10.0, rows=24, cols=80, capture_path=None):
        self.argv = argv
        self.step_delay = step_delay
        self.settle = settle
        self.connect_wait = connect_wait
        self.env = env
        self.verbose = verbose
        self.answer_queries = answer_queries
        self.wait_for_timeout = wait_for_timeout
        self.rows = rows
        self.cols = cols
        self.capture_path = capture_path
        self._stripper = Stripper()
        self._out = b''
        self._started = False
        self._closed = False
        self.proc = None
        self.controller = None

    @property
    def output(self):
        """The raw capture so far -- everything the pty produced."""
        return self._out

    def start(self):
        """Open the pty, spawn the target attached to it, and drain the
        connect-wait window (a banner/prompt gets time to show up before
        the first step). Returns self, so `Session(...).start()` reads
        as one expression."""
        # A fresh real terminal hands the target a window size (xterm's
        # 80x24 convention); pty.openpty() leaves the tty at 0x0. Some
        # targets paper over it (interactive bash falls back to its own
        # 80x24 default), but anything that reads the tty directly --
        # stty, ncurses, fish's line editor -- sees zero columns and
        # behaves like no other terminal would (surfaced during the #37
        # chase: fish's cursor math ran on a zero-width line).
        controller, target = pty.openpty()
        fcntl.ioctl(target, termios.TIOCSWINSZ,
                    struct.pack('HHHH', self.rows, self.cols, 0, 0))
        try:
            self.proc = subprocess.Popen(
                self.argv, stdin=target, stdout=target, stderr=target,
                close_fds=True, env=self.env, preexec_fn=_claim_target_tty,
            )
        except (OSError, subprocess.SubprocessError):
            # argv[0] doesn't exist, isn't executable, etc. (OSError), or
            # _claim_target_tty failed between fork and exec
            # (SubprocessError -- Popen re-raises it in the parent) --
            # either way close both ends ourselves (nothing else will)
            # and let the caller decide how to report it; see issue #21.
            os.close(target)
            os.close(controller)
            raise
        os.close(target)
        self.controller = controller
        self._started = True
        self.drain(self.connect_wait)
        return self

    def check_alive(self, narrative):
        """Raise TargetDied (capture attached) if the target has exited;
        `narrative` is the message fragment saying what was in progress,
        in the voice run()'s step loop uses ('after 0 of 4 steps; ...')."""
        if self.proc.poll() is not None:
            raise TargetDied(
                f'target {_exit_status_why(self.proc.returncode)} '
                f'{narrative}',
                self._out,
            )

    def drain(self, seconds):
        """Wait up to `seconds` for output, accumulating it. Stops early
        once output goes quiet for DRAIN_POLL -- see drain()'s module-
        level namesake for the mechanics this wraps."""
        new = drain(self.controller, seconds,
                    respond_queries=self.answer_queries)
        self._out += new
        self._stripper.feed(new)
        self._stripper.advance()
        return self.output

    def stripped(self):
        """The ANSI-stripped capture so far -- always exactly
        ANSI.sub(b'', self.output), maintained incrementally (see
        ansi.Stripper) so expect()'s per-poll match doesn't re-scan the
        whole capture on every tick."""
        return self._stripper.result()

    def send_step(self, step, on_death=None):
        """Dispatch one step of the vocabulary: a driver-side form
        (SLEEP/RESIZE/WAIT-FOR/WAIT-FOR-NEXT) is performed instead of
        written; anything else goes through encode_step and lands in the
        pty as one write, followed by the step-delay drain. Raises
        TargetDied if the target has exited (the #37 guard: a dead
        target's pty still echoes steps, so dying quietly would
        masquerade as a successful run); `on_death` lets a caller with
        more narrative context (run() names the step count and the next
        un-sent step) replace the default fragment. Malformed
        driver-side forms are the CLI's validation problem -- this is
        the send path, not the parse-and-reject path."""
        self.check_alive(on_death or
                         f"before step {step!r} could be sent -- the "
                         f"captured output is the pty echo of your steps, "
                         f"not the target responding")
        if self.verbose:
            print(f'>> {step}', file=sys.stderr)
        for parse, perform in _DRIVER_STEP_FORMS:
            parsed = parse(step)
            if parsed is not None:
                perform(self, step, parsed)
                return
        os.write(self.controller, encode_step(step))
        self.drain(self.step_delay)

    def expect(self, pattern, step=None, timeout=None, only_new=False):
        """Drain until the ANSI-stripped output matches `pattern` (a
        compiled bytes regex), or timeout -> WaitForTimeout. Matching
        the stripped stream is the point (issue #46): matching raw
        bytes is where a line editor redrawing mid-marker puts
        cursor-positioning escapes between the characters of the thing
        being waited for. The stripped view is maintained incrementally
        (ansi.Stripper), but it is exact ANSI.sub semantics, not a tail
        heuristic: an escape sequence can straddle two reads, and a
        boundary that didn't respect that would stop a marker split
        across reads from ever matching. The target dying mid-wait fails
        fast (TargetDied) instead of burning the whole budget: with its
        last fd closed the pattern cannot arrive any more, so waiting
        is guaranteed futile. The pattern is re-checked before the
        liveness test, so a marker in the target's dying output burst
        still matches normally.

        only_new=True is the WAIT-FOR-NEXT form: the accumulator is
        stripped once here at call time to record the boundary length,
        and the pattern searches the stripped stream from that offset --
        output that arrived before (the first prompt, the echo of
        typing) cannot satisfy the wait. Honest caveat: the boundary is
        a length on the stripped stream, and one escape sequence can
        straddle it, so "only what arrived after" is exact to within
        about one escape sequence at the boundary. The timeout message
        reports only the new region too -- that's the part actually
        being watched.

        `step` (the raw step text) names the step in error messages;
        library callers passing a bare pattern get the pattern text
        instead. Returns the accumulated output."""
        label = 'WAIT-FOR-NEXT' if only_new else 'WAIT-FOR'
        what = step if step is not None else \
            pattern.pattern.decode('utf-8', 'replace')
        timeout = self.wait_for_timeout if timeout is None else timeout
        stripped = self.stripped()
        skip = len(stripped) if only_new else 0
        deadline = time.time() + timeout
        while not pattern.search(stripped[skip:]):
            remaining = deadline - time.time()
            if remaining <= 0:
                tail = stripped[skip:][-200:]
                tail = tail.decode('utf-8', 'replace')
                raise WaitForTimeout(
                    f'{label} step {what!r}: no match within {timeout:g}s -- '
                    f'last output seen (ANSI-stripped): {tail!r}'
                )
            self.drain(min(DRAIN_POLL, remaining))
            stripped = self.stripped()
            if self.proc.poll() is not None:
                raise TargetDied(
                    f'target {_exit_status_why(self.proc.returncode)} while '
                    f'{label} step {what!r} was still waiting -- the '
                    f'pattern cannot arrive any more',
                    self._out,
                )
        return self._out

    def resize(self, rows, cols):
        """A driver-side resize sends no bytes: TIOCSWINSZ on the pty,
        and the kernel delivers SIGWINCH to the pty's foreground process
        group itself -- the same delivery a real terminal's resize
        produces. The controller fd is our end of the same tty start()
        sized at spawn."""
        fcntl.ioctl(self.controller, termios.TIOCSWINSZ,
                    struct.pack('HHHH', rows, cols, 0, 0))
        self.drain(self.step_delay)

    def close(self):
        """Reap the target, close the pty, write the capture file if one
        was configured (mode 0600 -- a capture is transcript content,
        NOTES.md's gotcha). Idempotent, and safe to call after a failed
        start() -- the finally-block shape every driver of this class
        should use. The capture is the RAW, unstripped stream:
        --raw/--capture-out's stdout stripping is the CLI's business,
        and a file that promises the raw capture must never second-guess
        it. Written on every exit path through the caller's try (clean
        finish, TargetDied, WaitForTimeout): the partial capture is
        exactly what a failed run's file is for. An unwritable path
        warns instead of raising, so a capture-file problem can't mask
        the run's own exception mid-propagation. Never types anything
        to close the target -- a "graceful quit" keystroke isn't the
        same across every target, so the caller sends its own."""
        if self._closed:
            return
        self._closed = True
        if self._started and self.capture_path is not None:
            self._write_capture()
        if self.proc is not None:
            self._terminate_target()
        if self.controller is not None:
            os.close(self.controller)

    def _write_capture(self):
        try:
            capture_fd = os.open(
                self.capture_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(capture_fd, 'wb') as capture_file:
                # fchmod even when O_CREAT didn't fire: a rerun over
                # an existing wider-mode capture must not leave the
                # old permissions on the new content. 0600 rather
                # than trusting the umask because a capture is
                # transcript content (NOTES.md's gotcha) -- pty
                # echo of typed steps included.
                os.fchmod(capture_file.fileno(), 0o600)
                capture_file.write(self._out)
        except OSError as error:
            print(f'terminal-puppeteer: warning: could not write '
                  f'capture file {self.capture_path!r}: {error}',
                  file=sys.stderr)

    def _terminate_target(self):
        # SIGTERM, then SIGKILL after 5s: the "only ever terminates
        # the target" README promise. Runs on a clean finish same as
        # on a Ctrl+C/exception mid-drain -- without this,
        # interrupting the tool itself orphans the target (issue #8).
        # An interactive shell ignores SIGTERM, so the kill path is
        # the normal one for bash -i targets.
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()  # reaps
