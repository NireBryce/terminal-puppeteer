# Recipes

Shaped around what a repro actually needs. Every command below was run
against a real `bash -i` (or `cat`) before being written down here.

## Pace on the target's output, not on clocks

Every step is followed by up to `--step-delay` seconds (default 1.5) of
draining; `--connect-wait` (default 3.0) is the same before the first
step; `--settle` (default 1.5) after the last. All three stop early
once output goes quiet for 200ms, so raising them costs nothing when
the target is fast — the defaults exist for the slow case.

Fixed delays are the blunt instrument, though. The sharp one is
`WAIT-FOR:`:

```sh
./terminal-puppeteer.py --cmd 'bash -i' \
  'make' ENTER 'WAIT-FOR:/BUILD (OK|FAILED)/' 'echo settled' ENTER
```

The three compose: `--step-delay` for typing cadence, `SLEEP N` for
settle-time, `WAIT-FOR:` for "tell me when you're actually there".

Two ways this bites (both the matching being correct, not broken):

- **It's case-sensitive.** `'echo START-marker-123' ENTER` +
  `WAIT-FOR:/MARKER.?.123/` times out: the output says `marker`, the
  pattern says `MARKER`.
- **It matches your own echo.** `WAIT-FOR` sees the stripped
  transcript, which includes what *you* typed. Use `WAIT-FOR-NEXT:`
  when the marker exists in your input too.

Against a slow-initializing target (fish probing capabilities, a login
shell with a heavy rc), a bare `SLEEP` can land mid-init, where typed
bytes get swallowed by a redraw — `WAIT-FOR:` on a prompt marker, then
a settle beat, is the reliable shape.

## Multi-line input

A plain multi-line write is not typing: each newline submits the line
before it, and a function definition arrives as broken partial
commands. Two fixes, in order of preference:

```sh
# 1. PASTE: — real bracketed-paste framing, newlines held literal
#    by editors that enabled bracketed-paste mode (readline, zle, ble.sh)
./terminal-puppeteer.py --cmd 'bash -i' 'PASTE:foo() {\n  echo inside\n}\nfoo' ENTER

# 2. write the file out-of-band, source it — nothing typed at all
./terminal-puppeteer.py --cmd 'bash -i' 'source /tmp/repro.sh' ENTER
```

## Reading what came back

Default stdout is the flat transcript — ANSI escape sequences
stripped, in arrival order, **capped at `--max-output` bytes (30000)**
with one loud marker naming the cut. Read it like a terminal
transcript, prompt lines and command output interleaved. The views on
top of the same capture:

- `--raw` — keep the escape sequences. Ground truth for anything the
  other views might be interpreting.
- `--capture-out FILE` — the raw bytes to a file, written on *every*
  exit path: a run that dies mid-way still leaves the partial capture,
  which is the diagnostic. For `od`-level byte questions this beats
  scraping stdout.
- `--screen` — render the capture as the terminal grid it was, sized
  by `--rows`/`--cols`. A layout renderer, not a terminal emulator:
  zsh's `eecho hi` redraw artifact renders as `echo hi`, redraw-heavy
  prompts show their final state once. Colors/modes are not kept;
  every codepoint is one cell; byte-level questions still need
  `--raw`/`--capture-out` and `od`. With `--repl` it goes live: each
  step's response is the current grid instead of the byte delta.
- `--full-output` — lift the cap: everything in context, uncapped.

**The loop for a big capture**: keep the complete bytes with
`--capture-out` (never capped), grep/od the file, then page just the
interesting ranges back through `--show` — `--tail 40`, or
`--offset N --limit M` around what the grep found (line numbers are
counted on the view `--show` emits, so they match a grep over the same
view). An agent's transcript should hold what it chose to look at, not
whatever volume the target produced.

One stderr note may join any run: the count of BEL (`\x07`) bytes the
target sent — invisible in every view, but the standard unbound-key /
alert signal (with `-v`, per step).

**A byte that renders invisible is still there.** A control byte with
no glyph (an SOH inside a variable, say) reads as nothing in the
transcript, `--raw` included. When a captured value matters at the
byte level, pipe it through `od -c` — see NOTES.md's SOH story for the
real debugging time this cost.

## Interactive sessions: `--repl`

`--repl` starts the target, prints whatever the connect-wait window
caught, then reads steps from stdin one line at a time. **One line is
one step** — the text and its `ENTER` are two lines:

```sh
printf 'echo hi\nENTER\n' | ./terminal-puppeteer.py --cmd 'bash -i' --repl
```

After each step: just the *new* output since the last one (stripped
unless `--raw`), then exactly one sentinel — `<< DONE`, or
`<< DEAD <why>` when the step failed (target died, pattern never
arrived). `#` lines are comments; `:quit` or EOF ends the session.
Exit 0 for a clean end, 1 for DEAD — so a driving agent can send a
line, read the response, and decide the next line without ever
waiting on a sentinel that won't come. Takes no positional steps and
no `--steps-file`; every other flag applies.

From Python, the same machinery is importable:
`terminal_puppeteer.session.Session` (`start`/`send_step`/`expect`/
`resize`/`close`) — `driver.run()` is a thin loop over it, one code
path for both.

## Window size

The pty reports a real size from the start — 24 rows × 80 cols, a
fresh terminal's, not the 0×0 a bare `pty.openpty()` gives (`stty
size` answers `24 80`). `--rows`/`--cols` override the start size;
`RESIZE <rows> <cols>` changes it mid-run for repros that only
misbehave after a resize. Both floors are 1 — a 0-sized tty is
exactly what the defaults exist to prevent.

## Over ssh

`--ssh HOST` is shorthand for `--cmd "ssh -tt HOST"`. Extra ssh
arguments via repeatable `--ssh-arg`, with the `=` form required for
values starting with `-`:

```sh
./terminal-puppeteer.py --ssh myhost --ssh-arg=-o --ssh-arg=BatchMode=yes 'echo hi' ENTER
```

Everything local applies one tty further away: latency multiplies
every timing gotcha (pace on output, not clocks), and CTRL-C delivery
to a remote foreground job is racy at the transport's mercy — verified
to work (status 130), but the same repro can fail then pass
back-to-back. Retry before concluding the interrupt doesn't land.

## Environment and secrets

`--env KEY=VALUE` (repeatable) merges onto the inherited environment —
for things that must be true *before* the target starts, which no step
can guarantee (`--env TERM=dumb` skips capability probing that races
any early `'export TERM=dumb' ENTER` step).

A credential typed as a step is transcript content — `-v` echoes
steps, the capture contains the pty's echo, positional steps sit in
shell history, and stderr failure messages name un-sent steps even
without `-v`. Hand secrets to the target out-of-band (inherited
environment, target-side config); NOTES.md has the full account.

## Exiting

This tool **only ever terminates the target** on its way out —
SIGTERM, then SIGKILL after 5 seconds — and never types a goodbye,
because no single keystroke quits every target. Want a clean logout
in the capture? End your own step list with the quit that fits:
`'exit' ENTER` for a shell, `CTRL-D` for a REPL. A target that dies
*after* the last step is a normal exit-0 run; a target that dies
*with steps un-sent* is a loud failure (exit 1, partial capture on
stdout, one stderr line naming the status and what was never sent) —
a dead target used to look exactly like a quiet successful run,
which is why the check exists.

---

*Facts checked against terminal-puppeteer 0.6.0, empirically
(2026-10-26) — originally under issue #64; re-verified through 0.5.1's
security pass and 0.6.0's output-cap changes.*
