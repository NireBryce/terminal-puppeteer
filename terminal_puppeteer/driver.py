"""The one-shot runner: run() drives a Session (see session.py -- the
pty/subprocess mechanics, the query responders, and the whole per-step
dispatch live there) through a flat list of steps and returns the raw
capture. This module is the step-list narrative on top -- the
idx-of-N bookkeeping, the between-steps death check's message -- plus
re-exports of the Session-core names the CLI and existing callers
import from here, so driver.run()/drain()/TargetDied/WaitForTimeout
keep working unchanged. See terminal_puppeteer.cli for the CLI surface
that calls these, and README.md's Timing/Exit behavior sections for
what this is actually implementing and why.
"""
from .session import (  # noqa: F401 -- re-exported for callers/tests
    DRAIN_POLL,
    QUERY_RESPONDERS,
    Session,
    TargetDied,
    WaitForTimeout,
    _claim_target_tty,
    _exit_status_why,
    drain,
)


def run(argv, steps, step_delay, settle, connect_wait, env=None, verbose=False,
        answer_queries=False, wait_for_timeout=10.0, rows=24, cols=80,
        capture_path=None):
    """Spawn `argv` under a pty, send `steps` in order (the vocabulary
    session.py dispatches), drain `settle` seconds at the end, and
    return the raw capture. Fails loudly -- TargetDied, WaitForTimeout,
    OSError -- rather than typing into a pty nobody is reading; the
    target is always reaped and the pty always closed (Session.close in
    the finally), on every exit path."""
    session = Session(
        argv, step_delay=step_delay, settle=settle, connect_wait=connect_wait,
        env=env, verbose=verbose, answer_queries=answer_queries,
        wait_for_timeout=wait_for_timeout, rows=rows, cols=cols,
        capture_path=capture_path,
    )
    try:
        session.start()
        for step_idx, step in enumerate(steps):
            # The between-steps death check lives in send_step; what
            # run() adds is the step-list narrative: how many steps
            # were never sent and which one would have been next. The
            # capture is attached because it is the diagnostic --
            # typically the target's dying words, or (per #37) the pty
            # echo of steps typed at a corpse, which is why the message
            # spells out that distinction.
            session.send_step(step, on_death=(
                f'after {step_idx} of {len(steps)} steps; '
                f'{len(steps) - step_idx} not sent, next would be {step!r} -- '
                f'the captured output is the pty echo of your steps, '
                f'not the target responding'))
        session.drain(settle)
    finally:
        session.close()
    return session.output
