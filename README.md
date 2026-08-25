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

Shell-agnostic by construction — `--cmd` execs whatever `argv` you give
it under a pty, nothing bash-specific anywhere in this tool. `--cmd 'zsh
-i'`, `--cmd 'fish'`, `--cmd 'tmux new -A -s work'` all work the same
way `--cmd 'bash -i'` does. (`zsh -i` verified directly; see NOTES.md's
live-redraw gotcha for what its captures look like. `fish` couldn't be
verified from the sandbox this was written in — see
[issue #37](https://github.com/NireBryce/terminal-puppeteer/issues/37) —
but nothing in this tool is shell-specific, so there's no design reason
to expect it to behave differently from any other target.)

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
bytes without a trip back to this file. `CTRL-`*letter* isn't a fixed
list: any letter A–Z works, not just the handful (`CTRL-C`/`CTRL-D`)
that used to be hardcoded — `CTRL-U`, `CTRL-L`, `CTRL-W`, `CTRL-A` all
work the same way.

A step that doesn't match any known key is still sent as literal text,
same as ever — but if it's *close* to one (`CTRLC` missing its hyphen,
`ENTR` a typo) a warning goes to stderr saying so. It's advisory only:
the step still goes out literally either way, this just flags the case
that's more likely a mistake than intended text.

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
responding, not a real ordering bug.

## Exit behavior

On its way out this **only ever terminates the target process** (SIGTERM,
then SIGKILL after 5 seconds if it's still alive) — it never types
anything to close it. There's no single "graceful quit" keystroke that's
correct across every target (`exit`+`ENTER` for a shell, `CTRL-D` for a
REPL, a whole different key for `tmux`), so this doesn't guess. Include
whatever quit step actually applies to your target as the last entry in
your own step list if a clean logout matters for what you're checking.

## Other flags

`--verbose`/`-v` prints each step to stderr as it's sent (`>> TAB`), for
watching a long or slow multi-step run happen live instead of waiting
for the whole transcript to print at the end — output on stdout is
unaffected either way. `--version` prints the version and exits.

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

`flake.nix`/`package.nix`/`default.nix` — the Nix packaging behind the
"Nix:" line above. `flake.nix` is the real definition (outputs:
`packages`, `apps` for `nix run`, `devShells`); `package.nix` is the
actual derivation, factored out so `default.nix`'s `flake-compat` shim
can build the same thing for `nix-build` without flakes, without
duplicating the package definition itself.

## History

Extracted from `NireBryce/nixos-configs` (`flake/scripts/ssh-pty-drive.py`,
built there 2026-08-24 to chase a ble.sh/carapace completion bug — see that
repo's `claude cave/lessons-learned.md` #39 and
[issue #72](https://github.com/NireBryce/nixos-configs/issues/72)),
generalized here beyond SSH and renamed from `pty-drive` to something
self-evident without needing to know what a pty is.
