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

## 2026-10-26 — 0.6.0: the output cap, `--show`, live-screen `--repl`

The transcript-volume problem, designed after the 0.5.1 audit showed
stdout was the one unbounded leak surface left: a capture poured into
an agent's context is unfilterable after the fact and buries signal
and secrets alike. One rule drives all of it — **complete artifact,
bounded presentation, loud pointer between them** — now written up as
a repo convention in CLAUDE.md.

- **stdout is capped by default** (`--max-output`, 30000): a run's
  view, each `--repl` response, `--show`, the dying-target partial —
  past the cap one loud marker names the cut size and the ways out.
  `--capture-out` is never capped (the "capture doesn't lie" contract
  lives there), and `--full-output` is the deliberate
  everything-in-context opt-out. Sentinel protocol untouched: the
  marker is part of the response, before `<< DONE`.
- **`--show FILE`** reads a capture back instead of driving a target —
  same views as a run, plus `--offset`/`--limit`/`--tail` line
  selection counted on the viewed bytes (stripped unless `--raw`), so
  the numbers match a grep over the same view. The loop for a big
  capture: capture to disk, grep/od the file, `--show` the ranges.
- **`--repl` + `--screen` is now the live grid** (was a usage error):
  one persistent rows-x-cols screen fed the whole session, emitted
  after every step — the bounded view for full-screen targets, the
  same shape byte deltas have for line-oriented ones. Escape
  sequences straddling feed boundaries are held back from the last
  ESC, the screen-parser twin of `ansi.Stripper`'s boundary rule.
- Docs carried along everywhere (README cap/show sections, man
  OPTIONS, NOTES fetch gotcha, Recipes big-capture loop, Gotchas
  bullet, Home quick-shape); stamps re-checked at 0.6.0.
- **A second adversarial pass over the new surface found four more,
  all fixed:** run-only flags (`--verbose`, timing flags, `--ssh-arg`)
  were silently ignored under `--show`, and `--offset/--limit/--tail`
  were silently ignored without it — every one is now the usual
  usage error (a flag left at its default stays accepted, since it's
  indistinguishable from unset); `\C-<non-ASCII letter>` passed
  `str.isalpha()` and `ord()&0x1f`'d into unrelated control bytes
  (`\C-é` sent TAB) — now ASCII-only, unknown keys stay literal;
  `--screen` at a legal winsize (60000x60000) was a 3.6-billion-cell
  renderer grind past any timeout — now bounded at 4M renderer cells
  (the pty itself is unaffected); and `--steps-file` decoded in the
  machine's locale — now always UTF-8, matching what an agent wrote.
- **The two fixable items from the known-limits list got fixed:**
  `ansi.Stripper`'s one unbounded tail (a stray ESC followed by bytes
  that can never start a sequence pinned the boundary, quadratic for
  exactly the bad-paste captures) now advances past it — the byte
  after ESC proves which introducers can start a match, and anything
  else is literal; and the live `--repl --screen` grid holds back an
  incomplete multi-byte character the same way it holds a truncated
  escape (`screen.utf8_safe_len`), so a character split across feeds
  no longer renders as replacement glyphs. `\E` also joins `\e` as an
  escape spelling (`echo -e` parity), README grew a Known limits
  section (the one item left is the stdlib-`re`-has-no-watchdog
  WAIT-FOR caveat, plus the capture memory ceiling), and the CI
  packaging smoke now round-trips `--capture-out` → `--show`.
- **SIGTERM/SIGHUP to the tool itself now run the cleanup** instead of
  bypassing every try/finally: a harness killing a timed-out tool used
  to orphan the target AND skip the `--capture-out` write (Ctrl-C
  already worked — KeyboardInterrupt unwinds). The handlers convert
  the signal to SystemExit down the same unwind; exit status is the
  died-by-signal convention 128+N. Regression test mirrors the #8
  Ctrl-C test (pgrep marker) plus the capture-file assertion.
  `.github/dependabot.yml` keeps the 0.5.1 SHA-pinned Actions fresh.
  Also: `--cmd '   '` was a bare IndexError traceback (shlex returns
  no argv) — now a usage error.

---

## 2026-10-26 — 0.5.1: security/robustness audit fixes (agent audit session)

A pass over the whole repo for security and memory-safety issues, plus
the secrets/transcript question. Two real bugs found and fixed, four
hardening changes, docs brought along:

- **`--answer-queries` missed repeat queries across drain windows** —
  reply bookkeeping persisted across `drain()` calls while indexing
  each call's local output, so a query arriving in a later window (a
  line editor re-querying on a prompt redraw after the startup window
  answered the first batch) sliced against a stale count and went
  unanswered — exactly the #37 fallback the flag exists to prevent.
  Timing-dependent (a query-free echo read reset the count and masked
  it), which made it flaky-repro material. Now counted per window.
  Regression test drives the deterministic shape: SLEEP step between
  two queries (writes no bytes, so no echo read can mask it).
- **`--screen` renderer: unbounded IL/DL loops** — the CSI parameter
  has no length cap, and `for range(p(0,1))` took it literally:
  `ESC[999999999999L` in a capture (the capture is the target's input)
  was ~50 hours of list del/insert. Now clamped to the lines actually
  available at/below the cursor, the real-terminal semantic and the
  same shape scroll_up already had.
- **`--capture-out` is created 0600** (and a pre-existing wider-mode
  file is tightened on rewrite) — a capture is transcript content, the
  NOTES.md gotcha's own logic applied to the file mode.
- **Incremental ANSI stripping for `expect()`** — WAIT-FOR re-stripped
  the whole accumulator every poll, quadratic in capture size.
  `ansi.Stripper` maintains an exact `ANSI.sub`-equivalent view behind
  a nothing-spans-it boundary; `session.stripped()` is pinned to
  one-shot `sub` by tests over a chunking matrix (the first cut of the
  boundary rule was wrong — an OSC terminator's ESC arriving in its
  own feed stranded the sequence header in the cache — which is why
  the tests feed byte-by-byte).
- **`--ssh` rejects a leading-dash HOST** — one argv element starting
  with `-` lands in ssh's option parser (`-oProxyCommand=...`);
  `--ssh-arg` is the documented way to pass options.
- **fd leak on preexec failure** — `start()` now also catches
  `subprocess.SubprocessError` (what a `_claim_target_tty` failure
  surfaces as) and closes both pty fds, same as the OSError path.
- **Secrets docs completed**: the transcript-content gotcha (NOTES,
  README, man, docs wiki) now names the stderr paths that carry step
  content *without* `-v` — failure messages naming un-sent steps
  (verified live), WAIT-FOR timeouts naming pattern+tail, the
  near-miss/sleep advisories. Also new NOTES sections: a WAIT-FOR
  regex is your own CPU budget (no re timeouts; the budget is checked
  between drains), and the capture-accumulates-in-memory ceiling in
  the fetch-out-of-band gotcha. The tool never copies env vars
  anywhere — verified by reading; the man CAVEATS now says so.
- **CI: third-party actions SHA-pinned** (checkout/setup-python/
  install-nix-action were tag-pinned).
- Docs stamps bumped to 0.5.1 (re-verified the pages' claims against
  this pass; the answer-queries claims read truer now than at 0.5.0).

---

## 2026-09-09 — #64: docs fact-check pass (two stale examples fixed); the wiki lands in docs/

- Asked for a docs pass "verifying old facts now that so much has
  changed" plus a wiki. The audit: claims in README/man/NOTES checked
  against the code and run live — defaults, step-table bytes via
  `--list-keys`, flag behaviors, exit statuses, the advisories, every
  runnable example against real targets. Mostly clean (the in-repo
  docs kept pace through 0.5.0); two real stale facts, both examples:
  - The man page's `PASTE:` example used `\een` in troff, which renders
    `\en` — and `decode_raw` turns `\en` into ESC + literal `n`, so the
    example typed a broken function definition into the target
    (verified: bash syntax error, ESC bytes in the paste). Source fixed
    to `\en` (renders `\n`); corrected payload verified to define and
    call the function.
  - The `--repl` quick-start example (`echo 'echo hi' ENTER | …
    --repl`, in README, man EXAMPLES, and cli.py's docstring) pipes one
    line, but `--repl` reads ONE STEP per line — it typed `ENTER`
    literally and never submitted anything (verified: no `hi` in the
    output, the literal echo visible). Fixed to the two-line `printf`
    form in all three places, verified end to end.
- **The wiki: GitHub's is unavailable on this private repo** —
  `has_wiki` PATCH returns 200 but stays false, and the
  `<repo>.wiki.git` repo doesn't exist (push to create fails too;
  likely plan-gated). Elly directed it into `docs/`: Home, Step
  vocabulary, Recipes, Gotchas — every page stamped with the version
  its facts were empirically checked against, linking to README/NOTES
  rather than duplicating where drift is likely. README's See also,
  the man page's SEE ALSO, and this file's Docs section point at it.
- No version bump: docs only.

---

## 2026-09-08 — #58: the death-race tests made deterministic (no version bump)

- **Root cause found, and it wasn't what the issue guessed.** The
  flaky shape (`bash -c "…; exit N"` + a fixed check window) failed
  because bash's STARTUP is the long pole: under load it can take
  ~1s to even reach its first command, and input typed before that
  just sits in the tty queue — the kernel echoes it regardless, so
  the capture looks perfectly normal while poll() rightly says alive.
  Probing further: even the gap between a target's last two
  statements (`echo DYING-WORDS; kill -9 $$`) once stretched past
  half a second. Any single-point-in-time check can lose those races;
  measured ~13% per-trial on an otherwise idle loop.
- **Fix: the tests now pace on the target's actual behavior instead
  of racing fixed windows.** The death is TRIGGERED by a step (an
  ENTER completing a `read`), the dying words are waited for with
  `WAIT-FOR:` (proof bash ran its last statement), and the final
  `WAIT-FOR:tp-never-arrives` is the catcher — its liveness loop
  keeps checking until the target is really dead, within budget.
  Which message template fires varies with timing (fast death → the
  i-of-N pre-check message; slow death → the liveness-loop message),
  so the run()-level tests assert the loud-failure shape (TargetDied,
  status named, dying words in the capture) and NOT the template.
  The Session tests wait out the death explicitly on the process
  handle (`while proc.poll() is None`) and pin the exact i-of-N and
  default-narrative message formatting deterministically.
- **Guard semantics unchanged, deliberately.** The issue's sketch of
  a post-loop death check would break the clean-logout shape
  (`test_target_dying_after_the_last_step_is_not_an_error`): a target
  that dies during the last step's drain is exactly the `exit`+ENTER
  finale, and the driver cannot distinguish "read my last line and
  exited" from "died before reading it". The blind spot after the
  final pre-step check is documented behavior, not an oversight.
- 7 tests reshaped (3 direct driver, 2 Session, 2 CLI); one renamed
  (`test_early_target_death_exits_1_with_capture_and_explanation` →
  `test_target_death_exits_1_with_capture_and_explanation`, since the
  death is no longer pinned to startup). Suite still 224; the
  reshaped set passed 20/20 consecutive loop iterations (the old
  shapes flaked 1/6 to 4/6 under the same load).

---

## 2026-09-08 — 0.5.0: --screen renders the capture as a terminal grid

- **screen.py: a layout-only ANSI interpreter over the raw capture** —
  the C0/CSI subset that determines WHERE characters sit: cursor
  positioning (CUP, CUU/CUD/CUF/CUB, CHA/CNL/CPL, VPA/HPA, CR/LF/BS/
  TAB, IND/RI/NEL, DECSC/DECRC), erase (EL/ED/ECH), character and line
  insert/delete (ICH/DCH, IL/DL), scrolling (SU/SD, DECSTBM scroll
  regions — editor redraws depend on a protected status line), and
  xterm's deferred autowrap (wrapping only when the NEXT printable
  arrives — immediate wrapping would leave phantom blank lines under
  every full-width redraw). SGR, private modes, OSC/DCS strings, and
  charset selection are parsed-and-discarded; unrecognized-but-
  grammatical sequences discard invisibly; truncated sequences render
  nothing, which is what a real terminal shows while still waiting for
  the tail — the bytes themselves stay in --raw/--capture-out for
  forensics, the same division of labor as ansi.py.
- **`--screen`** — the grid replaces the byte stream on stdout (one
  line per screen row, trailing whitespace and empty bottom rows
  trimmed); composes with `--capture-out` (raw bytes still land in the
  file) and the BEL note; refuses `--repl` (a live session has no
  final capture to render). The acceptance cases render legibly: zsh's
  `eecho hi` backspace artifact → `echo hi`, ble.sh's `[▽][▶]…` probe
  → plain bracketed text, and the `bash -i` integration pins
  prompt/echo appearing ONCE, not four interleaved copies.
- Honest boundaries stated where they belong: every codepoint occupies
  one cell (wide glyphs included); unhandled control bytes render as
  nothing — --screen answers layout questions, not byte questions, so
  the SOH gotcha still routes through od/--raw; alternate-screen not
  modeled; when a render looks impossible, --raw is the ground truth.
  README "Viewing the output", man OPTIONS + CAVEATS, NOTES.md
  pointers from the redraw/SOH gotchas, --help epilog.
- Tests 198 → 224: 23 golden-grid unit tests, one per sequence family
  (including the two acceptance captures), 3 CLI tests. Caught for
  real along the way: `'' in '<=>?'` is True in Python, so the
  private-marker check was silently discarding every BARE CSI sequence
  (`ESC[K`, `ESC[H`, `ESC[J` all no-ops) — the golden tests earned
  their keep immediately. Plus three wrong expectations of mine
  corrected against VT semantics (ECH doesn't move the cursor; RI
  preserves the column; DECSTBM steady state leaves the region's last
  row blank).
- **PLAN.md retires** — the three-PR roadmap is fully landed (0.3.7,
  0.4.0, 0.5.0); its Done entries live in agent-tasks.md and its deferred
  ideas moved to an agent-tasks.md Backlog section, per the plan's own
  lifecycle. 0.4.0 → 0.5.0 (pyproject, `_version.py`, man `.TH`).
  Wheel round-trip + `nix flake check` + `nix build` +
  `nix-build default.nix` verified before shipping.

---

## 2026-09-08 — 0.4.0: Session class + --repl interactive mode

- **session.py: `Session` owns the pty/subprocess mechanics and the
  whole per-step dispatch** — start/send_step/expect/resize/drain/
  close, with drain(), QUERY_RESPONDERS, TargetDied, WaitForTimeout,
  and the spawn helpers moved in from driver.py. driver.run() is now a
  thin loop over it, passing the i-of-N narrative as the death
  message — one code path for one-shot and interactive, no drift to
  accumulate. All the old import paths kept (driver.drain/
  TargetDied/WaitForTimeout work exactly as before); the fd-leak/
  reap/no-orphan semantics are preserved and now pinned on the class
  directly, not just observed through run().
- **`--repl`** — the same step vocabulary read from stdin one line at
  a time: '#' comments, ':quit'/EOF = settle + close, startup capture
  reported before the first step. After each step: just the NEW
  output since the last one (stripped unless --raw), then `<< DONE`;
  a failed step prints its fresh output and `<< DEAD <why>` and exits
  1. Exactly one response per step line, always — a driving agent
  never waits on a sentinel that won't come. Refuses positional steps
  and --steps-file (stdin is the step stream); every other flag
  applies. Delta cutting has WAIT-FOR-NEXT's honest boundary caveat,
  one level up.
- DEAD covers both TargetDied and WaitForTimeout: protocol totality
  beats taxonomy — a consumer keys on the sentinel, not the exception
  class; the exit code still distinguishes (0 clean, 1 DEAD).
- Tests 177 → 198: SessionTests (dispatch, expect/only_new/timeout,
  resize, reap, fd leaks including failed start, idempotent close,
  capture-path) and ReplTests (sentinel counts, EOF/:quit, comments,
  DEAD with deterministic death ordering — the target exits after a
  *completed* step, so the #58 startup race can't bite — malformed
  step as usage error, flag conflicts, --raw deltas, capture-out).
  The existing suite is untouched and green through the refactor.
- Docs: README's "Interactive mode" section, man SYNOPSIS/OPTIONS/
  EXAMPLES, --help epilog. 0.3.7 → 0.4.0 (a new interaction model
  justifies the minor bump; pyproject, `_version.py`, man `.TH`).
  Wheel round-trip + `nix flake check` + `nix build` +
  `nix-build default.nix` verified before shipping.

---

## 2026-09-08 — 0.3.7: step-vocabulary batch (PASTE, RESIZE, WAIT-FOR-NEXT, --capture-out, sleep/wait advisory)

- **`PASTE:<text>`** — real bracketed-paste framing (`\e[200~` …
  `\e[201~`) around a decode_raw payload, sent as one write: the only
  way to express a multi-line paste in one steps-file line, and the
  quiet way to hand a line editor a blob (newlines held literal, no
  per-keystroke completion/redraw machinery set off). Empty payload is
  a usage error. NOTES.md gains the honest-edges section (a target
  without bracketed-paste mode enabled sees the framing as ignored
  noise; `--steps-file` strips, positional args don't).
- **`RESIZE <rows> <cols>`** — driver-side mid-run resize: TIOCSWINSZ
  on the controller fd; the kernel delivers SIGWINCH to the pty's
  foreground group itself. Verified live: `stty size` → 24 80 →
  40 120 across the step. Validated as a usage error unless two
  integers ≥ 1 (a 0-size tty is exactly what --rows/--cols prevent)
  and ≤ 65535 (the winsize struct's 16-bit field).
- **`WAIT-FOR-NEXT:<pattern>`** — WAIT-FOR's matching against only
  output arriving after the step starts: `wait_for()` strips the
  accumulator once at step start and searches from that offset, so the
  echo of your own typing and a prompt that already contained the
  marker can't satisfy it (NOTES.md's WAIT-FOR gotcha, fixed by form).
  Documented honestly: exact to within ± one escape sequence that
  straddles the boundary. Timeout/death messages label the form
  WAIT-FOR-NEXT. No prefix ambiguity to worry about — `WAIT-FOR-NEXT:`
  does not start with `WAIT-FOR:` — but the parse order and a
  non-overlap test pin it anyway.
- **`--capture-out FILE`** — the raw, unstripped capture written in
  run()'s finally on EVERY exit path (clean finish, TargetDied,
  WaitForTimeout) — the failure path is the point, the partial capture
  is the diagnostic. `out` initialized before the try so a partial
  capture always exists; an unwritable path warns to stderr instead of
  masking the run's own exception. Stdout behavior unchanged.
- **lowercase `sleep`/`wait` advisory** — NOTES.md's embarrassing
  repro (the sleep that was never sent) as a stderr note at validation
  time: lowercase spellings matching `^(sleep|wait)\s+\d` get pointed
  at the `RAW:...\r` form that types it for real; uppercase (the
  documented driver form) stays silent. Advisory only, same shape as
  the near-miss warning it lives beside.
- `--help` epilog extended to cover all six step forms and the new
  flag; its "bulk paste != typing" bullet now points at PASTE instead
  of claiming bracketed paste is out of scope. README Steps/Other
  flags, man STEPS/OPTIONS/SYNOPSIS/EXAMPLES, `--list-keys` rows.
- Tests 138 → 177: parse/encode units per new form; PASTE byte-exact
  via `stty -icanon` + `od` (the RAW test's shape); RESIZE end to end
  via `stty size` (direct + CLI); capture-out raw/partial/timeout
  paths (direct + CLI, byte-exact); advisory present/absent;
  WAIT-FOR-NEXT negative (pre-existing text must NOT match), positive,
  and the typed-echo end-to-end discriminator (count ≥ 2 only if the
  wait didn't end on the echo). 0.3.6 → 0.3.7 (pyproject,
  `_version.py`, man `.TH`). Wheel round-trip + `nix flake check` +
  `nix build` + `nix-build default.nix` verified before shipping.
- Found while running the suite: the #37-pin CLI test
  (`test_early_target_death_exits_1_with_capture_and_explanation`) is
  flaky under load — ambient scheduling can stall the target past
  every `poll()` check, so the #37 guard never fires and the run
  exits 0. Pre-existing (measured 1/20 on unmodified main), untouched
  by this PR; filed #58.

---

## 2026-09-08 — the --ssh TODO item closes: verified against a genuinely different host

- The "second host" turned out to have been reachable all along under
  its other name: `ts-tenacity` (tailscale MagicDNS) pointed at the
  right machine but filtered port 22, while `nire-tenacity.local`
  (mDNS, same box's LAN address) answered immediately with the keys
  that were already set up. `hostname` confirms it's a different
  machine from this one (`nire-cube`), its own sshd, LAN network path.
- The full battery through the tool, first try on this host:
  round-trip execution, `stty size` → `24 80`, job control
  (`flags=himBHs`), CTRL-C killing a remote `sleep` (status 130 —
  notably no delivery race here, unlike loopback), `exit 3` finale
  shape, `--ssh-arg` passthrough. Everything typed was read-only
  (echo/stty) inside a `bash --norc` session.
- Pinned as `SecondHostSshTests` (round-trip/window/job-control +
  interrupt), gated on `TERMINAL_PUPPETEER_SSH_HOST`: CI has no second
  host, and an unconfigured run must not probe anyone's machines —
  set the variable to any reachable host with key auth and the tests
  verify *your* path. Suite 138 → 140 (both skip unconfigured).
- No version bump: tests and docs only. agent-tasks.md's Open section is now
  empty; the DONE entry carries the details.

---

## 2026-09-07 — 0.3.6: CSI/DCS stripping over a real ssh transport; the --ssh TODO half-closes

- **ansi: private-marker CSI and DCS sequences are stripped now.** The
  CSI branch only allowed `0-9;?` in the parameter area, so everything
  with the other legal parameter bytes (`:` and the private markers
  `< = >`) survived stripping: ble.sh's XTMODKEYS pushes (`ESC[>4;1m`),
  its DA2 request (`ESC[>c`), kitty flag pushes (`ESC[>Nu`), DA3
  (`ESC[=c`), colon-SGR (`38:2::r:g:b`). And there was no DCS branch at
  all, so XTGETTCAP traffic (fish and ble.sh both speak it) and
  --answer-queries' own XTVERSION reply leaked too. The regex now
  implements the actual VT grammar (parameter bytes `0-9:;<=>?`,
  intermediates `0x20-0x2F`, one final `0x40-0x7E`) — exact, not
  heuristic — plus a DCS branch with the OSC payload argument (payload
  can't contain ESC). `ESC E` (NEL) joined the single-char class.
  Unterminated sequences and UTF-8 continuation bytes that *look* like
  CSI syntax (ӣ = `\xd3\x9b`) stay untouched, pinned by tests. Seven
  new regex tests.
- **The `--ssh` TODO item halves.** The "needs a machine" blocker was
  too pessimistic: the local sshd is a real ssh transport. Verified
  end to end through the tool over `ssh -tt` loopback: round-trip
  execution, `stty size` → `24 80` propagating through ssh's pty
  request (a client with no tty on its side gets a 0x0 remote pty —
  probed directly), job control `m` over `-tt`, CTRL-C killing a remote
  foreground job (status 130), clean-logout and `exit 3` finale shapes,
  `--ssh-arg` passthrough. Pinned as `SshTransportTests`
  (`skipUnless` passwordless localhost ssh works), which drive a plain
  remote `bash --norc` on purpose — the transport is the thing under
  test, not the author's dotfiles. agent-tasks.md's item narrows to "a
  genuinely different host" (ts-tenacity's sshd was down when tried).
- **CI runs the ssh tests for real**: test.yml starts the runner's own
  sshd and authorizes it to itself — same reasoning as #53's fish PPA
  step (otherwise the tests run only on the author machine). Free on a
  public repo, and the step costs seconds.
- **README's documented `--ssh-arg -o` form never parsed**: argparse
  reads a bare `-o` value as the next option (`expected one argument`).
  README and the man page now show the `--ssh-arg=-o` form; a test pins
  the `=` form end to end.
- **Four new NOTES.md gotchas** from the verification: ssh puts every
  timing gotcha one tty further away (and CTRL-C delivery is racy —
  retry before concluding); `--answer-queries` is a fish fix that
  *breaks* ble.sh (the canned cursor-position replies arrive late, land
  as idle input, and drive endless redraws that swallow steps — left
  unanswered, ble.sh's probe storm self-settles in ~2s, measured on a
  bare pty); a step named `sleep N`/`wait N` is a driver wait, not a
  typed command (the repro that "failed" for an hour never sent its
  sleep at all); and WAIT-FOR can match the echo of its own input —
  pick output-only markers. The garbled `[▽][▶]…` at the top of
  ble.sh captures is *not* a stripping bug: that's ble.sh's
  cursor-glyph probe printing literal bracketed text between
  sequences.
- Fish-over-ssh skipped: fish isn't installed on this machine (0.3.1's
  verification used a throwaway `nix shell`), and the transport findings
  don't depend on which shell drove them. Needs fish installed or a
  second host (agent-tasks.md).
- Tests 129 → 138 (seven regex, two ssh transport, version bump
  sync-checked by the existing guard). 0.3.5 → 0.3.6 (pyproject,
  `_version.py`, man `.TH`).

---

## 2026-09-07 — CI: fish tests run for real; wheel round-trip job

- The two fish integration tests skipped everywhere — no fish on
  GitHub runners, none off-PATH locally — so #49's fix was never
  exercised on a schedule. test.yml installs the fish-shell release-4
  PPA series (the tests were verified against 4.8.1; distro repos
  carry 3.x, whose startup probing differs), and the tests gate on
  fish major >= 4 via `fish --version` instead of mere presence, so
  other fish skips them rather than failing on an unwritten-for path.
- New packaging job: build the wheel, pip install into a scratch venv,
  run the installed entry point from OUTSIDE the checkout (--version,
  --list-keys, one real cat repro via --steps-file -). Guards #30's
  historical failure mode — a wrapper shadowing the package it
  imported was invisible in place and only broke installed.
- No version bump: nothing user-facing changed. 121 ran locally (2
  fish skips off-PATH); CI's fish slots now covered by the PPA.

---

## 2026-09-07 — 0.3.5: ANSI stripping actually strips; agent-tasks.md; CI hardening

- **ansi: ST-terminated OSC sequences are stripped now.** The regex's
  OSC branch only matched the BEL terminator, so `ESC ] payload ESC \`
  — fish's OSC 133 prompt marks, OSC 0 titles, OSC 8 hyperlinks, and
  terminal-puppeteer's *own* `--answer-queries` OSC 11 reply — survived
  into default-stripped output as visible garbage (`]133;C;cmdline_url=...`
  was all over this session's captures before anyone asked why). An OSC
  payload cannot contain ESC or BEL, so the negated classes are exact;
  unterminated OSCs stay untouched (strip-what-is-provable, unchanged).
  Five new regex tests including the hyperlink and the
  payload-ends-then-next-sequence boundary.
- **agent-tasks.md returns** (the old todo.md went away in the package split):
  the working list this entry came from, with blockers named on items
  that can't be done from here (live `--ssh` needs a second host).
- **CI hardening**: packaging job now asserts the README's man-page
  claim — wheel contains `share/man/man1/terminal-puppeteer.1`, it
  installs into the venv's `share/man/man1/`, `man --warnings -l`
  accepts the installed copy (data-files is a deprecated setuptools
  feature; this is the tripwire). Test matrix gains 3.14 (the author
  machine's and nix build's version). New tests: pyproject/package
  version-sync (was comment-enforced), `python -m terminal_puppeteer`
  entry point. Tests 123 → 129.
- 0.3.4 → 0.3.5 (pyproject, script version, man `.TH`).

---

## 2026-09-07 — 0.3.4: --rows/--cols; WAIT-FOR fails fast on a dying target

- **`--rows`/`--cols`** (defaults 24/80, matching 0.3.3's TIOCSWINSZ):
  size-sensitive repros — a TUI that only misrenders below N columns,
  an editor's wrapping — can now pick their own size. Values below 1
  are a usage error: the flags may override the size, not un-set it
  (0x0 is exactly the bug the defaults fix).
- **WAIT-FOR fails fast when the target dies mid-wait** (the other
  loose end from 0.3.2's loud-failure work): a dead target can never
  produce the pattern, so burning the whole `--wait-for-timeout` budget
  was a slow no-op ending in a misleading "no match". The wait now
  notices the death on each poll and raises `TargetDied` immediately —
  "target exited with status 3 while WAIT-FOR step '...' was still
  waiting -- the pattern cannot arrive any more". The pattern is
  re-checked before the liveness test, so a marker in the target's
  dying output burst still matches normally. Exit-status wording
  factored into `_exit_status_why`, shared with run()'s step-loop
  check.
- Tests 119 → 123: flag passthrough end to end (`stty size` → `40 120`
  through the CLI), 0/negative size usage errors, the mid-wait
  fail-fast (and its boundary — a target already dead before the step
  starts is run()'s check, a different message shape; the test now
  keeps the target alive past step start to hit the mid-wait path).
- Docs: README's Other flags + Exit behavior, man SYNOPSIS/OPTIONS/
  EXIT STATUS (warning-checked). 0.3.3 → 0.3.4 (pyproject, script
  version, man `.TH`).

---

## 2026-09-07 — 0.3.3: the pty has a real window size (24x80); tracker + CI hygiene

- The pty reported **0 0** to anything that read the tty directly —
  `pty.openpty()` leaves the window size at zero. Interactive bash
  masked it with its own 80x24 fallback (`$COLUMNS` looked fine), but
  `stty size`, ncurses, and fish's line editor (its cursor math ran on
  a zero-width line, seen during the #37 chase) all saw a
  zero-sized terminal no human has ever sat in front of. `run()` now
  sets `TIOCSWINSZ` 24 rows × 80 cols right after `openpty()`, so the
  target's view matches a fresh real terminal.
- Test asserts `stty size` → `24 80` (stty is coreutils, present in
  the nix sandbox where procps's `ps` wasn't — same lesson as #49).
- Tracker hygiene: #43–#47 and #36 were still open — #48's body used
  bold labels, not closing keywords, so nothing auto-closed. Closed
  with a pointer to #48 each.
- CI hygiene: `actions/checkout` v4 → v5 in both workflows, clearing
  the Node 20 deprecation annotation GitHub already force-runs on
  Node 24.
- Docs: README and man CAVEATS mention the real terminal size (man
  CAVEATS also de-staled — its "the tool exits 0 even if the target
  died" sentence predated 0.3.2's loud failure). 0.3.2 → 0.3.3
  (pyproject, script version, man `.TH`).

---

## 2026-09-07 — 0.3.2: a target that dies with steps un-sent fails loudly

- The last #37 loose thread: the tool used to exit 0 even when the
  target died at startup — the pty echoes every step regardless, so
  fish's old refusal looked like a quiet successful run. Now `run()`
  checks the target before each step, and a death with steps remaining
  raises `TargetDied`: the CLI still prints the partial capture to
  stdout (it's the diagnostic — typically the target's dying words,
  stripped the same as a successful run), and one stderr line names
  the exit status or signal (decoded, e.g. `killed by signal 9
  (SIGKILL)`), how many steps were never sent, and which step would
  have been next. Exit 1, no usage block — the same runtime-failure
  shape as #46's `WaitForTimeout`.
- Dying **after** the last step is deliberately not an error: `'exit'
  ENTER` as the finale is the normal clean-logout shape. Tested both
  ways, plus the signal-name decoding and the CLI's stdout/stderr/exit
  shape. 114 tests before this change; +4 here → 118.
- Docs: README's Exit behavior section and the man page's EXIT STATUS
  now spell out the new failure mode; NOTES.md's masquerade gotcha
  rewritten to describe the check (the echo-vs-output distinction
  stays as advice for reading any capture); cli.py's epilog mentions
  it. 0.3.1 → 0.3.2 (pyproject, script version, man `.TH`).

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

## 2026-08-24 — CONTRIBUTORS.md + .claude/agent-styleguide.md: LLM agents explicitly invited (#26)

- Asked to write `CONTRIBUTORS.md` inviting issues/PRs from LLM agents
  (not just humans, and not merely tolerating them) as long as
  well-written and human-readable, plus a style guide for what that
  means concretely.
- Split into two files rather than one, on request: `CONTRIBUTORS.md`
  answers "can I contribute and how" (who's welcome, filing an issue,
  opening a PR); `.claude/agent-styleguide.md` answers "what does well-written mean
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
  ruleset, no `wiki/`/`lessons-learned.md` — `agent-devlog.md`/`NOTES.md` play
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
