# TODO

Working list. Done items move to the bottom section (with the PR that
landed them) and eventually fall off into DEVLOG.md, which is the
authoritative record. Anything needing hardware or sandbox exceptions
lists its blocker instead of sitting half-done.

## Open

(nothing -- the last item, `--ssh` against a genuinely different host,
closed 2026-09-08: verified live against `nire-tenacity` over its LAN
path and pinned as the opt-in `SecondHostSshTests`, which run wherever
`TERMINAL_PUPPETEER_SSH_HOST` points at a machine with key auth.)

## Done

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
