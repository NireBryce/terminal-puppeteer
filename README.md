# terminal-puppeteer

Lets an LLM agent drive a real interactive program through a pty —
sending actual keystrokes with delays between them, and reading back what
the program actually did — instead of the one-shot, no-tty view a normal
tool call gets.

What comes back is a flat, in-order byte stream — everything the pty
produced, ANSI stripped by default — not a rendered screen with a cursor
position. That's the right shape for a line-oriented shell/REPL session,
which is what this tool targets; it's the wrong shape for a full-screen
program like `vim` or a debugger, where you actually want the current
screen contents. Most similar tools (see Alternatives, below) take that
screen-reading approach instead — reach for one of those if that's what
you're driving.

Built for reproducing bugs that only exist in a live, human-typed terminal
session: `$-` not containing `i` because no pty was allocated, a
completion/suggestion race that only fires while keys are still landing, a
program that behaves differently piped than attached to a real terminal.
An agent has no terminal of its own to hit those with — this is the
stand-in.

Stdlib only (`pty`, `subprocess`, `select`), no dependencies, no install
needed to run in place: `terminal-puppeteer.py` is a thin entry point,
`terminal_puppeteer/` next to it is the actual implementation, split by
concern (`keys.py`, `ansi.py`, `driver.py`, `cli.py`) once it outgrew one
file. Grab the whole directory, not just the script, if copying this
elsewhere — the wrapper needs its sibling package to run.

`pip install .` (or from a released version) gives a `terminal-puppeteer`
command instead — no `.py`, a proper generated entry point rather than a
copy of the script. Same tool either way; which name applies depends on
whether it's installed.

Nix: `nix run github:NireBryce/terminal-puppeteer`, or add it as a flake
input for `packages.<system>.default`. `default.nix` works too, without
flakes (`nix-build default.nix`).

## Provenance

Written and maintained largely by Claude Code agents; the human behind
this repo is not reading every line before it lands. That's fine for the
personal debugging use case this exists for, but it means the review
here is agent-review, not human-review — read the code yourself before
pointing it at anything sensitive.

## Contributing

Issues and PRs are welcome — explicitly including from LLM agents, not
just human contributors, as long as the submission is well-written and
human-readable. See `CONTRIBUTORS.md` for how to file one, and
`STYLEGUIDE.md` for what "well-written" means here concretely for an
LLM-originating submission: verify claims before making them, explain
why alongside what, write real tests, and match this repo's own voice
rather than generic AI-PR boilerplate.

## Quick start

```sh
# over SSH -- the case this was built for
./terminal-puppeteer.py --ssh myhost 'git co' TAB CTRL-C

# any local interactive program, not just a remote shell
./terminal-puppeteer.py --cmd 'bash -i' 'echo $-' ENTER
./terminal-puppeteer.py --cmd 'tmux attach -t work' 'echo hi' ENTER

# a scripted multi-step repro, read from a file instead of argv
./terminal-puppeteer.py --ssh myhost --steps-file repro.txt

# what "TAB", "CTRL-U", etc. actually send, without a trip to the docs
./terminal-puppeteer.py --list-keys
```

Output is everything the pty produced, ANSI escape sequences stripped by
default (`--raw` to keep them) — read it the way you'd read a terminal
transcript, in order, prompt lines and command output interleaved. One
advisory stderr note may join it: if any BEL (`\x07`) bytes came back
from the target — invisible in both stripped and raw output, but the
standard unbound-key/alert signal — the count is printed after the run,
so a bell-heavy capture doesn't look identical to a bell-free one.

Full reference: `--help`, or `man ./man/terminal-puppeteer.1` (installed
to the usual `man(1)` location if this was `pip install`ed rather than
run in place).

## Targets

Exactly one of these, always:

- **`--ssh HOST`** — shorthand for `--cmd "ssh -tt HOST"`. `--ssh-arg` adds
  extra `ssh` args (repeatable) — e.g. `--ssh-arg=-o --ssh-arg=BatchMode=yes`
  for a non-interactive auth check, or `--ssh-arg=-o --ssh-arg=ConnectTimeout=5`
  (the `=` form is required: argparse reads a bare `-o` as the next option).
  Only valid alongside `--ssh`. Verified end to end over a real ssh
  transport — both loopback and a genuinely different machine (see
  NOTES.md for the ssh-specific gotchas: latency, pacing, interrupt
  delivery).
- **`--cmd COMMAND`** — any command, shell-quoted as one string and split
  with `shlex.split` (so quote it the way you'd type it: `--cmd 'bash -i'`,
  `--cmd 'tmux attach -t work'`, `--cmd 'python3 -i'`). Runs local to
  wherever `terminal-puppeteer.py` itself runs — for a different host
  that isn't SSH-reachable in the usual way, that's on the command you
  pass, not something this tool special-cases.

Shell-agnostic by construction — `--cmd` execs whatever `argv` you give
it under a pty, nothing bash-specific anywhere in this tool. `--cmd 'zsh
-i'`, `--cmd 'fish'`, `--cmd 'tmux new -A -s work'` all work the same
way `--cmd 'bash -i'` does. The target runs in its own session with the
pty as its controlling terminal, so job control works like a real
terminal: `CTRL-C` delivers SIGINT to the foreground job, CTRL-Z/`fg`
work, and interactive shells start without job-control warnings. The
pty also has a real terminal size (24 rows × 80 columns), so `stty`,
`$COLUMNS`/`$LINES`, and size-aware rendering match a fresh terminal
instead of a zero-sized one. (`zsh
-i` verified directly; see NOTES.md's live-redraw gotcha for what its
captures look like. `fish` verified on real hardware too —
[issue #37](https://github.com/NireBryce/terminal-puppeteer/issues/37)
— with the caveat that it needs `--answer-queries` (below) to get past
its startup capability probing, and `WAIT-FOR` on a prompt marker to
pace past that probing instead of typing into it.)

## Steps

Everything after the target is a flat list of **steps**, applied in order.
A step is either literal text, sent to the pty exactly as written with no
added newline, or one of a closed set of named keys (case-insensitive):

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

`--list-keys` prints this same table (computed from the same table the
tool itself uses, not transcribed separately) and exits — the exact
bytes without a trip back to this file, plus the other step forms below.
`CTRL-`*letter* isn't a fixed
list: any letter A–Z works, not just the handful (`CTRL-C`/`CTRL-D`)
that used to be hardcoded — `CTRL-U`, `CTRL-L`, `CTRL-W`, `CTRL-A` all
work the same way.

A step that doesn't match any known key is still sent as literal text,
same as ever — but if it's *close* to one (`CTRLC` missing its hyphen,
`ENTR` a typo) a warning goes to stderr saying so. It's advisory only:
the step still goes out literally either way, this just flags the case
that's more likely a mistake than intended text. A step spelled like a
typed command — lowercase `sleep 30`/`wait 30` — is a *recognized*
driver-side wait that sends nothing, and gets its own advisory note
pointing at the `RAW:` form that types it for real.

More step forms cover what named keys can't express:

- **`RAW:<escapes>`** — bytes through a `bind`-style escape parser, sent
  as one write: `'RAW:\e[I'` sends the focus-in sequence `\e[I` as a
  single step. An `ESC` step followed by `'[I'` text is two writes, and
  the gap between them gives the target's escape-sequence parser a
  window to time out and discard the partial sequence (ble.sh's decoder
  does exactly that). Understands `\e`, `\C-x` (any letter, plus
  `@[\]^_`), `\xHH`, `\a \b \t \n \v \f \r`, `\\`; anything else stays
  literal. The explicit prefix is the point — a plain step was never
  supposed to grow escape syntax, so text that merely starts with a
  backslash keeps meaning what it always meant.
- **`PASTE:<text>`** — the text wrapped in real bracketed-paste framing
  (`\e[200~` … `\e[201~`), sent as one write, with the same
  `bind`-style escapes `RAW:` understands. The way to express a
  multi-line paste in one step: a line editor with bracketed-paste mode
  enabled treats the whole payload as one paste and holds its newlines
  literal, instead of executing each line as it arrives (what a plain
  multi-line write does — see NOTES.md's bulk-paste gotcha). Against a
  target that never enabled bracketed-paste mode, the framing bytes
  usually arrive as ignored noise and the payload types in plainly.
- **`SLEEP <seconds>`** (or `WAIT <seconds>`) — a driver-side pause:
  sends nothing, just keeps draining for N seconds. Unlike a
  target-side `'sleep 5' ENTER` workaround, it puts nothing in the
  session under test — no history entry, no `PROMPT_COMMAND`/`precmd`
  hooks firing mid-observation, no transcript noise — and it works for
  non-shell targets. `--step-delay` retimes every step; this retimes
  one.
- **`RESIZE <rows> <cols>`** — a driver-side resize of the pty
  mid-run: the kernel delivers SIGWINCH to the target's foreground
  process group, the same delivery a real terminal's resize produces.
  `--rows`/`--cols` (below) fix the size the pty *starts* with; this
  changes it once the session is live — for repros that only misbehave
  after a resize, or that need a narrow wrap applied mid-line.
- **`WAIT-FOR:<pattern>`** — drain until the ANSI-stripped output
  accumulated so far matches: a literal substring, or a regex when
  wrapped in `/.../`. Paces on the target's actual output instead of a
  duration somebody measured once, and the regex form is what survives
  a line editor redrawing mid-marker (cursor-positioning escapes
  landing between the characters of the thing being waited for —
  `'WAIT-FOR:/===.?.MARK.?.===/'` still matches where the literal never
  appears contiguously). Matching the *stripped* stream is deliberate:
  matching raw bytes is where the interleaving problem lives. A pattern
  that never arrives fails the run — exit 1, one stderr line naming the
  step and the last output seen — not a slow no-op; the budget is
  `--wait-for-timeout` (default 10s). One honest caveat: matching is
  against the transcript, which includes the echo of your own typing —
  wait for a marker that only exists in the target's *output*, not one
  you also typed.
- **`WAIT-FOR-NEXT:<pattern>`** — the same matching, same `/.../`
  regex form, same timeout — but only against output arriving *after
  the step starts*. The echo of your own typing and a prompt that
  already contained the marker cannot satisfy it, which is exactly the
  caveat above, fixed by form: `WAIT-FOR-NEXT:done-42` after typing
  `done-42` waits for the *target* to say it. Honest boundary caveat:
  "after" is a length measured on the stripped stream, and one escape
  sequence can straddle it, so it's exact to within about one escape
  sequence at the boundary.

**No step ever gets an implicit trailing newline.** That's what lets a step
be a bare `TAB` or a partial line someone's about to keep typing — but it
also means a command you want to actually run needs its own explicit
`ENTER` step, or it just sits typed and unsubmitted for the rest of the
run. `'git status' ENTER`, not `'git status'` alone.

`--steps-file PATH` appends more steps, one per line, on top of any given
on the command line — blank lines skipped, `#`-prefixed lines are
comments, `PATH` of `-` reads from stdin instead of a file. Useful once a
repro has more steps than are comfortable as shell arguments, or needs to
be versioned/reused.

## Environment

`--env KEY=VALUE` (repeatable) sets an extra environment variable for the
target process, merged onto the inherited environment (not replacing it
— `PATH` and everything else the target would normally see is still
there). Most useful for something that has to be true *before* the
target starts, which a step can't guarantee: `--env TERM=dumb` avoids
some terminal-capability probing that a program checking `$TERM` might
otherwise do on startup (see NOTES.md's ble.sh gotcha) — a `'export
TERM=dumb' ENTER` step sets the same variable, but only after the shell
is already up and reading input, racing whatever probe happens first.

## Timing

Every step is followed by up to `--step-delay` seconds (default `1.5`) of
draining pty output before the next one is sent; `--connect-wait` (default
`3.0`) is the same wait before the *first* step, to let a login banner or
shell prompt show up; `--settle` (default `1.5`) is one more wait after
the *last* step, for anything still catching up. All three stop early once
output goes quiet for 200ms, so raising them costs nothing when the target
responds fast — the defaults are there for the slow case.

Raise `--step-delay`/`--settle` when the output looks truncated or the
next step's echo appears mashed into the previous step's output; that's
usually the delay running out before the target actually finished
responding, not a real ordering bug. The three forms compose: a step
list reads `'setup' ENTER SLEEP 5 'the probe' ENTER
WAIT-FOR:the-answer` — `SLEEP` for settle-time pacing, `WAIT-FOR` for
reactive pacing, `--step-delay` for typing cadence.

## Exit behavior

On its way out this **only ever terminates the target process** (SIGTERM,
then SIGKILL after 5 seconds if it's still alive) — it never types
anything to close it. There's no single "graceful quit" keystroke that's
correct across every target (`exit`+`ENTER` for a shell, `CTRL-D` for a
REPL, a whole different key for `tmux`), so this doesn't guess. Include
whatever quit step actually applies to your target as the last entry in
your own step list if a clean logout matters for what you're checking.

If the target exits on its own **while steps remain to be sent** — a
crash, an unwelcome startup, a typo'd target — the run fails loudly:
exit 1, the partial capture still on stdout (it's the diagnostic,
usually the target's dying words), and one stderr line naming the exit
status or signal and what was in progress. A death between steps names
how many steps were never sent and which step would have been next; a
death during a `WAIT-FOR` names the pattern that can no longer arrive.
A target that dies *after* the last step — `exit`+`ENTER`
as the final step, the clean-logout shape — is not an error. This check
exists because a dead target used to be invisible: the pty echoes every
step whether or not anything is reading them, so a target that died at
startup looked exactly like a quiet, successful run
([issue #37](https://github.com/NireBryce/terminal-puppeteer/issues/37)).

## Other flags

`--rows`/`--cols` override the terminal size the pty reports to the
target (default 24 × 80, a fresh terminal's — see the `--cmd` section
for why it isn't 0×0). Useful when a repro is size-sensitive: a TUI
that only misrenders below N columns, an editor's wrapping, a
progress-bar width. `RESIZE <rows> <cols>` (Steps, above) changes that
size mid-run.

`--capture-out FILE` also writes the raw, unstripped pty capture to a
file — `--raw` changes stdout's rendering only, and the file always gets
the bytes exactly as they came off the pty. Written on every exit path:
a run that fails mid-way (target death, WAIT-FOR timeout) still leaves
the partial capture in the file, which is the diagnostic. For byte-exact
work (`od`-level checks, or feeding a capture to another tool) this
beats scraping stdout.

`--verbose`/`-v` prints each step to stderr as it's sent (`>> TAB`), for
watching a long or slow multi-step run happen live instead of waiting
for the whole transcript to print at the end — output on stdout is
unaffected either way.

`--answer-queries` answers terminal capability queries (device
attributes, cursor position, XTVERSION, kitty-keyboard protocol, OSC 11
background color, termcap strings — the set verified against a real
probe-heavy target, fish 4.8) with canned "normal terminal" replies
while the run is happening, answering every occurrence: a line editor
re-queries on every prompt redraw, and a real terminal answers every
time. A
bare pty answers nothing; that's usually fine, but a target that
probes-then-fallbacks can take a genuinely different code path with no
reply — so a repro can silently fail to reproduce behavior a real
terminal shows, and the failure looks like a bug in the target. Opt-in,
because the replies are canned lies: a target whose behavior legitimately
differs by reported capability will be misled. Default stays
exactly-as-before (queries go unanswered; see NOTES.md's gotcha for what
that looks like in a capture). This is not a terminal emulator — it
answers just enough for a probe-then-fallback target to take its normal
path.

`--version` prints the version and exits.

## See also

`NOTES.md` — gotchas found by actually using this against a real bug (a
ble.sh/carapace interaction on a remote host), worth reading before
trusting a confusing capture: what a synthetic pty does and doesn't
emulate, why a byte that looks empty in the output might not be, and how
this tool's own diagnostic commands can get caught by the very bug being
investigated.

`DEVLOG.md` — terse, reverse-chronological record of what changed in
this project and why, for picking the work back up without re-reading
the whole git log.

`man/terminal-puppeteer.1` — the same reference as this README in
`man(1)` form: `man ./man/terminal-puppeteer.1` to read it in place.

`CONTRIBUTORS.md` — who can contribute and how to file an issue or PR.

`STYLEGUIDE.md` — a style guide for LLM-originating submissions
specifically.

"Alternatives" below — other tools doing the same kind of pty-driving,
most of them more mature than this one.

`flake.nix`/`package.nix`/`default.nix` — the Nix packaging behind the
"Nix:" line above. `flake.nix` is the real definition (outputs:
`packages`, `apps` for `nix run`, `devShells`); `package.nix` is the
actual derivation, factored out so `default.nix`'s `flake-compat` shim
can build the same thing for `nix-build` without flakes, without
duplicating the package definition itself.

## Alternatives

This is a personal debugging tool, extracted the same day it's dated
below — not the first or most mature entry in this space. Several other
tools take the same "wrap the target in a real pty, let an agent send
keys and read output" approach, and most of them are older, more
established, and more actively maintained:

- [tui-use](https://github.com/onesuper/tui-use) (257★) — closest in
  spirit to this tool: spawn a program in a pty, read the screen as
  plain text, send keystrokes from the command line.
- [clsh](https://github.com/my-claude-utils/clsh) (523★) — real pty
  (zsh/bash/vim/tmux, full colors) reachable from any device; more
  aimed at remote terminal access than one-shot bug repro, but built on
  the same idea.
- [agent-tui](https://github.com/pproenca/agent-tui) (113★) —
  screenshot-based TUI automation for agents, actively maintained.
- [interminai](https://github.com/mstsirkin/interminai) (56★) — a pty
  proxy specifically for full-screen programs (`vim`, `git rebase -i`,
  `gdb`) via screen capture rather than a flat byte stream.
- [hty](https://hty.sh/) — a *persistent* pty session an agent reads and
  types into like a human, rather than this tool's one-shot-and-exit
  model.
- [agent-tty](https://github.com/coder/agent-tty) (26★, Coder) —
  `node-pty`-backed sessions with an append-only event log; built for
  driving/inspecting/recording sessions, not just single repros.

Two more exist in the same space but are narrower or less established,
noted honestly rather than folded into the "more mature" list above:
[agent-terminal](https://github.com/jasonkneen/agent-terminal) (12★, a
single day of commits back in January) and
[claude-pty-wrapper](https://github.com/kcosr/claude-pty-wrapper) (31★)
which wraps interactive Claude itself rather than an arbitrary target,
so it isn't really solving the same problem as this tool.

The main design difference between this tool and most of the above:
they read the *rendered screen* (cursor-aware, TUI-style), which is the
right model for a full-screen program like `vim` or a debugger. This
tool reads a flat byte stream instead, which fits its actual use case —
line-oriented shell/REPL sessions — but buys stdlib-only simplicity at
the cost of screen/cursor fidelity. If your target is genuinely
full-screen, one of the screen-capture tools above is probably a better
fit than this one.

## History

Extracted from `NireBryce/nixos-configs` (`flake/scripts/ssh-pty-drive.py`,
built there 2026-08-24 to chase a ble.sh/carapace completion bug — see that
repo's `claude cave/lessons-learned.md` #39 and
[issue #72](https://github.com/NireBryce/nixos-configs/issues/72)),
generalized here beyond SSH and renamed from `pty-drive` to something
self-evident without needing to know what a pty is.
