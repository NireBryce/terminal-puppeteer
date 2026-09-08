# Plan — `--screen`

Dated 2026-09-08. The working plan for the last remaining PR agreed
this session; decisions are settled, not proposals. (PR 1 — the
step-vocabulary batch, 0.3.7 — and PR 2 — Session/REPL, 0.4.0 —
landed 2026-09-08 and moved to TODO.md's Done section, as this file's
lifecycle describes.) When it lands, the done items move to TODO.md's
Done section and this file goes away (same lifecycle TODO.md's header
describes) — it is a plan, not a second tracker.

## Settled decisions

| Question | Decision |
|---|---|
| Packaging | PRs: vocabulary batch (0.3.7, **landed**), Session/REPL (0.4.0, **landed**), `--screen` (0.5.0) |

## PR 1 — step-vocabulary batch (0.3.6 → 0.3.7): LANDED

PASTE:, RESIZE, --capture-out, the lowercase sleep/wait advisory, and
WAIT-FOR-NEXT:<pattern> all shipped in 0.3.7 — see TODO.md's Done
section and DEVLOG 2026-09-08 for what landed.

## PR 2 — Session/REPL (0.3.7 → 0.4.0): LANDED

Session in session.py owning the mechanics + the whole per-step
dispatch, driver.run() as a thin loop over it, and the --repl
stdin/stdout sentinel protocol — shipped in 0.4.0. See TODO.md's Done
section and DEVLOG 2026-09-08.

## PR 3 — `--screen` (0.4.0 → 0.5.0)

Render the final capture as an actual terminal grid (default 24x80,
`--rows`/`--cols` respected) instead of a byte stream — the thing that
makes captures like ble.sh's `[▽][▶]…` cursor-glyph probe and zsh's
`eecho hi` backspace artifact legible at a glance.

- New `screen.py`: a stdlib-only ANSI-grid interpreter over the raw
  capture — the C0/CSI subset that determines *layout* only:
  cursor positioning (CUP, CUU/CUD/CUF/CUB, CHA, CR/LF/BS, IND/RI,
  NEL), erase (EL, ED, ECH), insert/delete lines (IL/DL — ble.sh
  redraws use them), scroll regions only as far as repro captures
  need (honest subset, documented); SGR and everything non-layout is
  parsed-and-discarded. Anything unrecognized stays untouched
  (strip-what-is-provable, same policy as ansi.py).
- `--screen` flag: after the usual capture, print the grid as text
  (one line per row, trailing whitespace trimmed); composes with
  `--capture-out` (grid file separate, raw capture still available).
- Tests: golden-grid unit tests per sequence family (the ble.sh probe
  capture from this session is the acceptance case — it must render
  to a clean prompt line); a `bash -i` integration asserting the
  prompt/echo appears once, not four times.
- Docs: README (Viewing the output), man OPTIONS + CAVEATS (the
  "not a terminal emulator, a layout renderer" boundary), NOTES.md
  pointer from the redraw gotchas, DEVLOG. 0.4.0 → 0.5.0.

## Sequencing

PR 1 landed first because PR 2's Session absorbs its step machinery
(done); PR 3 last, consuming `Session`'s output stream — building it
before the refactor would have meant throwing away its plumbing. Full
ship flow per PR. CI unchanged — new tests are ordinary suite members.

## Backlog (considered, not now)

- `--json` events log (steps sent, replies answered, timings) for
  machine-readable run reports.
- Mouse-event injection (SGR encoding) for TUI repros.
- Per-keystroke type delay (human-speed typing vs fast input changes
  line-editor behavior).
- More QUERY_RESPONDERS entries as targets demand them.
