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
    echo 'echo hi' ENTER | terminal-puppeteer.py --cmd 'bash -i' --repl
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
unless --raw) followed by a '<< DONE' sentinel line; a failed step
(target death, WAIT-FOR timeout) prints its fresh output and
'<< DEAD <why>' instead. Exactly one response per step line, always,
and the exit code says which way the session ended (0 for :quit/EOF,
1 for DEAD) -- so a driving agent can send a line, read the response,
and decide the next line without ever waiting on a sentinel that won't
come. The Session class behind it (terminal_puppeteer.session: start/
send_step/expect/resize/close) is importable for the same style of use
from Python.

Viewing the output: --screen renders the final capture as an actual
terminal grid (--rows/--cols sized) instead of a byte stream. The flat
transcript interleaves a live line editor's redraws with its text --
backspaces left as literal characters, cursor moves invisible -- and a
grid shows what a terminal was actually displaying: zsh's 'eecho hi'
artifact renders as 'echo hi', ble.sh's cursor-glyph probe renders as
plain bracketed text. It is a layout-only ANSI interpreter (cursor
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
from .screen import render
from .session import Session


def load_steps(args):
    steps = list(args.steps)
    if args.steps_file:
        f = sys.stdin if args.steps_file == '-' else open(args.steps_file)
        try:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    steps.append(line)
        finally:
            if f is not sys.stdin:
                f.close()
    return steps


def validate_step(p, step):
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
        except ValueError as e:
            p.error(f'{what} {step!r}: {e}')
    warn_if_near_miss(step)


def maybe_note_bels(p, args, output):
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
            f'{p.prog}: note: {bels} BEL (0x07) byte'
            f'{"s" if bels != 1 else ""} received from the target',
            file=sys.stderr,
        )


def drive_repl(p, args, argv, env):
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

    def emit(delta):
        text = delta if args.raw else ANSI.sub(b'', delta)
        if text:
            out.write(text)
            if not text.endswith(b'\n'):
                out.write(b'\n')

    try:
        try:
            session.start()
        except OSError as e:
            p.exit(1, f'{p.prog}: error: could not start {argv[0]!r}: '
                      f'{e.strerror or e}\n')
        # The startup capture -- whatever the connect-wait window caught,
        # usually the banner/prompt -- reports before the first step is
        # read, so a driver sees where it's typing into.
        emit(session.output)
        out.write(b'<< DONE\n')
        out.flush()
        for line in sys.stdin:
            step = line.strip()
            if not step or step.startswith('#'):
                continue
            if step == ':quit':
                break
            validate_step(p, step)
            mark = len(session.output)
            try:
                session.send_step(step)
            except (TargetDied, WaitForTimeout) as e:
                emit(session.output[mark:])
                out.write(f'<< DEAD {e}\n'.encode())
                out.flush()
                return 1
            emit(session.output[mark:])
            out.write(b'<< DONE\n')
            out.flush()
        session.drain(args.settle)
        return 0
    finally:
        session.close()
        maybe_note_bels(p, args, session.output)


def main():
    # __doc__'s first paragraph is this exact sentence -- drop it from the
    # epilog so --help doesn't print it twice (once as the description
    # right under usage, once again as the epilog's own opening line).
    p = argparse.ArgumentParser(
        description='Let an LLM agent drive a real interactive program through a pty.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('\n\n', 1)[1],
    )
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    p.add_argument('--list-keys', action='store_true', help='print the table of named keys (TAB, CTRL-C, UP, ...) and exit')
    # Not required at the group level: --list-keys needs neither --ssh nor
    # --cmd. "at least one, unless --list-keys" is enforced by hand below;
    # "at most one" (the actual mutual-exclusion part) still comes free
    # from the group regardless of required=True/False.
    target = p.add_mutually_exclusive_group()
    target.add_argument('--ssh', metavar='HOST', help='shorthand for --cmd "ssh -tt HOST"')
    target.add_argument('--cmd', metavar='COMMAND', help='shell-quoted command to run under a pty, e.g. \'bash -i\' or \'tmux attach -t work\'')
    p.add_argument('steps', nargs='*', help='literal text or a named key (TAB, ENTER, CTRL-C, ...) -- see --list-keys')
    p.add_argument('--steps-file', help="read additional steps, one per line, from this file ('-' for stdin)")
    p.add_argument('--repl', action='store_true', help="interactive: read steps from stdin one line at a time ('#' comments, ':quit' or EOF to finish), printing each step's new output then a '<< DONE' sentinel line ('<< DEAD <why>' if the step fails) -- no positional steps or --steps-file")
    p.add_argument('--step-delay', type=float, default=1.5, help='seconds to wait for output after each step (default: 1.5)')
    p.add_argument('--settle', type=float, default=1.5, help='extra seconds to wait after the last step (default: 1.5)')
    p.add_argument('--connect-wait', type=float, default=3.0, help='seconds to wait for a banner/prompt before the first step (default: 3.0)')
    p.add_argument('--raw', action='store_true', help='print output with ANSI escape sequences intact instead of stripped')
    p.add_argument('--screen', action='store_true', help="render the final capture as a terminal grid (--rows/--cols sized) instead of a byte stream -- a layout-only ANSI interpreter (cursor movement, erase, scrolling), not a terminal emulator; byte-level forensics still needs --raw/--capture-out")
    p.add_argument('--capture-out', metavar='FILE', help="also write the raw, unstripped pty capture to this file -- stdout output unchanged, and the file is written even when the run fails mid-way (the partial capture is the diagnostic)")
    p.add_argument('--answer-queries', action='store_true', help="answer terminal capability queries (\\e[>c, \\e[6n, ...) with canned 'normal terminal' replies -- a target that probes-then-fallbacks can behave differently with no reply, so repros silently diverge from a real terminal (default: off, queries go unanswered)")
    p.add_argument('--wait-for-timeout', type=float, default=10.0, metavar='SECONDS', help='how long a WAIT-FOR step may wait for its pattern before failing the run (default: 10)')
    p.add_argument('--rows', type=int, default=24, metavar='ROWS', help='terminal rows reported to the target through the pty (default: 24)')
    p.add_argument('--cols', type=int, default=80, metavar='COLS', help='terminal columns reported to the target through the pty (default: 80)')
    p.add_argument('--ssh-arg', dest='ssh_args', action='append', default=[], help='extra arg to pass through to ssh, only with --ssh (repeatable)')
    p.add_argument('--env', dest='env_vars', action='append', default=[], metavar='KEY=VALUE', help="extra environment variable for the target process (repeatable) -- e.g. --env TERM=dumb, set before the target starts rather than raced by an early step")
    p.add_argument('-v', '--verbose', action='store_true', help='print each step to stderr as it is sent')
    args = p.parse_args()

    if args.list_keys:
        print_key_list()
        return

    if not args.ssh and not args.cmd:
        p.error('one of the arguments --ssh --cmd is required')

    if args.rows < 1 or args.cols < 1:
        # A 0x0 tty is exactly the bug the defaults exist to fix -- allow
        # overriding the size, not un-setting it.
        p.error('--rows/--cols must be at least 1')

    if args.cmd and args.ssh_args:
        p.error('--ssh-arg only applies to --ssh')

    if args.ssh:
        argv = ['ssh', '-tt', *args.ssh_args, args.ssh]
    else:
        try:
            argv = shlex.split(args.cmd)
        except ValueError as e:
            p.error(f'--cmd: {e}')

    env = None
    if args.env_vars:
        env = dict(os.environ)
        for kv in args.env_vars:
            if '=' not in kv:
                p.error(f'--env: expected KEY=VALUE, got {kv!r}')
            key, _, value = kv.partition('=')
            env[key] = value

    if args.repl:
        # The REPL's input IS stdin -- steps would collide with it, so
        # both other step sources are refused rather than silently
        # concatenated or raced. --screen renders a FINAL capture; a
        # live session has no final capture to render.
        if args.steps:
            p.error('--repl reads steps from stdin as it goes; pass no positional steps')
        if args.steps_file:
            p.error('--repl reads steps from stdin as it goes; --steps-file does not apply')
        if args.screen:
            p.error('--screen applies to the final capture; it does not combine with --repl')
        sys.exit(drive_repl(p, args, argv, env))

    try:
        steps = load_steps(args)
    except OSError as e:
        p.error(f'--steps-file: {e.strerror} ({e.filename})')
    if not steps:
        p.error('no steps given (positionally or via --steps-file)')
    for step in steps:
        validate_step(p, step)

    try:
        out = run(
            argv, steps, args.step_delay, args.settle, args.connect_wait,
            env=env, verbose=args.verbose, answer_queries=args.answer_queries,
            wait_for_timeout=args.wait_for_timeout,
            rows=args.rows, cols=args.cols,
            capture_path=args.capture_out,
        )
    except OSError as e:
        # argv[0] not found, not executable, etc. -- a real runtime failure,
        # not a usage mistake, so no usage block (p.error()'s always-exit-2
        # style doesn't fit here): just the message, exit 1. See issue #21.
        p.exit(1, f'{p.prog}: error: could not start {argv[0]!r}: {e.strerror or e}\n')
    except WaitForTimeout as e:
        # Same runtime-failure shape: a pattern that never arrived is a
        # failed repro, not a usage mistake -- exit 1, one-line message
        # saying what was waited for and what was last seen (issue #46),
        # no usage block.
        p.exit(1, f'{p.prog}: error: {e}\n')
    except TargetDied as e:
        # The target died with steps un-sent: a failed repro, same
        # runtime-failure shape, exit 1, no usage block (issue #37).
        # The partial capture still goes to stdout first -- it is the
        # diagnostic (typically the target's own dying words), stripped
        # the same as a successful run's, or gridded the same when
        # --screen is on.
        if args.screen:
            captured = render(e.output, rows=args.rows,
                              cols=args.cols).encode('utf-8')
        else:
            captured = e.output if args.raw else ANSI.sub(b'', e.output)
        sys.stdout.buffer.write(captured)
        sys.stdout.buffer.write(b'\n')
        p.exit(1, f'{p.prog}: error: {e}\n')
    maybe_note_bels(p, args, out)
    if args.screen:
        # --screen REPLACES the byte stream with the rendered grid: the
        # capture was the run's raw record either way (--capture-out
        # still gets the raw bytes), and this is the alternative VIEW
        # of it -- what the terminal was actually showing. Computed on
        # the raw capture; the grid contains no ANSI by construction,
        # so --raw has nothing to change here.
        rendered = render(out, rows=args.rows, cols=args.cols)
        sys.stdout.buffer.write(rendered.encode('utf-8'))
        sys.stdout.buffer.write(b'\n')
        return
    if not args.raw:
        out = ANSI.sub(b'', out)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.write(b'\n')


if __name__ == '__main__':
    main()
