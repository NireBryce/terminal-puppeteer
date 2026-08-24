#!/usr/bin/env python3
"""Let an LLM agent drive a real interactive program through a pty.

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
                     several partial commands. Real bracketed-paste support
                     is more machinery than this needs -- keep multi-line
                     payloads to a `source /path/to/file` step instead of
                     typing the file's contents.

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

Each positional STEP is sent to the pty literally, except for a closed set
of named keys (case-insensitive): TAB, ENTER (or RET/CR/NEWLINE), CTRL-C,
CTRL-D, CTRL-R, ESC, BACKSPACE. `--steps-file` reads one step per line
instead (blank lines skipped, `#`-prefixed lines are comments) -- useful
once a repro grows past what's comfortable as shell arguments.

No step ever gets an implicit trailing newline -- unlike a real line
editor, this does not know a "command" is finished until an ENTER step
says so. That is deliberate: it is what lets a step be a bare TAB or a
partial line. On exit the target process is just terminated (SIGTERM, then
SIGKILL if it doesn't go) -- nothing is typed to close it, since a
"graceful quit" keystroke isn't the same across every target; include an
explicit ENTER/CTRL-D/'exit' step of your own if a clean logout matters
for a given repro.
"""
import argparse
import os
import pty
import re
import select
import shlex
import subprocess
import sys
import time

SPECIAL_KEYS = {
    'TAB': b'\t',
    'ENTER': b'\r',
    'RET': b'\r',
    'CR': b'\r',
    'NEWLINE': b'\n',
    'CTRL-C': b'\x03',
    'CTRL-D': b'\x04',
    'CTRL-R': b'\x12',
    'ESC': b'\x1b',
    'BACKSPACE': b'\x7f',
}

ANSI = re.compile(rb'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|[()][0-9A-Za-z]|[=>78McD])')


def encode_step(step):
    return SPECIAL_KEYS.get(step.upper(), step.encode())


def load_steps(args):
    steps = list(args.steps)
    if args.steps_file:
        with open(args.steps_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    steps.append(line)
    return steps


def drain(controller, seconds, chunk=65536):
    out = b''
    end = time.time() + seconds
    got_data = False
    while time.time() < end:
        r, _, _ = select.select([controller], [], [], 0.2)
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
    return out


def run(argv, steps, step_delay, settle, connect_wait):
    # pty.openpty() calls these master/slave (matching the underlying POSIX
    # API and Python's own pty module docs) -- renamed here to the terms
    # this project already uses everywhere else: `controller` is our end,
    # `target` is the spawned program's end, same as README.md/NOTES.md's
    # own vocabulary for "the thing being driven".
    controller, target = pty.openpty()
    proc = subprocess.Popen(
        argv, stdin=target, stdout=target, stderr=target, close_fds=True,
    )
    os.close(target)
    try:
        out = drain(controller, connect_wait)
        for step in steps:
            os.write(controller, encode_step(step))
            out += drain(controller, step_delay)
        out += drain(controller, settle)
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


def main():
    p = argparse.ArgumentParser(
        description='Let an LLM agent drive a real interactive program through a pty.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--ssh', metavar='HOST', help='shorthand for --cmd "ssh -tt HOST"')
    target.add_argument('--cmd', metavar='COMMAND', help='shell-quoted command to run under a pty, e.g. \'bash -i\' or \'tmux attach -t work\'')
    p.add_argument('steps', nargs='*', help='literal text or a named key (TAB, ENTER, CTRL-C, ...)')
    p.add_argument('--steps-file', help='read additional steps, one per line, from this file')
    p.add_argument('--step-delay', type=float, default=1.5, help='seconds to wait for output after each step (default: 1.5)')
    p.add_argument('--settle', type=float, default=1.5, help='extra seconds to wait after the last step (default: 1.5)')
    p.add_argument('--connect-wait', type=float, default=3.0, help='seconds to wait for a banner/prompt before the first step (default: 3.0)')
    p.add_argument('--raw', action='store_true', help='print output with ANSI escape sequences intact instead of stripped')
    p.add_argument('--ssh-arg', dest='ssh_args', action='append', default=[], help='extra arg to pass through to ssh, only with --ssh (repeatable)')
    args = p.parse_args()

    if args.cmd and args.ssh_args:
        p.error('--ssh-arg only applies to --ssh')

    if args.ssh:
        argv = ['ssh', '-tt', *args.ssh_args, args.ssh]
    else:
        try:
            argv = shlex.split(args.cmd)
        except ValueError as e:
            p.error(f'--cmd: {e}')

    try:
        steps = load_steps(args)
    except OSError as e:
        p.error(f'--steps-file: {e.strerror} ({e.filename})')
    if not steps:
        p.error('no steps given (positionally or via --steps-file)')

    out = run(argv, steps, args.step_delay, args.settle, args.connect_wait)
    if not args.raw:
        out = ANSI.sub(b'', out)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.write(b'\n')


if __name__ == '__main__':
    main()
