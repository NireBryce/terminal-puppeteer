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

## 2026-08-24 — [project.scripts] replaces script-files (#30)

- Follow-up question after the modularization/naming-collision episode:
  "what would be idiomatic" for the packaging side. `script-files`
  (copying the raw wrapper onto PATH) is the legacy setuptools
  mechanism; `[project.scripts]` is the standard one. Switched --
  `pip install .` now gives a generated `terminal-puppeteer` command
  (no `.py`), not a copy of the source file.
- `terminal-puppeteer.py` at the repo root is unchanged (still the only
  way to run without installing). Installed vs. in-place now
  deliberately have different names, which also means the earlier
  wrapper/package naming-collision bug can't recur through this
  mechanism at all -- a generated entry-point script is a fresh file
  with its own name, nothing to collide with.
- Verified the same way as the collision bug was found: built the
  wheel, installed it into a scratch venv, ran the generated command
  for real (`--version`, `--list-keys`, an actual pty run against
  `cat`), confirmed the man page install path and the in-place wrapper
  both still work.

## 2026-08-24 — Modularized into terminal_puppeteer/ (#28)

- Asked to modularize (the ~325-line single file, up against the "one
  file" design goal stated in README/CLAUDE.md at the time). Flagged
  that tension before doing it; asked again with "docs changes aren't
  as bad this early" and proceeded. Pure move by concern: `keys.py`
  (step vocabulary), `ansi.py`, `driver.py` (pty/subprocess), `cli.py`
  (argparse + `main`, carries the `--help` docstring).
  `terminal-puppeteer.py` at the root becomes a thin wrapper. README/
  CLAUDE.md updated to describe the new layout rather than still
  claiming "one file".
- Tried renaming the wrapper to `terminal_puppeteer.py` to match the
  package (asked for, to "fix/match import issue"), verified it against
  a same-directory case first (safe), shipped it, then caught a real
  bug building and installing the actual wheel into a scratch venv:
  installed alone in `<venv>/bin/`, separate from the package in
  `site-packages`, the identically-named wrapper shadows the real
  package with itself -- `ModuleNotFoundError` on every installed
  invocation. Reverted the rename, kept the rest. Lesson written up in
  `pyproject.toml`'s own comment and a new `CLAUDE.md` convention so it
  doesn't get "fixed" back into the same bug.
- `tests/test_terminal_puppeteer.py`: real `from terminal_puppeteer
  import ansi, cli, keys` now, replacing the `importlib.util`
  load-by-path hack the old hyphenated single file needed. Same 56
  tests, same assertions.
- Verified at every stage (split, rename, revert, final state): full
  suite green each time; man page re-rendered and `--warnings`-checked;
  the wheel actually built and installed into a scratch venv three
  times over the course of this, not just read and assumed correct.

## 2026-08-24 — CONTRIBUTORS.md + STYLEGUIDE.md: LLM agents explicitly invited (#26)

- Asked to write `CONTRIBUTORS.md` inviting issues/PRs from LLM agents
  (not just humans, and not merely tolerating them) as long as
  well-written and human-readable, plus a style guide for what that
  means concretely.
- Split into two files rather than one, on request: `CONTRIBUTORS.md`
  answers "can I contribute and how" (who's welcome, filing an issue,
  opening a PR); `STYLEGUIDE.md` answers "what does well-written mean
  here" (10 rules, grounded in this repo's own demonstrated practice —
  cites #1/#4/#8/#9/#21 as the "verify, don't assume" precedent rather
  than reading as generic AI-PR advice).
- README.md: new Contributing section right after Provenance, both
  files linked from See also.

## 2026-08-24 — 0.2.0: 8 CLI improvements from a review, all landed together (#24)

- Asked for a CLI-improvements review; got 8 suggestions, then asked to
  implement all of them. Landed as one PR since each is small and
  independent: arrow/nav keys (UP/DOWN/LEFT/RIGHT/HOME/END/DELETE/
  PGUP/PGDN — the biggest real gap, verified against actual readline
  history recall + CTRL-U line-kill under `bash --norc -i`);
  `CTRL-<letter>` generalized from 3 hardcoded entries to any A-Z;
  a `difflib`-based stderr warning for a step that's close to a key
  name but doesn't match (advisory only, tuned against common literal
  steps to avoid false positives); `--env KEY=VALUE` (merged onto
  inherited env, fixes the real TERM=dumb race NOTES.md already
  documented — NOTES.md updated to point at it); `--list-keys` (needed
  relaxing the `--ssh`/`--cmd` group to non-required, verified mutual
  exclusion still held); `--steps-file -` for stdin; `--version`;
  `--verbose`/`-v`.
- First version bump: 0.1.0 → 0.2.0 (script, pyproject.toml, man
  page's `.TH` line) — a real feature release, not a patch.
- README, man page, and the module docstring all updated to match; man
  page re-rendered and `--warnings`-checked after every section
  touched. 20 new tests (36 → 56).

## 2026-08-24 — Fixed #21: same #10 bug class, a different call site

- The previous entry's "noticed but not filed" item got filed and fixed
  same session: `subprocess.Popen` in `run()` was unguarded, so a
  `--cmd` (or `ssh` itself) that can't be started raised a raw
  `FileNotFoundError` traceback instead of a clean message. `run()` now
  closes both pty fds itself on that failure and re-raises; `main()`
  reports it via `p.exit(1, ...)` — exit 1, no usage block, since this
  is a runtime failure rather than a usage mistake. Man page's EXIT
  STATUS updated to match (it had documented the traceback as expected
  behavior). #22.

## 2026-08-24 — Man page, and a couple of usage-text/packaging warts along the way (#19)

- Added `man/terminal-puppeteer.1`, content drawn from README.md/
  NOTES.md/the module docstring rather than written fresh. Wired into
  `[tool.setuptools.data-files]` so it installs to `share/man/man1/`.
- Verified by actually rendering it (`man --warnings -l`), not just
  writing troff: found `\(em` renders as a *doubled* em-dash under this
  system's mandoc (switched to the plain `--` the docstring already
  uses) and `.B` on "-h, --help" adds a stray space around the comma
  (needed `.BR`).
- Verifying the wheel surfaced an unrelated setuptools issue: with no
  explicit `packages` list, auto-discovery treated `man/` as an implicit
  package and copied it into the wheel root too, on top of the intended
  data-files copy. `packages = []` fixes it. Verified end-to-end with a
  real install into a scratch venv (script + man page both land where
  expected, installed page renders clean).
- Separately: `--help` printed its own opening sentence twice
  (`description=` and `epilog=__doc__`'s first paragraph were the same
  text) — trimmed, with a regression test.
- Noticed but not filed: `--cmd` naming a genuinely nonexistent command
  exits 1 with a raw `FileNotFoundError` traceback from `Popen` — same
  class of bug as #10, different call site. Documented honestly as
  exit status 1 in the new man page rather than fixed.

## 2026-08-24 — Full-repo review: 5 issues filed, fixed, and closed same session

- Requested review of the whole repo (script, tests, all four docs,
  the four skills) by a session that hadn't touched this repo before.
  Verified everything empirically rather than reading and trusting:
  ran the suite, built the wheel, sent real signals, tried real bad
  input.
- Filed #8–#12, then fixed and closed all five in the same session
  (#13–#17):
  - **#8/#13**: `run()` leaked the target process if the tool itself
    was interrupted (Ctrl+C, or any exception mid-drain) — cleanup only
    ran on a normal return. Reproduced directly with `pgrep` against a
    marked target process before fixing. Wrapped the drain loop in
    try/finally. The regression test needed its own fix along the way:
    interrupting within ~0.2s of the target's own startup lands in a
    flaky window unrelated to the bug (found by scanning interrupt
    delays 0.0s–2.0s against the confirmed-unfixed code); the test now
    lands the interrupt well past that window.
  - **#9/#15**: `pyproject.toml` had no `[build-system]` table and
    nothing telling setuptools to include `terminal-puppeteer.py`
    (hyphenated filename, not a valid module name) — `pip install .`
    silently produced a wheel with zero code. Verified with `uv build`
    + `python3 -m zipfile -l` before and after. Fixed via
    `[tool.setuptools] script-files`, verified by installing the built
    wheel into a scratch venv and actually running the installed
    script.
  - **#10/#14**: a malformed `--cmd` (unbalanced quote) or a missing
    `--steps-file` path crashed with a raw traceback instead of the
    `p.error()` style every other bad input in `main()` gets. Both now
    routed through `p.error()`.
  - **#11/#16**: no `LICENSE` on a public repo. Added MIT (Elly's
    choice, asked directly) plus the matching `pyproject.toml` field.
  - **#12/#17**: nothing ran `tests/` automatically. Added
    `.github/workflows/test.yml` (ubuntu-latest, Python 3.9 + 3.13, no
    install step) — confirmed it actually passes by watching the run
    on its own PR before merging, not just trusting the YAML parses.
- All five landed via the `ship` skill's two-confirmation flow, merged
  as five separate PRs (not squashed) so each fix's own reasoning stays
  attached in `git log`.

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
