r"""Let an LLM agent drive a real interactive program through a pty.

For an agent debugging a terminal program: you have no real terminal of
your own, only tool calls that run a command and get back its output once
it exits. That gap is invisible for most commands and is exactly where
some bugs live. This opens a real pty, spawns the target attached to it,
and sends it actual keystrokes with delays between them -- so you can
reproduce a bug that only exists in a live, human-typed session, then read
back what happened, without a human needing to sit at a keyboard for you.

Three specific ways a plain tool call differs from a person typing:

  no pty            Without a pty, a remote shell's `$-` never contains `i`
                     (nothing gated on `[[ $- == *i* ]]` runs), and plenty
                     of local programs detect a non-tty stdin/stdout and
                     change behavior outright. `ssh -tt` and most local
                     interactive programs need a real pty allocated, not a
                     pipe -- `--ssh`/`--cmd` here both go through one.

  one shot           `ssh host 'cmd'` or `subprocess.run([...])` sends one
                     command (or one blob of input) and reads one reply.
                     Bugs that depend on what is already in flight -- a
                     live auto-suggestion mid-keystroke, a background job,
                     a key landing while another is still being processed
                     -- need actual keystrokes sent with the process still
                     running, not a full line handed over at once.

  bulk paste != typing
                     Writing a whole multi-line string to the pty at once
                     is NOT the same as a human typing it: without
                     bracketed-paste framing, each embedded newline submits
                     whatever line precedes it right then, so a multi-line
                     function definition sent as one write arrives as
                     several partial commands. The PASTE: step form below
                     supplies real bracketed-paste framing; a `source
                     /path/to/file` step avoids typing the contents
                     entirely.

Originally built by an agent (Claude Code) to reproduce a ble.sh/bash
completion bug that only ever showed up while actually typing at a real
terminal, over SSH -- `--ssh` is still the shorthand for that. `--cmd`
drives anything else the same way: a local shell, `tmux attach`, a REPL,
any program that behaves differently attached to a real terminal than
piped.

    terminal-puppeteer.py --ssh HOST 'git co' TAB CTRL-C
    terminal-puppeteer.py --ssh HOST 'export TERM=xterm-256color' ENTER 'echo hi' ENTER
    terminal-puppeteer.py --cmd 'bash -i' 'echo $-' ENTER
    terminal-puppeteer.py --cmd 'tmux attach -t work' 'echo hi' ENTER
    terminal-puppeteer.py --ssh HOST --settle 5 'sleep 3' ENTER
    terminal-puppeteer.py --ssh HOST --steps-file repro.txt
    printf 'echo hi\nENTER\n' | terminal-puppeteer.py --cmd 'bash -i' --repl
    terminal-puppeteer.py --ssh HOST --env TERM=dumb 'echo hi' ENTER
    terminal-puppeteer.py --list-keys

Each positional STEP is sent to the pty literally, except for a closed set
of named keys (case-insensitive): TAB, ENTER (or RET/CR/NEWLINE), ESC,
BACKSPACE, the arrow/navigation keys (UP, DOWN, LEFT, RIGHT, HOME, END,
DELETE, PGUP, PGDN), and CTRL-<letter> for any letter A-Z, not just a
hardcoded few (CTRL-C, CTRL-U, CTRL-L, ...) -- `--list-keys` prints the
full table with exact bytes. A step that's close to a key name but
doesn't match (a missing hyphen, a typo) still gets sent as literal text,
same as always, but also gets a heads-up on stderr in case that wasn't
intended. `--steps-file` reads one step per line instead (blank lines
skipped, `#`-prefixed lines are comments, `-` reads from stdin) -- useful
once a repro grows past what's comfortable as shell arguments.

The other step forms cover what the key vocabulary can't express:

  RAW:<escapes>      bytes through a bind/readline-style escape parser --
                     'RAW:\e[I' sends the focus-in sequence \e[I as ONE
                     write. An ESC step followed by '[I' text is two
                     writes, and the gap between them gives the target's
                     escape-sequence parser a window to time out and
                     discard the partial sequence. Understands \e, \C-x,
                     \xHH, \a \b \t \n \v \f \r, \\; anything else stays
                     literal.

  PASTE:<text>       the text wrapped in real bracketed-paste framing
                     (\e[200~ ... \e[201~), sent as one write, with the
                     same bind-style escapes RAW: understands. The way to
                     express a multi-line paste in one step: a line editor
                     with bracketed-paste mode enabled treats the whole
                     payload as one paste and holds its newlines literal,
                     instead of executing each line as it arrives.

  SLEEP <seconds>    a driver-side pause: sends nothing, just keeps
                     draining for N seconds. Unlike a target-side
                     'sleep 5' ENTER, it puts nothing in the session
                     under test -- no history entry, no PROMPT_COMMAND/
                     precmd hooks, no transcript noise, and it works for
                     non-shell targets.

  RESIZE <rows> <cols>
                     resize the pty mid-run: the kernel delivers SIGWINCH
                     to the target's foreground process group, the same
                     delivery a real terminal's resize produces.
                     --rows/--cols fix the size the pty starts with;
                     this changes it once the session is live.

  WAIT-FOR:<pattern> drain until the ANSI-stripped output accumulated so
                     far matches -- a literal substring, or a regex when
                     wrapped in /.../. Paces on the target's actual
                     output instead of a duration somebody measured once
                     ('WAIT-FOR:/===.?.MARK.?.===/' survives a line
                     editor redrawing mid-marker; the literal form does
                     not). Fails the run with exit 1 and the last output
                     seen on stderr if nothing matches within
                     --wait-for-timeout.

  WAIT-FOR-NEXT:<pattern>
                     the same matching, but only against output arriving
                     after the step starts -- the echo of your own typing
                     and a prompt that already contained the marker
                     cannot satisfy it (WAIT-FOR's transcript includes
                     both). Same /.../ regex form, same timeout.

No step ever gets an implicit trailing newline -- unlike a real line
editor, this does not know a "command" is finished until an ENTER step
says so. That is deliberate: it is what lets a step be a bare TAB or a
partial line. On exit the target process is just terminated (SIGTERM, then
SIGKILL if it doesn't go) -- nothing is typed to close it, since a
"graceful quit" keystroke isn't the same across every target; include an
explicit ENTER/CTRL-D/'exit' step of your own if a clean logout matters
for a given repro. If the target exits on its own while steps remain --
a crash, an unwelcome startup, a typo'd target -- the run fails loudly
(exit 1, partial capture still printed, one stderr line naming the
status and the steps never sent) instead of typing into a pty nobody is
reading.

Interactive sessions: --repl inverts the one-shot model. It starts the
target, reports whatever the connect-wait window caught (usually the
banner/prompt), and then reads the same step vocabulary from stdin one
line at a time -- '#' lines are comments, ':quit' or EOF ends the
session (a final settle drain, then the target is terminated). After
each step it prints just the NEW output since the last one (stripped
unless --raw, capped like any stdout below) followed by a '<< DONE'
sentinel line; a failed step (target death, WAIT-FOR timeout) prints
its fresh output and '<< DEAD <why>' instead. Exactly one response per
step line, always, and the exit code says which way the session ended
(0 for :quit/EOF, 1 for DEAD) -- so a driving agent can send a line,
read the response, and decide the next line without ever waiting on a
sentinel that won't come. With --screen the response is not the byte
delta but the live grid: a persistent rows-x-cols screen fed the whole
session, so a chatty full-screen target costs the same bounded render
every step. The Session class behind it (terminal_puppeteer.session:
start/send_step/expect/resize/close) is importable for the same style
of use from Python.

Viewing the output: stdout is capped by default -- --max-output bytes
(default 30000) of the rendered view, then one loud marker line naming
the cut size and the ways out. An agent's transcript should hold what
it chose to look at, not whatever volume the target produced -- the
same rule every agent-facing tool converges on: complete artifact,
bounded presentation, loud pointer between them. The complete artifact
is --capture-out FILE (never capped, written on every exit path);
--full-output is the opt-out that puts everything in context, one-shot
stdout and every --repl response alike.

--show FILE reads a capture file back instead of driving a target:
the same views as a run (ANSI-stripped by default, --raw, --screen of
the whole capture), plus line selection over the viewed bytes --
--offset N (1-based first line), --limit N, --tail N (standalone).
Line numbers are counted on what --show emits, so they match a grep
over the same view. The intended loop for a big capture: keep it with
--capture-out, grep/od the file, then page just the interesting ranges
through --show into context.

--screen renders the capture as an actual terminal grid
(--rows/--cols sized) instead of a byte stream. The flat transcript
interleaves a live line editor's redraws with its text -- backspaces
left as literal characters, cursor moves invisible -- and a grid shows
what a terminal was actually displaying: zsh's 'eecho hi' artifact
renders as 'echo hi', ble.sh's cursor-glyph probe renders as plain
bracketed text. It is a layout-only ANSI interpreter (cursor
positioning, erase, insert/delete, scrolling, scroll regions), not a
terminal emulator: colors and modes are parsed-and-discarded, truncated
sequences render nothing (a real terminal is still waiting for their
tail), and byte-level questions -- an embedded SOH, an exact value --
still need --raw/--capture-out and od.

One advisory note lands on stderr after a run: if any BEL (\x07) bytes
came back from the target -- invisible in both stripped and raw output,
but the standard "unbound key"/alert signal a whole class of bugs
expresses itself as -- the count is printed, so a bell is legible in a
capture instead of silent.
"""
import argparse
import os
import shlex
import signal
import sys

from ._version import __version__
from .ansi import ANSI
from .driver import TargetDied, WaitForTimeout, run
from .keys import (
    parse_paste_step,
    parse_resize_step,
    parse_wait_for_next_step,
    parse_wait_for_step,
    parse_wait_step,
    print_key_list,
    warn_if_near_miss,
)
from .screen import Screen, render, utf8_safe_len
from .session import Session


def load_steps(args):
    steps = list(args.steps)
    if args.steps_file:
        # UTF-8 regardless of locale: steps are keystrokes, and a file
        # an agent wrote is UTF-8 -- decoding it in the machine's
        # locale would silently send mojibake under anything else.
        steps_source = sys.stdin if args.steps_file == '-' \
            else open(args.steps_file, encoding='utf-8')
        try:
            for line in steps_source:
                line = line.strip()
                if line and not line.startswith('#'):
                    steps.append(line)
        finally:
            if steps_source is not sys.stdin:
                steps_source.close()
    return steps


def validate_step(parser, step):
    # Malformed driver-side forms are usage errors, not literal text:
    # typing 'SLEEP abc' or 'RESIZE abc 80' into the target would be a
    # silent footgun. Well-formed ones pass through untouched (the
    # driver re-parses them, same pattern as encode_step). Shared by the
    # one-shot validation loop and --repl's per-line validation -- the
    # same vocabulary must be rejected the same way in both, or the
    # REPL would accept steps the one-shot run rejects.
    for parse, what in ((parse_wait_step, 'wait step'),
                        (parse_paste_step, 'PASTE step'),
                        (parse_resize_step, 'RESIZE step'),
                        (parse_wait_for_next_step, 'WAIT-FOR-NEXT step'),
                        (parse_wait_for_step, 'WAIT-FOR step')):
        try:
            parse(step)
        except ValueError as error:
            parser.error(f'{what} {step!r}: {error}')
    warn_if_near_miss(step)


def maybe_note_bels(parser, args, output):
    # Counted on the stream being printed, on purpose: a \x07
    # terminating an OSC sequence (\x1b]0;title\x07) is part of that
    # sequence, not a bell -- the standalone BELs this counts are the
    # ones that survive stripping untouched. BEL is not part of an ANSI
    # escape sequence, so stripping leaves it -- and it renders as
    # nothing, --raw included. Advisory only (same shape as the
    # near-miss warning): programs bell for reasons a given repro may
    # not care about, but a bell is the visible symptom of a whole
    # class of unbound-key/alert behavior, and without this line a
    # capture of a bell-heavy session looks identical to a bell-free
    # one. See issue #47.
    printed = output if args.raw else ANSI.sub(b'', output)
    bels = printed.count(b'\x07')
    if bels:
        print(
            f'{parser.prog}: note: {bels} BEL (0x07) byte'
            f'{"s" if bels != 1 else ""} received from the target',
            file=sys.stderr,
        )


def capped(text, args, what, pointer):
    """The stdout cap: past --max-output bytes, cut the rendered view
    and append one loud marker naming the cut and the ways out. The
    capture (--capture-out) is never capped -- the tool's contract is
    that the record doesn't lie, so the cap bounds only what enters an
    agent's transcript, and the marker always says where the rest is.
    Loud, never silent: a transcript that just stopped early would be
    indistinguishable from a quiet target."""
    if args.full_output or len(text) <= args.max_output:
        return text
    marker = (f'\n[[ terminal-puppeteer: {what} truncated at '
              f'{args.max_output} bytes of {len(text)} -- {pointer} ]]\n')
    return text[:args.max_output] + marker.encode()


def select_lines(data, offset, limit, tail):
    """Line selection for --show, over the bytes as viewed (stripped
    unless --raw, so the numbers match a grep over the same view).
    Lines split on \\n; a trailing final newline terminates the last
    line rather than adding an empty one, matching how editors and the
    Read-style tools agents already use count. --tail stands alone;
    --offset is 1-based; --limit caps the run of lines from --offset."""
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    if tail is not None:
        return b'\n'.join(lines[-tail:])
    start = (offset - 1) if offset is not None else 0
    lines = lines[start:]
    if limit is not None:
        lines = lines[:limit]
    return b'\n'.join(lines)


def write_output(data):
    """The one stdout exit: the view, plus the trailing newline every
    run's stdout has always ended with (unconditional — a view that
    already ends in a newline gets the blank-line terminator too; that
    wart is the byte-for-byte contract the tests and consumers pin).
    A capped view ends in its marker's newline, then this one."""
    out = sys.stdout.buffer
    out.write(data)
    out.write(b'\n')


CAPTURE_POINTER = ('add --capture-out FILE to keep the full capture on disk, '
                   'or --full-output to print everything')


def render_view(data, args):
    """A run's capture as shown on stdout: the --screen grid, or the byte
    stream (ANSI-stripped unless --raw)."""
    if args.screen:
        # --screen REPLACES the byte stream with the rendered grid: the
        # capture was the run's raw record either way (--capture-out
        # still gets the raw bytes), and this is the alternative VIEW
        # of it -- what the terminal was actually showing. Computed on
        # the raw capture; the grid contains no ANSI by construction,
        # so --raw has nothing to change here.
        return render(data, rows=args.rows, cols=args.cols).encode('utf-8')
    return data if args.raw else ANSI.sub(b'', data)


def write_run_view(data, args, what=None):
    """Print a run's capture through the view and the stdout cap."""
    if what is None:
        what = 'rendered screen' if args.screen else 'output'
    write_output(capped(render_view(data, args), args, what, CAPTURE_POINTER))


def show_capture(parser, args):
    """--show: read a capture file back through the same views a run
    gets -- stripped bytes, --raw, or a --screen grid -- with line
    selection over the viewed bytes and the same stdout cap. Reads
    only; no target, no steps, no pty."""
    try:
        with open(args.show, 'rb') as capture_file:
            data = capture_file.read()
    except OSError as error:
        parser.exit(1, f'{parser.prog}: error: could not read '
                       f'{args.show!r}: {error.strerror or error}\n')
    if args.screen:
        text = render(data, rows=args.rows, cols=args.cols)
        shown = text.encode('utf-8')
        pointer = (f'{args.show!r} holds the whole capture '
                   f'(--full-output lifts the cap)')
        what = 'rendered screen'
    else:
        shown = select_lines(data if args.raw else ANSI.sub(b'', data),
                             args.offset, args.limit, args.tail)
        pointer = (f'{args.show!r} holds all of it '
                   f'(--offset/--limit/--tail select, '
                   f'--full-output lifts the cap)')
        what = 'output'
    write_output(capped(shown, args, what, pointer))
    return 0


class ScreenFeeder:
    """The live grid for --screen + --repl: one persistent rows-x-cols
    screen fed the whole session, rendered after every step -- the
    bounded view of a chatty full-screen target, the same shape --repl's
    byte deltas have for line-oriented ones. Feeds hold back anything
    that could still be completed by later bytes: escape sequences (bytes
    from the last ESC on -- the renderer consumes a truncated sequence as
    gone-for-good) and partial multi-byte characters (utf8_safe_len),
    either of which split across feeds would render wrong. The settle
    drain at :quit is never emitted (byte mode discards it too), so no
    end-of-session flush of the holdback exists -- deliberate."""

    def __init__(self, rows, cols):
        self.screen = Screen(rows, cols)
        self._holdback = bytearray()

    def feed(self, delta):
        """Feed new capture bytes; returns the current grid as bytes."""
        self._holdback.extend(delta)
        cut = self._holdback.rfind(b'\x1b')
        if cut == -1:
            cut = len(self._holdback)
        # Hold back an incomplete multi-byte character too, the
        # same way the ESC hold above holds a truncated sequence:
        # a character split across feeds would otherwise decode to
        # replacement glyphs.
        safe = utf8_safe_len(bytes(self._holdback[:cut]))
        self.screen.feed(bytes(self._holdback[:safe]).decode('utf-8',
                                                             'replace'))
        del self._holdback[:safe]
        return self.screen.text().encode('utf-8')


def drive_repl(parser, args, argv, env):
    """The --repl loop: the same step vocabulary as a one-shot run, read
    line-by-line from stdin so an agent can pace itself on the target's
    actual responses -- send a line, read what came back, decide the
    next line. '#' lines are comments, ':quit' or EOF ends the session
    (a final --settle drain, then close). After each step it prints just
    the NEW output since the last one (stripped unless --raw), then a
    '<< DONE' sentinel; a failed step (target death, WAIT-FOR timeout)
    prints its fresh output and '<< DEAD <why>' instead, and exits 1 --
    exactly one response per step line, so a consumer never waits on a
    sentinel that won't come. Returns the exit code; the Session in the
    finally is what keeps the target reaped and fds closed on every
    path out of here."""
    session = Session(
        argv, step_delay=args.step_delay, settle=args.settle,
        connect_wait=args.connect_wait, env=env, verbose=args.verbose,
        answer_queries=args.answer_queries,
        wait_for_timeout=args.wait_for_timeout,
        rows=args.rows, cols=args.cols, capture_path=args.capture_out,
    )
    out = sys.stdout.buffer
    screen_feeder = ScreenFeeder(args.rows, args.cols) if args.screen else None

    def emit(delta, what='response'):
        if screen_feeder is not None:
            text = screen_feeder.feed(delta)
        else:
            text = delta if args.raw else ANSI.sub(b'', delta)
        text = capped(
            text, args, what,
            'add --capture-out FILE to the session for the full capture, '
            'or --full-output to see everything')
        if text:
            out.write(text)
            if not text.endswith(b'\n'):
                out.write(b'\n')

    try:
        try:
            session.start()
        except OSError as error:
            parser.exit(1, f'{parser.prog}: error: could not start '
                           f'{argv[0]!r}: {error.strerror or error}\n')
        # The startup capture -- whatever the connect-wait window caught,
        # usually the banner/prompt -- reports before the first step is
        # read, so a driver sees where it's typing into.
        emit(session.output, what='startup capture')
        out.write(b'<< DONE\n')
        out.flush()
        for line in sys.stdin:
            step = line.strip()
            if not step or step.startswith('#'):
                continue
            if step == ':quit':
                break
            validate_step(parser, step)
            mark = len(session.output)
            try:
                session.send_step(step)
            except (TargetDied, WaitForTimeout) as error:
                emit(session.output[mark:])
                out.write(f'<< DEAD {error}\n'.encode())
                out.flush()
                return 1
            emit(session.output[mark:])
            out.write(b'<< DONE\n')
            out.flush()
        session.drain(args.settle)
        return 0
    finally:
        session.close()
        maybe_note_bels(parser, args, session.output)


def _install_signal_cleanup():
    """SIGTERM/SIGHUP get the cleanup Ctrl-C already gets. Ctrl-C raises
    KeyboardInterrupt, which unwinds through every try/finally on the
    way out -- run()'s and drive_repl's -- reaping the target and
    writing --capture-out; the default disposition of SIGTERM/SIGHUP
    kills the process without any of that, so an agent harness killing
    a timed-out tool would lose the capture file and orphan the target.
    Converting the signal to SystemExit sends it down the same unwind;
    128+N is the died-by-signal exit-status convention."""
    def handler(signum, frame):
        raise SystemExit(128 + signum)

    for name in ('SIGTERM', 'SIGHUP'):
        num = getattr(signal, name, None)
        if num is None:
            continue
        try:
            signal.signal(num, handler)
        except (ValueError, OSError):
            pass  # not the main thread (ValueError): run unhandled


def build_parser():
    # __doc__'s first paragraph is this exact sentence -- drop it from the
    # epilog so --help doesn't print it twice (once as the description
    # right under usage, once again as the epilog's own opening line).
    parser = argparse.ArgumentParser(
        description='Let an LLM agent drive a real interactive program through a pty.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('\n\n', 1)[1],
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--list-keys', action='store_true', help='print the table of named keys (TAB, CTRL-C, UP, ...) and exit')
    # Not required at the group level: --list-keys needs neither --ssh nor
    # --cmd. "at least one, unless --list-keys" is enforced by hand below;
    # "at most one" (the actual mutual-exclusion part) still comes free
    # from the group regardless of required=True/False.
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--ssh', metavar='HOST', help='shorthand for --cmd "ssh -tt HOST"')
    target.add_argument('--cmd', metavar='COMMAND', help='shell-quoted command to run under a pty, e.g. \'bash -i\' or \'tmux attach -t work\'')
    target.add_argument('--show', metavar='FILE', help="readback: emit a capture file written by --capture-out instead of driving a target -- same views as a run (ANSI-stripped by default, --raw, --screen), selectable by line with --offset/--limit/--tail")
    parser.add_argument('steps', nargs='*', help='literal text or a named key (TAB, ENTER, CTRL-C, ...) -- see --list-keys')
    parser.add_argument('--steps-file', help="read additional steps, one per line, from this file ('-' for stdin)")
    parser.add_argument('--repl', action='store_true', help="interactive: read steps from stdin one line at a time ('#' comments, ':quit' or EOF to finish), printing each step's new output then a '<< DONE' sentinel line ('<< DEAD <why>' if the step fails); with --screen the response is the live grid; no positional steps or --steps-file")
    parser.add_argument('--max-output', type=int, default=30000, metavar='BYTES', help="cap on stdout (a run's view, each --repl response, --show) -- past it one loud marker names the cut and the way out; --capture-out is never capped (default: 30000)")
    parser.add_argument('--full-output', action='store_true', help="lift the output cap: everything in context, uncapped -- one-shot stdout and every --repl response; the complete-on-disk alternative is --capture-out")
    parser.add_argument('--offset', type=int, default=None, metavar='N', help='with --show: emit from 1-based line N (lines counted on the viewed bytes)')
    parser.add_argument('--limit', type=int, default=None, metavar='N', help='with --show: emit at most N lines from --offset')
    parser.add_argument('--tail', type=int, default=None, metavar='N', help='with --show: emit only the last N lines (standalone)')
    parser.add_argument('--step-delay', type=float, default=1.5, help='seconds to wait for output after each step (default: 1.5)')
    parser.add_argument('--settle', type=float, default=1.5, help='extra seconds to wait after the last step (default: 1.5)')
    parser.add_argument('--connect-wait', type=float, default=3.0, help='seconds to wait for a banner/prompt before the first step (default: 3.0)')
    parser.add_argument('--raw', action='store_true', help='print output with ANSI escape sequences intact instead of stripped')
    parser.add_argument('--screen', action='store_true', help="render the capture as a terminal grid (--rows/--cols sized) instead of a byte stream -- of a run's final capture, of each --repl step (the live grid), or of a whole --show file; a layout-only ANSI interpreter (cursor movement, erase, scrolling), not a terminal emulator; byte-level forensics still needs --raw/--capture-out")
    parser.add_argument('--capture-out', metavar='FILE', help="also write the raw, unstripped pty capture to this file, created 0600 (a capture is transcript content: NOTES.md's gotcha) -- stdout output unchanged, and the file is written even when the run fails mid-way (the partial capture is the diagnostic)")
    parser.add_argument('--answer-queries', action='store_true', help="answer terminal capability queries (\\e[>c, \\e[6n, ...) with canned 'normal terminal' replies -- a target that probes-then-fallbacks can behave differently with no reply, so repros silently diverge from a real terminal (default: off, queries go unanswered)")
    parser.add_argument('--wait-for-timeout', type=float, default=10.0, metavar='SECONDS', help='how long a WAIT-FOR step may wait for its pattern before failing the run (default: 10)')
    parser.add_argument('--rows', type=int, default=24, metavar='ROWS', help='terminal rows reported to the target through the pty (default: 24)')
    parser.add_argument('--cols', type=int, default=80, metavar='COLS', help='terminal columns reported to the target through the pty (default: 80)')
    parser.add_argument('--ssh-arg', dest='ssh_args', action='append', default=[], help='extra arg to pass through to ssh, only with --ssh (repeatable)')
    parser.add_argument('--env', dest='env_vars', action='append', default=[], metavar='KEY=VALUE', help="extra environment variable for the target process (repeatable) -- e.g. --env TERM=dumb, set before the target starts rather than raced by an early step")
    parser.add_argument('-v', '--verbose', action='store_true', help='print each step to stderr as it is sent')
    return parser


def validate_args(parser, args):
    """Usage-error checks that apply to every mode (run, --repl, --show)."""
    if not args.ssh and not args.cmd and not args.show:
        parser.error('one of the arguments --ssh --cmd --show is required')

    if args.rows < 1 or args.cols < 1:
        # A 0x0 tty is exactly the bug the defaults exist to fix -- allow
        # overriding the size, not un-setting it.
        parser.error('--rows/--cols must be at least 1')

    if args.max_output < 1:
        parser.error('--max-output must be at least 1')

    if not args.show and (args.offset is not None or args.limit is not None
                          or args.tail is not None):
        # Reached only on the run/repl paths (the --show block exits
        # first), so these would be silently ignored there.
        parser.error('--offset/--limit/--tail only apply to --show')

    if args.cmd and args.ssh_args:
        parser.error('--ssh-arg only applies to --ssh')

    if args.show and args.ssh_args:
        parser.error('--ssh-arg only applies to --ssh')

    if args.screen and args.rows * args.cols > 4_000_000:
        # A grid is rows*cols cells of Python strings; at the winsize
        # ceiling (65535x65535) that is a 4-billion-cell grind, not
        # even a clean MemoryError. This bounds the RENDERER only --
        # the pty itself is fine at any winsize -- and 4M cells is
        # already far past any readable screen.
        parser.error(f'--screen: {args.rows}x{args.cols} is '
                     f'{args.rows * args.cols} renderer cells -- keep '
                     f'rows*cols at 4000000 or less')


def validate_show_args(parser, args):
    """--show drives nothing, so everything with run semantics is refused
    the way --ssh-arg is: usage error, not silent ignore."""
    if args.repl:
        parser.error('--show reads a capture file; --repl drives a target')
    if args.steps or args.steps_file:
        parser.error('--show reads an existing capture; steps drive a target')
    if args.env_vars:
        parser.error('--env applies to a run, not to --show')
    if args.answer_queries:
        parser.error('--answer-queries applies to a run, not to --show')
    if args.verbose:
        parser.error('--verbose applies to a run, not to --show')
    for flag, value, default in (('--step-delay', args.step_delay, 1.5),
                                 ('--settle', args.settle, 1.5),
                                 ('--connect-wait', args.connect_wait, 3.0),
                                 ('--wait-for-timeout',
                                  args.wait_for_timeout, 10.0)):
        # A flag left at its default is indistinguishable from
        # unset and changes nothing; only an explicit value is a
        # misdirected attempt to pace a readback.
        if value != default:
            parser.error(f'{flag} applies to a run, not to --show')
    if args.capture_out:
        parser.error('--capture-out writes a capture from a run; --show only reads one')
    if args.screen and (args.offset is not None or args.limit is not None
                        or args.tail is not None):
        parser.error('--offset/--limit/--tail select lines of the byte '
                     'stream; --screen renders a grid of the whole capture '
                     '-- not combinable')
    for name, value in (('offset', args.offset), ('limit', args.limit),
                        ('tail', args.tail)):
        if value is not None and value < 1:
            parser.error(f'--{name} must be at least 1')
    if args.tail is not None and (args.offset is not None
                                  or args.limit is not None):
        parser.error("--tail stands alone -- it can't combine with --offset/--limit")


def build_target_argv(parser, args):
    """The target's argv: `ssh -tt ...` for --ssh, shlex-split for --cmd."""
    if args.ssh:
        if args.ssh.startswith('-'):
            # The host is one argv element, so this is option parsing,
            # not injection -- but '--ssh -oProxyCommand=...' would land
            # in ssh's option parser all the same. No real destination
            # starts with '-'; real option passthrough is --ssh-arg.
            parser.error(f"--ssh: HOST {args.ssh!r} starts with '-' -- ssh "
                         f"would parse it as an option, not a destination; "
                         f"pass ssh options with --ssh-arg")
        return ['ssh', '-tt', *args.ssh_args, args.ssh]
    try:
        argv = shlex.split(args.cmd)
    except ValueError as error:
        parser.error(f'--cmd: {error}')
    if not argv:
        # Whitespace or empty: shlex hands back no argv at all, and
        # Popen's failure would be a bare IndexError traceback.
        parser.error('--cmd: empty command')
    return argv


def build_env(parser, args):
    """The target's environment: None (inherit) unless --env was given."""
    if not args.env_vars:
        return None
    env = dict(os.environ)
    for assignment in args.env_vars:
        if '=' not in assignment:
            parser.error(f'--env: expected KEY=VALUE, got {assignment!r}')
        key, _, value = assignment.partition('=')
        env[key] = value
    return env


def validate_repl_args(parser, args):
    """The REPL's input IS stdin -- steps would collide with it, so both
    other step sources are refused rather than silently concatenated or
    raced. --screen COMBINES with --repl: the response becomes the live
    grid (see drive_repl)."""
    if args.steps:
        parser.error('--repl reads steps from stdin as it goes; pass no positional steps')
    if args.steps_file:
        parser.error('--repl reads steps from stdin as it goes; --steps-file does not apply')


def run_oneshot(parser, args, argv, env):
    """The one-shot path: load and validate steps, run them, print the
    view. Runtime failures exit 1 with no usage block."""
    try:
        steps = load_steps(args)
    except OSError as error:
        parser.error(f'--steps-file: {error.strerror} ({error.filename})')
    if not steps:
        parser.error('no steps given (positionally or via --steps-file)')
    for step in steps:
        validate_step(parser, step)

    try:
        out = run(
            argv, steps, args.step_delay, args.settle, args.connect_wait,
            env=env, verbose=args.verbose, answer_queries=args.answer_queries,
            wait_for_timeout=args.wait_for_timeout,
            rows=args.rows, cols=args.cols,
            capture_path=args.capture_out,
        )
    except OSError as error:
        # argv[0] not found, not executable, etc. -- a real runtime failure,
        # not a usage mistake, so no usage block (parser.error()'s always-exit-2
        # style doesn't fit here): just the message, exit 1. See issue #21.
        parser.exit(1, f'{parser.prog}: error: could not start {argv[0]!r}: {error.strerror or error}\n')
    except WaitForTimeout as error:
        # Same runtime-failure shape: a pattern that never arrived is a
        # failed repro, not a usage mistake -- exit 1, one-line message
        # saying what was waited for and what was last seen (issue #46),
        # no usage block.
        parser.exit(1, f'{parser.prog}: error: {error}\n')
    except TargetDied as error:
        # The target died with steps un-sent: a failed repro, same
        # runtime-failure shape, exit 1, no usage block (issue #37).
        # The partial capture still goes to stdout first -- it is the
        # diagnostic (typically the target's own dying words), stripped
        # the same as a successful run's, or gridded the same when
        # --screen is on, capped the same too (the full partial capture
        # is in --capture-out if one was configured).
        write_run_view(error.output, args, 'partial capture')
        parser.exit(1, f'{parser.prog}: error: {error}\n')
    maybe_note_bels(parser, args, out)
    write_run_view(out, args)


def main():
    _install_signal_cleanup()
    parser = build_parser()
    args = parser.parse_args()

    if args.list_keys:
        print_key_list()
        return

    validate_args(parser, args)
    if args.show:
        validate_show_args(parser, args)
        sys.exit(show_capture(parser, args))

    argv = build_target_argv(parser, args)
    env = build_env(parser, args)
    if args.repl:
        validate_repl_args(parser, args)
        sys.exit(drive_repl(parser, args, argv, env))
    run_oneshot(parser, args, argv, env)


if __name__ == '__main__':
    main()
