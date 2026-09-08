# Plan — Session/REPL + `--screen`

Dated 2026-09-08. The working plan for the remaining PRs agreed this
session; decisions are settled, not proposals. (The first PR from this
plan — the step-vocabulary batch, 0.3.7 — landed 2026-09-08 and moved
to TODO.md's Done section, as this file's lifecycle describes.) When
the rest land, the done items move to TODO.md's Done section and this
file goes away (same lifecycle TODO.md's header describes) — it is a
plan, not a second tracker.

## Settled decisions

| Question | Decision |
|---|---|
| REPL transport | stdin/stdout line protocol (steps in, capture + sentinel out); `Session` class stays importable for library users |
| Packaging | PRs: vocabulary batch (0.3.7, **landed**), Session/REPL (0.4.0), `--screen` (0.5.0) |

## PR 1 — step-vocabulary batch (0.3.6 → 0.3.7): LANDED

PASTE:, RESIZE, --capture-out, the lowercase sleep/wait advisory, and
WAIT-FOR-NEXT:<pattern> all shipped in 0.3.7 — see TODO.md's Done
section and DEVLOG 2026-09-08 for what landed. Section kept as a
one-line marker so PR 2/3's numbering below still reads.

## PR 2 — Session/REPL (0.3.7 → 0.4.0)

- New `session.py`: `Session` class — `start` / `send_step` /
  `expect` / `resize` / `close` — owning the per-step dispatch;
  `driver.run()` refactored onto it (one code path, no drift).
  Reaping/fd-leak semantics per `RunDirectTests`' pattern.
- `--repl` on top: reads the same step vocabulary line-by-line from
  stdin (`#` comments, `:quit`, EOF = settle + close); after each
  step prints the NEW output (stripped unless `--raw`) then a
  `<< DONE` sentinel line; `<< DEAD <why>` on target death.
- Tests: direct Session tests (dispatch, expect, resize, fd leak,
  reap) + CLI REPL pipe test asserting the sentinel protocol.
- Docs: README section, man SYNOPSIS/OPTIONS, DEVLOG. Version
  0.3.7 → 0.4.0 (new interaction model justifies the minor bump).

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

PR 1 (landed) came first because PR 2's Session absorbs its step
machinery; PR 3 last (the screen renderer consumes `Session`'s output
stream — building it before the refactor would mean throwing away its
plumbing). Each PR through the full ship flow. CI unchanged — new
tests are ordinary suite members; the REPL test pipes stdin.

## Backlog (considered, not now)

- `--json` events log (steps sent, replies answered, timings) for
  machine-readable run reports.
- Mouse-event injection (SGR encoding) for TUI repros.
- Per-keystroke type delay (human-speed typing vs fast input changes
  line-editor behavior).
- More QUERY_RESPONDERS entries as targets demand them.
