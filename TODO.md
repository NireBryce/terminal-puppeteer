# TODO

Working list. Done items move to the bottom section (with the PR that
landed them) and eventually fall off into DEVLOG.md, which is the
authoritative record. Anything needing hardware or sandbox exceptions
lists its blocker instead of sitting half-done.

## Open

- [ ] **`--ssh` against a genuinely different host** — the transport
  itself is verified now (real `ssh -tt` loopback to the local sshd:
  round-trip, 24x80 window propagation, job control, CTRL-C, exit
  shapes; pinned as `SshTransportTests`, run in CI against its own
  sshd). What loopback can't cover: a different machine's sshd config,
  OS, and network path. Blocker: `ts-tenacity` has keys set up but its
  sshd wasn't answering when tried (2026-09-07) — start sshd there (or
  name another host) and this runs.

## Done

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
