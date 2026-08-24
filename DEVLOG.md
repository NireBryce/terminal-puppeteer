# Dev log

Terse, reverse-chronological record of what changed and why. For anyone
(human or a Claude Code instance) picking this project back up without
re-reading the whole git log. `NOTES.md` is what using the tool is like;
this is what happened to the project itself.

**Conventions:** newest entry on top. One entry per logical change, not
per commit. Reference commit hashes / issue / PR numbers instead of
restating their content — look them up if you need detail. Keep entries
to a few bullets; if it needs paragraphs it probably belongs in NOTES.md
or a commit message instead.

---

## 2026-08-24 — Fixed #4: a race, not just a flaky raw-flag test

- Both `test_ansi_stripped_by_default` and `test_raw_flag_preserves_ansi`
  had the same bug, not just the one #4 was filed against: a bare
  `printf ...` target exits (closing its end of the pty) right around
  when the trailing `x` step lands, so the step is intermittently dropped
  regardless of `--raw`. 15 back-to-back full-suite runs and 10 more
  targeted at just these two, all clean, after changing the target
  command to `bash -c "printf ...; cat"` — `cat` keeps the process alive
  to reliably receive and echo the step once `printf`'s output has
  already flushed. Closes #4.

## 2026-08-24 — Dropped master/slave for controller/target

- `pty.openpty()`'s own return values are named `master`/`slave`
  (matching the POSIX API and Python's own docs), but that's not load-
  bearing outside `run()`/`drain()` — renamed the local variables to
  `controller`/`target`, the terms `README.md`/`NOTES.md` already used
  everywhere for the same two sides. No public interface changed.
- Incidentally reconfirmed issue #4 is a genuine race rather than a
  deterministic failure while re-running the suite after the rename: 3
  back-to-back runs of `test_raw_flag_preserves_ansi` gave fail/fail/pass.

## 2026-08-24 — CLAUDE.md and agent-facing skills, ported from nixos-configs

- Added `CLAUDE.md` and four skills (`ship`, `propose-issue`,
  `investigate-bug`, `new-skill`), adapted from
  `NireBryce/nixos-configs`'s own copies rather than pasted verbatim: the
  commit-trailer rule (no model name — this repo had already drifted on
  it, 4 of the first 8 commits), the never-file-outside-this-repo rule,
  the GitHub `owner/repo#number` autolink note, and the two-confirmation
  `ship` flow, all re-grounded in this repo's actual state (no branch
  ruleset, no `wiki/`/`lessons-learned.md` — `DEVLOG.md`/`NOTES.md` play
  that role here, and this repo has been issue-native from the start
  rather than growing into it).
- Found incidentally while verifying the `ship` skill's own "is it green"
  step against the real test suite: `test_raw_flag_preserves_ansi` fails
  consistently. Filed as #4, not fixed here.

## 2026-08-24 — Test suite + drain() early-exit fix

- Added `tests/` (stdlib `unittest`, no pytest — matches the project's
  own "no dependencies" stance): unit tests for `encode_step`,
  `load_steps`, the `ANSI` regex; CLI integration tests that run the
  actual script against real pty targets (`cat`/`printf`/`bash`) and
  check stdout byte-for-byte.
- Found while writing those tests: README claimed all three waits
  (`--connect-wait`/`--step-delay`/`--settle`) "stop early once output
  goes quiet for 200ms" — `drain()` never actually did that, always
  burning its full budget regardless of how fast the target went quiet.
  Filed as #1, fixed in the same PR: `drain()` now breaks once 200ms
  passes with no new data, but only after it's seen *some* data first —
  a target producing nothing at all still gets the full wait.
- `.vscode/` added to `.gitignore`.
- commits: `279536d` (tests), `4401dad` (fix), `d078d0c` (gitignore) —
  merged via #2, closes #1.

## 2026-08-24 — Extracted from nixos-configs, documented

- Pulled out of `NireBryce/nixos-configs`
  (`flake/scripts/ssh-pty-drive.py`), generalized beyond SSH-only, and
  renamed `pty-drive` → `terminal-puppeteer` (self-evident without
  knowing what a pty is). Built there to chase a real ble.sh/carapace
  completion bug — see that repo's `claude cave/lessons-learned.md` #39
  and its issue #72.
- README and `NOTES.md` written from that real usage: NOTES.md carries
  the gotchas (unanswered terminal queries, a byte that looks empty but
  isn't, forgetting the `ENTER` step, bulk-paste vs. typing, chasing a
  bug that can catch your own diagnostics, tuning delays before trusting
  a negative result).
- commits: `bb18d3d`, `62dd760`.
