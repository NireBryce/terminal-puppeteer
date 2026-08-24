"""pty/subprocess mechanics: draining output, spawning and driving the
target process. See terminal_puppeteer.cli for the CLI surface that
calls these, and README.md's Timing/Exit behavior sections for what
this is actually implementing and why.
"""
import os
import pty
import select
import subprocess
import sys
import time

from .keys import encode_step


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
