# terminal-puppeteer

Lets an LLM agent drive a real interactive program through a pty —
sending actual keystrokes with delays between them, and reading back what
the program actually did — instead of the one-shot, no-tty view a normal
tool call gets.

Built for reproducing bugs that only exist in a live, human-typed terminal
session: `$-` not containing `i` because no pty was allocated, a
completion/suggestion race that only fires while keys are still landing, a
program that behaves differently piped than attached to a real terminal.
An agent has no terminal of its own to hit those with — this is the
stand-in.

Stdlib only (`pty`, `subprocess`, `select`), no dependencies, one file.

## Provenance

Written and maintained largely by Claude Code agents; the human behind
this repo is not reading every line before it lands. That's fine for the
personal debugging use case this exists for, but it means the review
here is agent-review, not human-review — read the code yourself before
pointing it at anything sensitive.

## Quick start

```sh
# over SSH -- the case this was built for
./terminal-puppeteer.py --ssh myhost 'git co' TAB CTRL-C

# any local interactive program, not just a remote shell
./terminal-puppeteer.py --cmd 'bash -i' 'echo $-' ENTER
./terminal-puppeteer.py --cmd 'tmux attach -t work' 'echo hi' ENTER

# a scripted multi-step repro, read from a file instead of argv
./terminal-puppeteer.py --ssh myhost --steps-file repro.txt
```

Output is everything the pty produced, ANSI escape sequences stripped by
default (`--raw` to keep them) — read it the way you'd read a terminal
transcript, in order, prompt lines and command output interleaved.

Full reference: `--help`, or `man ./man/terminal-puppeteer.1` (installed
to the usual `man(1)` location if this was `pip install`ed rather than
run in place).

## Targets

Exactly one of these, always:

- **`--ssh HOST`** — shorthand for `--cmd "ssh -tt HOST"`. `--ssh-arg` adds
  extra `ssh` args (repeatable) — e.g. `--ssh-arg -o --ssh-arg BatchMode=yes`
  for a non-interactive auth check, or `--ssh-arg -o --ssh-arg
  ConnectTimeout=5`. Only valid alongside `--ssh`.
- **`--cmd COMMAND`** — any command, shell-quoted as one string and split
  with `shlex.split` (so quote it the way you'd type it: `--cmd 'bash -i'`,
  `--cmd 'tmux attach -t work'`, `--cmd 'python3 -i'`). Runs local to
  wherever `terminal-puppeteer.py` itself runs — for a different host
  that isn't SSH-reachable in the usual way, that's on the command you
  pass, not something this tool special-cases.

## Steps

Everything after the target is a flat list of **steps**, applied in order.
A step is either literal text, sent to the pty exactly as written with no
added newline, or one of a closed set of named keys (case-insensitive):

| name | sends |
|---|---|
| `TAB` | `\t` |
| `ENTER`, `RET`, `CR` | `\r` |
| `NEWLINE` | `\n` |
| `CTRL-C` | `\x03` |
| `CTRL-D` | `\x04` |
| `CTRL-R` | `\x12` |
| `ESC` | `\x1b` |
| `BACKSPACE` | `\x7f` |

**No step ever gets an implicit trailing newline.** That's what lets a step
be a bare `TAB` or a partial line someone's about to keep typing — but it
also means a command you want to actually run needs its own explicit
`ENTER` step, or it just sits typed and unsubmitted for the rest of the
run. `'git status' ENTER`, not `'git status'` alone.

`--steps-file PATH` appends more steps, one per line, on top of any given
on the command line — blank lines skipped, `#`-prefixed lines are
comments. Useful once a repro has more steps than are comfortable as shell
arguments, or needs to be versioned/reused.

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
responding, not a real ordering bug.

## Exit behavior

On its way out this **only ever terminates the target process** (SIGTERM,
then SIGKILL after 5 seconds if it's still alive) — it never types
anything to close it. There's no single "graceful quit" keystroke that's
correct across every target (`exit`+`ENTER` for a shell, `CTRL-D` for a
REPL, a whole different key for `tmux`), so this doesn't guess. Include
whatever quit step actually applies to your target as the last entry in
your own step list if a clean logout matters for what you're checking.

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

## History

Extracted from `NireBryce/nixos-configs` (`flake/scripts/ssh-pty-drive.py`,
built there 2026-08-24 to chase a ble.sh/carapace completion bug — see that
repo's `claude cave/lessons-learned.md` #39 and
[issue #72](https://github.com/NireBryce/nixos-configs/issues/72)),
generalized here beyond SSH and renamed from `pty-drive` to something
self-evident without needing to know what a pty is.
