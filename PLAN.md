# Plan — step-vocabulary batch + Session/REPL + `--screen`

Dated 2026-09-08. The working plan for the three PRs agreed this
session; decisions are settled, not proposals. When all three land,
this file's done items move to TODO.md's Done section and this file
goes away (same lifecycle TODO.md's header describes) — it is a plan,
not a second tracker.

## Settled decisions

| Question | Decision |
|---|---|
| REPL transport | stdin/stdout line protocol (steps in, capture + sentinel out); `Session` class stays importable for library users |
| Packaging | Three PRs: vocabulary batch (0.3.7), Session/REPL (0.4.0), `--screen` (0.5.0) |
| PASTE payload | bind-style escape-decoded (`decode_raw`) — the only way to express multi-line pastes in one steps-file line |
| `sleep N` advisory | fires only for lowercase `sleep`/`wait` spellings (docs use uppercase for the driver form); advisory only, never blocks |

## PR 1 — step-vocabulary batch (0.3.6 → 0.3.7)

1. **`PASTE:<text>`** — new prefix in `keys.py`: `encode_step` returns
   `b'\x1b[200~' + decode_raw(payload) + b'\x1b[201~'`, one write.
   Empty payload → ValueError → usage error (same shape as
   WAIT-FOR's). Add `PASTE_PREFIX` to `warn_if_near_miss`'s skip list
   and a row to `print_key_list`'s `extra_rows`.
2. **`RESIZE <rows> <cols>`** — `parse_resize_step()` in keys.py
   (tuple or None; ValueError unless integers ≥ 1, mirroring
   `parse_wait_step`). In `driver.run()`'s step loop:
   `TIOCSWINSZ` on the controller fd, then `drain(step_delay)` — the
   kernel delivers SIGWINCH to the pty's foreground group itself.
   Verify live (`RESIZE 40 120` → `stty size` → `40 120`). Row in
   `extra_rows`; parsed in cli's validation loop like SLEEP/WAIT-FOR.
3. **`--capture-out FILE`** — `run()` gains `capture_path=None`; init
   `out = b''` before the try, write the RAW capture in the `finally`
   (success, TargetDied partial, WaitForTimeout partial all covered).
   Stdout stripping behavior unchanged.
4. **`sleep N` advisory** — keys.py: steps matching
   `^(sleep|wait)\s+\d` with a lowercase first token print a stderr
   advisory ("driver-side wait, sends nothing; to type it use
   `RAW:sleep 30\r`"). Uppercase spellings stay silent. Advisory only.
5. **`WAIT-FOR-NEXT:<pattern>`** — distinct prefix, parsed before
   WAIT-FOR (no prefix ambiguity: `WAIT-FOR-NEXT:` does not start with
   `WAIT-FOR:`). `wait_for()` strips the accumulator once at step
   start to record the boundary length, then searches the stripped
   stream from that offset — cannot match the first prompt or its own
   typed echo. The ± one escape-sequence straddling the boundary is
   documented honestly.

**Tests:** unit per new parse/encode form; PASTE byte-exact via
`stty -icanon` + `od` (RAW test's shape); RESIZE end-to-end via
`stty size`; capture-out raw-vs-stripped + failure path; advisory
present/absent (lowercase vs uppercase); WAIT-FOR-NEXT negative
(pre-existing text must NOT match) + positive.

**Docs:** README Steps + Other flags, man STEPS/OPTIONS,
`--list-keys` rows, NOTES.md (PASTE vs typing for line editors),
DEVLOG entry. Version 0.3.6 → 0.3.7 (pyproject, `_version.py`,
man `.TH`). Wheel round-trip + `nix flake check` + `nix build` +
`nix-build default.nix` before shipping. Ship flow: branch → PR →
two confirmations.

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

PR 1 first (PR 2's Session absorbs its step machinery), PR 3 last
(the screen renderer consumes `Session`'s output stream — building it
before the refactor would mean throwing away its plumbing). Each PR
through the full ship flow. CI unchanged — new tests are ordinary
suite members; the REPL test pipes stdin.

## Backlog (considered, not now)

- `--json` events log (steps sent, replies answered, timings) for
  machine-readable run reports.
- Mouse-event injection (SGR encoding) for TUI repros.
- Per-keystroke type delay (human-speed typing vs fast input changes
  line-editor behavior).
- More QUERY_RESPONDERS entries as targets demand them.
