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
import difflib
import os
import pty
import re
import select
import shlex
import string
import subprocess
import sys
import time

__version__ = '0.2.0'  # keep in sync with pyproject.toml's [project].version

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

ANSI = re.compile(rb'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|[()][0-9A-Za-z]|[=>78McD])')


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


def run(argv, steps, step_delay, settle, connect_wait, env=None, verbose=False):
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
    try:
        out = drain(controller, connect_wait)
        for step in steps:
            if verbose:
                print(f'>> {step}', file=sys.stderr)
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
    p.add_argument('--step-delay', type=float, default=1.5, help='seconds to wait for output after each step (default: 1.5)')
    p.add_argument('--settle', type=float, default=1.5, help='extra seconds to wait after the last step (default: 1.5)')
    p.add_argument('--connect-wait', type=float, default=3.0, help='seconds to wait for a banner/prompt before the first step (default: 3.0)')
    p.add_argument('--raw', action='store_true', help='print output with ANSI escape sequences intact instead of stripped')
    p.add_argument('--ssh-arg', dest='ssh_args', action='append', default=[], help='extra arg to pass through to ssh, only with --ssh (repeatable)')
    p.add_argument('--env', dest='env_vars', action='append', default=[], metavar='KEY=VALUE', help="extra environment variable for the target process (repeatable) -- e.g. --env TERM=dumb, set before the target starts rather than raced by an early step")
    p.add_argument('-v', '--verbose', action='store_true', help='print each step to stderr as it is sent')
    args = p.parse_args()

    if args.list_keys:
        print_key_list()
        return

    if not args.ssh and not args.cmd:
        p.error('one of the arguments --ssh --cmd is required')

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

    try:
        steps = load_steps(args)
    except OSError as e:
        p.error(f'--steps-file: {e.strerror} ({e.filename})')
    if not steps:
        p.error('no steps given (positionally or via --steps-file)')
    for step in steps:
        warn_if_near_miss(step)

    try:
        out = run(
            argv, steps, args.step_delay, args.settle, args.connect_wait,
            env=env, verbose=args.verbose,
        )
    except OSError as e:
        # argv[0] not found, not executable, etc. -- a real runtime failure,
        # not a usage mistake, so no usage block (p.error()'s always-exit-2
        # style doesn't fit here): just the message, exit 1. See issue #21.
        p.exit(1, f'{p.prog}: error: could not start {argv[0]!r}: {e.strerror or e}\n')
    if not args.raw:
        out = ANSI.sub(b'', out)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.write(b'\n')


if __name__ == '__main__':
    main()
