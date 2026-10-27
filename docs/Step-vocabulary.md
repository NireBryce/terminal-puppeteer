# Step vocabulary

A run is: exactly one target (`--ssh HOST` or `--cmd COMMAND`), then a
flat list of **steps**, applied in order. Steps come from two sources,
concatenated — positional arguments first, then `--steps-file PATH`
(one per line; blank lines skipped, `#`-prefixed lines are comments,
`PATH` of `-` reads stdin). `--list-keys` prints the key table below
from the same data the tool itself matches against, so it can never
drift from this page or the man page.

## The rule that bites everyone once

**No step ever gets an implicit trailing newline.** A step is a
keystroke-bytestring, not a command line. `'git status'` types seven
characters and stops; `'git status' ENTER` runs it. Forgetting the
`ENTER` is the single most common mistake with this tool — the capture
shows the command sitting typed-but-unsubmitted at the prompt, which
looks like the target ignoring you.

## Named keys (case-insensitive)

| name | sends |
|---|---|
| `TAB` | `\t` |
| `ENTER`, `RET`, `CR` | `\r` |
| `NEWLINE` | `\n` |
| `ESC` | `\x1b` |
| `BACKSPACE` | `\x7f` |
| `UP`, `DOWN`, `LEFT`, `RIGHT` | `\x1b[A` / `\x1b[B` / `\x1b[D` / `\x1b[C` |
| `HOME`, `END` | `\x1b[H` / `\x1b[F` |
| `DELETE` | `\x1b[3~` |
| `PGUP`, `PGDN` | `\x1b[5~` / `\x1b[6~` |
| `CTRL-`*letter* | any `A`–`Z`, e.g. `CTRL-C` → `\x03` |

`CTRL-`*letter* is computed, not a hardcoded list — `CTRL-U`,
`CTRL-L`, `CTRL-W`, `CTRL-A` all work, `ord(letter) - 64` the same way
a real terminal computes a control character.

Anything that isn't a named key or one of the forms below is sent as
literal text, exactly as written. Two advisories can fire at validation
time (stderr only, never blocking, never changing what is sent):

- a step *close* to a key name — `ENTR`, `CTRLC` — warns it looks like
  a typo but goes out as literal text;
- a step spelled like a typed command — lowercase `sleep 30` /
  `wait 30` — is a recognized **driver-side wait** that sends nothing;
  the note points at `RAW:sleep 30\r`, which actually types it.

## The other step forms

### `RAW:<escapes>` — arbitrary bytes, one write

`RAW:\e[I` sends the focus-in sequence as a single write. Escapes:
`\e` (ESC), `\C-x` / `\c-x` (any letter plus `@[\]^_`), `\xHH`,
`\a \b \t \n \v \f \r`, `\\`; anything else stays literal, so plain
text that merely starts with a backslash keeps meaning what it always
meant. One write matters because an `ESC` step + `'[I'` text step is
*two* writes, and the gap between them gives the target's
escape-sequence parser a window to time out and discard the partial
sequence.

### `PASTE:<text>` — real bracketed paste, one write

`PASTE:foo() {\n  echo inside\n}\nfoo` wraps the payload in
`\e[200~` … `\e[201~` and sends it as one write. A line editor with
bracketed-paste mode enabled holds the payload's newlines literal in
the edit buffer instead of executing each line as it arrives (what a
plain multi-line write does), and skips the per-keystroke machinery —
completions, autosuggestions, redraws — that typing would set off.
Same escapes as `RAW:`. Honest edge: a target that never enabled
bracketed-paste mode sees the framing as (usually ignored) noise and
types the payload plainly, newlines submitting. And remember
`--steps-file` strips surrounding whitespace per line — a payload with
significant leading/trailing spaces should come from a positional
argument.

### `SLEEP <seconds>` (or `WAIT <seconds>`) — driver-side pause

Sends nothing, keeps draining for N seconds. Unlike a target-side
`'sleep 5' ENTER`, it puts nothing in the session under test — no
history entry, no `PROMPT_COMMAND`/`precmd` hooks firing
mid-observation — and works for non-shell targets. `--step-delay`
retimes every step; this retimes one.

### `RESIZE <rows> <cols>` — resize mid-run

`TIOCSWINSZ` on the pty; the kernel delivers `SIGWINCH` to the
target's foreground process group, the same delivery a real terminal's
resize produces. Two integers, each 1–65535 (the winsize struct's
16-bit fields). `--rows`/`--cols` set the size the pty *starts* with;
this changes it once the session is live.

### `WAIT-FOR:<pattern>` — drain until output matches

Waits until the ANSI-**stripped** output accumulated so far matches —
a literal substring, or a regex when wrapped in `/.../`:

```
'WAIT-FOR:/===.?.MARK.?.===/'    # survives escapes interleaving the marker
'WAIT-FOR:ready'                 # literal substring
```

Matching the stripped stream is deliberate: matching raw bytes is
where a line editor's cursor-positioning escapes land between the
characters of the thing you're waiting for. Matching is
**case-sensitive**, and the transcript includes the echo of your own
typing — `echo done-$?` + `WAIT-FOR:done-` matches the *echo*, not
the output. Either wait for a marker your input can't contain, or use
`WAIT-FOR-NEXT:`. A pattern that never arrives fails the run (exit 1,
one stderr line naming the step and the last output seen); the budget
is `--wait-for-timeout` (default 10s), and a target that dies
mid-wait fails immediately instead of burning the budget. The budget
is checked between drains, not during a search — a catastrophically
backtracking regex can out-wait it inside one `search()` (see NOTES's
"your own CPU budget"); prefer the literal form when a regex isn't
needed.

### `WAIT-FOR-NEXT:<pattern>` — same, minus your own echo

Only output arriving *after* the step starts can match: the echo of
your typing and a prompt that already contained the marker cannot
satisfy it. Same `/.../` form, same timeout. Honest boundary: "after"
is a length on the stripped stream, and one escape sequence can
straddle it, so it's exact to within about one escape sequence.

### `WAIT-FOR-SCREEN:[!]<pattern>` — what's displayed now

Matches the rendered `--screen` grid, so text that was drawn and then
erased can't satisfy it (`echo X`, `clear`, `WAIT-FOR:X` passes;
`WAIT-FOR-SCREEN:X` times out). Same literal / `/regex/` forms and
timeout; `^`/`$` anchor per row. A leading `!` waits for the pattern to
be *gone*. Layout-subset renderer limits apply (no alternate screen).

### `SCREEN` — print the grid now

Sends nothing. One-shot: a `-- SCREEN <n> --` block on stdout ahead of
the final view. `--repl`: that step's response. Uppercase only.

---

*Facts checked against terminal-puppeteer 0.6.0, empirically
(2026-10-26) — key bytes via `--list-keys`, every example above run
against a real `bash -i`; originally under issue #64, re-verified
through 0.5.1's security pass and 0.6.0's output-cap changes.*
