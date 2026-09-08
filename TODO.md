# TODO

Working list. Done items move to the bottom section (with the PR that
landed them) and eventually fall off into DEVLOG.md, which is the
authoritative record. Anything needing hardware or sandbox exceptions
lists its blocker instead of sitting half-done.

## Open

(nothing -- the three-PR plan from PLAN.md is fully landed as of
2026-09-08; PLAN.md has retired, per its own lifecycle. Ideas that were
considered and consciously deferred live in Backlog, below.)

## Backlog (considered, not now)

Inherited from PLAN.md when it retired. Not committed to; recorded so
the reasoning isn't lost.

- `--json` events log (steps sent, replies answered, timings) for
  machine-readable run reports.
- Mouse-event injection (SGR encoding) for TUI repros.
- Per-keystroke type delay (human-speed typing vs fast input changes
  line-editor behavior).
- More QUERY_RESPONDERS entries as targets demand them.

## Done

- [x] **#58: death-race tests made deterministic** — 2026-09-08. Root
  cause: bash's STARTUP (and even the gap between its last two
  statements) can stretch past any fixed check window under load;
  queued input still echoes (kernel-side), so captures look normal
  while poll() rightly says alive. Fix is in the test shapes — death
  TRIGGERED by a step, dying words waited for with `WAIT-FOR:`, the
  catcher being the liveness-loop WAIT-FOR (or an explicit
  `proc.poll()` wait at Session level) — not in the driver's guard:
  the post-loop check sketched on the issue would have broken the
  clean-logout shape. Details in DEVLOG 2026-09-08.

- [x] **`--screen` layout renderer** — 0.5.0 (2026-09-08). The final
  capture rendered as a terminal grid (sized by `--rows`/`--cols`):
  the layout-only C0/CSI subset (cursor positioning, erase, ICH/DCH,
  IL/DL, scrolling + DECSTBM regions, deferred autowrap), everything
  else parse-and-discarded, truncated sequences rendering nothing.
  Replaces the byte stream on stdout; composes with `--capture-out`
  (raw bytes still land in the file); refuses `--repl`. The zsh
  `eecho hi` artifact and ble.sh's probe garbage render legibly —
  the acceptance cases. Details in DEVLOG 2026-09-08.

- [x] **Session class + `--repl` interactive mode** — 0.4.0
  (2026-09-08). `Session` (session.py: start/send_step/expect/resize/
  close) owns the pty mechanics and the whole per-step dispatch;
  `driver.run()` is a thin loop over it — one code path, no drift, all
  old import paths kept. `--repl` reads the same vocabulary from
  stdin a line at a time and answers each with the new output plus a
  `<< DONE` sentinel (`<< DEAD <why>` + exit 1 on a failed step) —
  one response per step, always. Details in DEVLOG 2026-09-08.

- [x] **Step-vocabulary batch** — 0.3.7 (2026-09-08). `PASTE:<text>`
  (real bracketed-paste framing, one write), `RESIZE <rows> <cols>`
  (TIOCSWINSZ; the kernel SIGWINCHes the foreground group),
  `WAIT-FOR-NEXT:<pattern>` (matches only output arriving after the
  step starts), `--capture-out FILE` (raw capture, written on failure
  paths too), and the lowercase `sleep`/`wait` advisory. Details in
  DEVLOG 2026-09-08. Running this batch's suite also surfaced #58
  (pre-existing flaky #37-pin test, ambient-load race — separate
  thread).

- [x] **`--ssh` against a genuinely different host** — 0.3.6 follow-up.
  `nire-tenacity.local` (a different physical machine, its own sshd,
  reached over LAN/mDNS — the tailscale name pointed at the same box
  but filtered port 22): round-trip, 24x80 window propagation, job
  control, CTRL-C → status 130, `exit 3` shape, `--ssh-arg`
  passthrough, all through the tool. Pinned as `SecondHostSshTests`,
  gated on `TERMINAL_PUPPETEER_SSH_HOST` (CI has no second host; an
  unconfigured run must not probe anyone's machines).

- [x] **Verify the `--ssh` path over a real ssh transport** — 0.3.6.
  Loopback to the local sshd: round-trip, 24x80 propagation, job
  control, CTRL-C, exit shapes, `--ssh-arg` passthrough (which also
  surfaced that the README's `-o` example never parsed). Pinned as
  `SshTransportTests`, run in CI against the runner's own sshd.
  Remaining: a genuinely different host (see Open).
- [x] **ansi: strip ST-terminated OSC sequences** — 0.3.5. `ESC ] payload
  ESC \` (fish's OSC 133 prompt marks, our own OSC 11 reply, OSC 8
  hyperlinks) used to survive "stripped" output; only the BEL
  terminator was handled. Seen directly in this project's own repro
  captures.
- [x] **CI: packaging job asserts the man page ships** — wheel contains
  `share/man/man1/terminal-puppeteer.1`, it lands in the venv's
  `share/man/man1/`, and `man --warnings -l` accepts the installed
  copy. Tripwire for the deprecated `data-files` mechanism.
- [x] **CI: version-sync guard** — test compares pyproject's
  `[project].version` against the package's `__version__` (was
  comment-enforced only).
- [x] **CI: Python 3.14 added to the test matrix** — matches the author
  machine and the nix build.
- [x] **Test: `python -m terminal_puppeteer` entry point pinned.**
- [x] **Ship the batch** — 0.3.5 + DEVLOG + this file committed.
