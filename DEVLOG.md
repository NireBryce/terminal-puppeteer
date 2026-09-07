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

## 2026-09-07 — 0.3.1: fish works — controlling terminal + full query answering (#37)

- Picking up #37's "verify on a normal machine" ask, on a real NixOS
  desktop this time (fish 4.8.1 via `nix shell`, no sandbox): the
  original sandbox attribution was **wrong**. Through the tool, fish
  reproduced exactly — and `waitpid` showed why it looked so weird:
  fish **exits rc=1 right after its two startup warnings**, and the
  "echoed but never executed" commands were the pty's kernel
  line-discipline echo over a dead target. The tool still exited 0.
- **Root cause, first layer**: `run()` spawned the target onto the pty
  without `setsid()`, so the pty was never the target's controlling
  terminal. Every target lost job control; bash/zsh masked it with a
  warning and kept going, fish treated it as fatal. Fixed with
  `preexec_fn` doing `setsid()` + `TIOCSCTTY` (subprocess has fds 0/1/2
  already dup'd to the pty by preexec time). bash/zsh lose their
  warnings and gain real job control (CTRL-C is a genuine SIGINT to
  the foreground job now — `$-` gains its `m`); fish comes alive.
- **Root cause, second layer**: with the controlling terminal, fish
  then hung before its first prompt on its startup query storm
  (kitty-keyboard, OSC 11, DA1, DSR, DCS termcap — beyond what #43's
  responder covered). Widened `QUERY_RESPONDERS` to that set (replies
  verified live against fish), and DA1's pattern now also matches the
  `\e[0c` form fish sends.
- **And a third finding the fish chase forced**: answering each query
  once per run wasn't enough — fish re-sends its queries on every
  prompt redraw, times out on an unanswered repeat, redraws, and
  *flushes its input buffer*, eating keystrokes typed into that window
  (this is why every "should work now" test kept coming back SILENT
  with no echo at all: the editor was live, the bytes were being
  flushed). Replying to every occurrence, same as a real terminal,
  settles the redraws. `drain()`'s answered-set became an occurrence
  counter per pattern; callable replies (termcap echoes the queried
  capname) replace the old fixed-bytes-only shape.
- Usage note that fell out: `connect-wait`/`SLEEP` drain with
  early-exit-on-quiet, so against a slow-initializing target like fish
  a bare delay lands **mid-init**, where typed-ahead keystrokes get
  swallowed. Pacing on the target's own output (`WAIT-FOR` on a prompt
  marker, then a settle beat) is the reliable shape — now the
  documented recipe for fish, and what the new fish tests do.
- Also fixed in passing, surfaced by the new bash -i tests: `run()`'s
  kill path never reaped the child (`proc.kill()` then no `wait()`),
  ResourceWarning + unreaped process whenever the target ignored
  SIGTERM — which is every interactive shell. Now waited.
- Tests 105 → 115: per-occurrence reply counting, the fish query-set
  coverage, controlling-terminal/session assertions, bash -i job
  control (`himBH`), CTRL-C interrupting `sleep 30` (`done-130`), and
  two fish tests (executes with `--answer-queries`; fails loudly
  without it) that skip where fish isn't installed. Full suite green;
  fish verified through the real CLI too.
- Docs: README fish paragraph rewritten from "couldn't be verified" to
  verified-with-caveats; `--answer-queries` sections (README/man) list
  the widened query set and per-occurrence answering; man CAVEATS now
  says job control works and warns that a dead target looks like a
  quiet run; NOTES.md gets the fish story and the check-your-target-
  is-alive gotcha. 0.2.0 → 0.3.1 (pyproject, script version, man
  `.TH`).

---



## 2026-09-07 — 0.3.0: RAW:/SLEEP/WAIT-FOR steps, --answer-queries, BEL note, Nix CI (#43–#47, #36)

- Asked to "implement solutions to github issues"; the tracker had seven
  open. Five were implementable code changes (#43–#47), plus #36's CI
  gap; #37 (fish) asks for verification on a normal non-sandboxed
  machine and stays open untouched. Landed as one batch, one bullet per
  logical piece, same shape as #24's batch entry.
- **#45 — `RAW:<escapes>` steps**: bytes through a `decode_raw()`
  bind/readline-style escape parser (`\e`, `\C-x` for letters plus
  `@[\]^_`, `\xHH`, `\a \b \t \n \v \f \r`, `\\`; unknown escapes stay
  literal), sent as ONE write — an ESC step followed by `'[I'` text is
  two writes, and the gap between them is a window for the target's
  escape-sequence parser to time out and discard the partial sequence
  (ble.sh's decoder does exactly that). Explicit prefix, so plain text
  that merely starts with a backslash never changes meaning.
- **#44 — `SLEEP <seconds>`/`WAIT <seconds>`**: the first driver-side
  steps that wait instead of write (`keys.parse_wait_step` returns
  None / seconds / ValueError; `run()` drains instead of writing).
  Replaces the target-side `'sleep 5' ENTER` workaround, which lands in
  shell history, fires PROMPT_COMMAND/precmd hooks, and isn't available
  to non-shell targets at all.
- **#46 — `WAIT-FOR:<pattern>`** (+ `--wait-for-timeout`, default 10s):
  drains until the ANSI-stripped output accumulated so far matches —
  a literal substring, or a `/regex/` — and fails the run otherwise
  (exit 1, one stderr line naming the step and the last output seen),
  never a slow no-op. Matching the stripped stream is the point: a line
  editor redrawing mid-marker interleaves cursor-positioning escapes
  into the raw bytes of the thing being waited for. Strips the whole
  accumulator per poll rather than a tail, because escape sequences
  straddle reads.
- **#43 — `--answer-queries`**: canned DA2/DA1/cursor-position/
  XTVERSION replies installed in `drain()` (each answered at most once
  per run, matched against accumulated output since queries can
  straddle reads). Opt-in because the replies are canned lies; default
  behavior unchanged. NOTES.md's unanswered-queries gotcha now tells
  the apply-vs-not ble.sh story (verified in issue #43: same session
  with/without the responder, byte-level capture as the signal) and
  points at the flag.
- **#47 — BEL note**: after the run, a stderr line counting `\x07`
  bytes — invisible in both stripped and raw output, but the standard
  unbound-key/alert signal, and the motivating bug *was* a bell.
  Counted on the stripped stream so a BEL terminating an OSC sequence
  (part of the sequence, not a bell) doesn't count. Advisory only, no
  stdout change.
- **#36 — `.github/workflows/nix.yml`**: `nix flake check`, `nix build
  .#default` + a `--version` smoke of the built binary, and
  `nix-build default.nix` + the same smoke. All four commands verified
  locally on a real NixOS machine (all passed, real builds); the
  GitHub Actions run itself hasn't happened yet — watch it on the
  workflow's first push, per the #12 precedent of confirming the run,
  not just the YAML.
- Tests 65 → 105. Writing them surfaced two real pty-rendering
  surprises, both now asserted explicitly: a cooked pty echoes a real
  ESC byte in *input* as the two literal characters `^[` (ECHOCTL), so
  the --answer-queries reply surfaces as `^[[>85;3800;0c` and a
  `RAW:\e[I` echo as `^[[I`; and `cat` in canonical mode never
  re-emits an unterminated write, so RAW:-step byte-exactness is
  proven through `stty -icanon` + `od` instead of cat's echo.
- Version 0.2.0 → 0.3.0 (script, pyproject.toml, man page's `.TH`) —
  a real feature release, same precedent as #24's 0.1.0 → 0.2.0.

## 2026-08-24 — Direct tests for driver.py and print_key_list (#40)

- Asked to "write tests" with no further context; surveyed actual
  coverage against the package's real surface rather than guessing what
  was meant. Found a real gap: `driver.drain`/`driver.run` and
  `keys.print_key_list` were only ever exercised indirectly through
  full CLI subprocess spawns, never imported and called directly, even
  though the package split (#28) made that straightforward.
- `DrainTests`: `drain()` against a real `os.pipe()` fd, not a mock --
  early exit once quiet, full budget wait when nothing arrives, and the
  EOF path (write end closed), a genuinely different exit condition
  than the quiet-timeout one.
- `RunDirectTests`: `run()` called directly, checking `/proc/self/fd`
  counts before/after. Formalizes the #21/#8 fd-cleanup fix, which had
  only ever been verified once, by hand -- confirmed the new test
  actually catches that regression by reverting the fix locally
  (failed, leaked exactly 2 fds) and restoring it (passed).
- `PrintKeyListTests`: the `--list-keys` formatting logic directly.
- 65 tests now (was 57).

## 2026-08-24 — Verified shell-agnostic --cmd: zsh, fish, other shells (#38)

- Filed #36 (CI doesn't verify Nix packaging -- known gap called out in
  CLAUDE.md's own conventions, never turned into a tracked issue until
  asked to). Then worked from a scratch `terminal_puppeteer/todo.md`
  someone added directly in the editor ("zsh support", "fish support",
  "other shells") -- addressed each, removed the file afterward in
  favor of this repo's actual tracking (an issue for the one genuinely
  open question, this DEVLOG entry for the rest) rather than a second
  floating TODO queue alongside it.
- "Other shells" was already true by construction -- nothing
  shell-specific anywhere in this tool. Documented that explicitly
  instead of leaving it implicit.
- zsh verified directly (including with `-f`, no rc files, to rule out
  personal dotfiles). Found a real gotcha along the way: zsh's line
  editor redraws the line on the first keystroke -- reproduces bare,
  no plugins needed -- so a flat capture shows `eecho hi` for a typed
  `echo hi`. Confirmed with `--raw` it's a literal backspace, not
  double-sending. New NOTES.md gotcha, same family as the existing
  ble.sh one, generalized to any live-redraw line editor. Real CLI
  test added (`skipUnless` zsh available).
- fish warned on startup and never executed anything sent to it --
  verified this is NOT a terminal-puppeteer bug by reproducing the
  identical symptom with a bare `pty.openpty()` script, zero
  terminal-puppeteer code involved. bash/zsh tolerate the same missing
  job-control capability in this sandbox and keep working; fish alone
  treats it as a hard blocker. Filed #37 rather than guessing which
  way it resolves on a normal machine.

## 2026-08-24 — Documented the Nix files (#34)

- Asked "is CI no strings attached, or drop it" (answered: keep it,
  free on a public repo, the one real trade-off is now executing
  external PRs' code on GitHub's runners, standard for any open repo),
  then asked to fill in missing Nix documentation. `flake.nix`/
  `package.nix`/`default.nix` had almost no comments -- added them,
  matching this project's usual density elsewhere. `README.md`'s See
  also was missing all three (every other doc file was listed);
  `CONTRIBUTORS.md` didn't mention `nix develop`. Checked first whether
  the man page needed a Nix mention too -- it never documented any
  install method, not even `pip`, so no gap there.
- Verified after adding comments, not assumed harmless: `nix flake
  check`, a real `nix build`, and `nix-build default.nix` all still
  succeed and the resulting binaries still run.

## 2026-08-24 — Nix flake + default.nix compat shim (#32)

- Asked "which is best, pros/cons" (flake vs. plain `default.nix`)
  first; laid out the tradeoff (flake matches this tool's own history
  and most current Nix users' actual workflow, `default.nix` needs no
  experimental features) and recommended both -- flake as the real
  definition, a `flake-compat` shim alongside for non-flake users, a
  few extra lines instead of picking one audience over the other.
  Confirmed, implemented.
- `flake.nix`: `packages`/`apps`/`devShells`, four standard systems.
  `package.nix`: `buildPythonApplication` against the existing
  `pyproject.toml` unchanged -- `[project.scripts]` and the man page's
  `data-files` entry both just work through Nix's normal wheel build,
  nothing extra needed. Version read from `pyproject.toml` directly
  rather than duplicated a third time.
- Verified for real: `nix build`, `nix run -- --version`, `nix
  develop`, and `nix-build default.nix` (the compat path) all actually
  run, including the real test suite inside the sandboxed build (one
  test skips cleanly -- no `pgrep` in the sandbox, exactly what its
  existing `skipUnless` guard is for) and the built binary + installed
  man page run afterward. `nix flake check` clean.

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
