# Notes from actually using this

Written while the context was fresh, from the session that used this tool
(as `ssh-pty-drive.py`, its name at the time) to chase a real bug: a
ble.sh/carapace interaction that corrupted a `read` call under fast
typing. `README.md` is what the tool does; this is what using it is
actually like, and where it can mislead you if you don't know these.

## The pty has no terminal behind it — query escapes go unanswered

A real terminal emulator answers escape sequences the program under it
sends to probe capabilities: cursor position reports (`\e[6n`), device
attribute queries (`\e[>c`), and more. `pty.openpty()` gives you a real
pty *device*, but nothing is listening on the other end to answer those —
this tool just reads whatever bytes come out and writes whatever
keystrokes you send. A program that queries the terminal on startup (ble.sh
does, heavily) sends those queries, gets no reply, and the raw escape
bytes end up sitting in the captured output looking like corruption:

```
    [>c[▽][▶][龼][鿄][ㆸ][ힰ][゙][鿍]...
```

That block at the start of a capture against ble.sh is *normal* — it's
ble.sh's own terminal probing failing to get answered, not a bug in
whatever you're actually testing. Don't chase it. (`--screen` renders
it as what it is — the literal bracketed glyphs, with the invisible
query gone — which is usually enough to stop it looking like
corruption at a glance.) The program still runs
correctly underneath; it just can't detect terminal capabilities it would
otherwise use to render more precisely. "Runs correctly underneath" has a
real limit, though: a target that probes-then-fallbacks can take a
genuinely different *code path* with no reply, so a repro run here can
silently fail to reproduce what a real terminal shows — verified the
expensive way: a ble.sh keybinding that applied in konsole did not apply
through a bare pty, and every negative result from that harness was an
artifact until a small canned-reply responder was added mid-drain, at
which point it matched the real terminal byte for byte. That responder is
now built in as `--answer-queries` (opt-in — the replies are canned lies
about terminal capabilities, and the default stays exactly as described
here). It answers on every occurrence, not once per run — a line editor
re-sends its capability queries on every prompt redraw, and a real
terminal answers each time; fish 4.x treated an unanswered repeat as a
fallback trigger, timing out, redrawing, and *flushing its input
buffer*, which ate keystrokes typed into that window. The responder
covers the query set verified against fish 4.8.1 (DA1/DA2, cursor
position, XTVERSION, kitty-keyboard, OSC 11 background color, DCS
termcap) — if a target probes something exotic, it still goes
unanswered. If a target hangs waiting
indefinitely on one of these instead of timing out and continuing, that's
a real limitation of this approach — there's no cheap general fix short of
implementing a real terminal-query responder, which is more machinery than
most repros need (the flag above is deliberately not that; it answers just
enough for a probe-then-fallback target to take its normal path). Setting
`TERM=dumb` (or another minimal value) avoids
some of this for programs that check `$TERM` before deciding whether to
probe at all -- `--env TERM=dumb` is the race-free way to do it (in the
target's environment from the moment it starts, before it's read a
single byte), preferred over setting it as an early step (`'export
TERM=dumb' ENTER`), which still races whatever probing happens before
that step lands.

## The target now gets a real controlling terminal — and a dead target no longer masquerades

The pty used to be second-class in one more way: the target was spawned
onto it without `setsid()`, so the pty was never the target's
*controlling terminal* and job control never worked. bash and zsh
printed `cannot set terminal process group` warnings at startup and ran
degraded (no SIGINT to foreground jobs, no CTRL-Z/`fg`) — easy to
dismiss as cosmetic. fish was the honest one: it exits outright, rc=1,
before drawing a prompt ([issue #37](https://github.com/NireBryce/terminal-puppeteer/issues/37),
verified on real hardware — the original sandbox attribution was
wrong). The tool now puts the target in its own session with the pty as
its controlling terminal, so `CTRL-C` is a real SIGINT to the
foreground job, CTRL-Z/`fg` work, and fish runs like any other shell
(given `--answer-queries`, per the gotcha above — without it fish hangs
before its first prompt, and a `WAIT-FOR` on the prompt turns that into
a loud failure).

The masquerade that made this hard to see is also fixed: the pty's
kernel line discipline echoes every step you send whether or not the
target is alive, so fish's refusal used to look like a quiet successful
run — every command "echoed", nothing executed, exit 0. The tool now
checks the target before each step and **fails loudly if it died with
steps un-sent**: exit 1, the partial capture still on stdout (the
dying words are the diagnostic), one stderr line naming the exit
status, the steps never sent, and the next step's name. Dying *after*
the last step (`'exit' ENTER` as the finale) stays a normal exit-0
run. The echo-vs-output distinction is still worth internalizing when
you read any capture: a step's echo tells you what was *typed*; only
the target's own output tells you anything *executed*.

## A fancy line editor's own redraws look like typos in a flat capture

Not specific to bash/ble.sh — any shell with a live, per-keystroke line
editor does this, and it's worth knowing before assuming a capture shows
a real typo. Driving `zsh -i` (with zsh's own syntax-highlighting/
autosuggestion zle widgets active, the common default) and sending the
step `'echo hi' ENTER`, the captured line reads:

```
eecho hi
```

Not a typo in the step, and not this tool double-sending a character.
`--raw` shows what's actually happening: `e`, then a bare backspace
(`\x08`), then `echo hi` — zsh's line editor redrew the line after the
first keystroke (to (re)apply syntax highlighting), the way it would on
every real terminal, all the time, completely invisibly, because a real
terminal *processes* backspace as a cursor movement and overwrite. This
tool doesn't emulate a terminal — it just captures the byte stream as
text — so a redraw sequence that a human would never consciously notice
comes out looking like a corrupted duplicate character instead. The
command itself still ran correctly underneath (`hi` printed on the next
line); only the flat-text rendering of the *typing itself* is
misleading. Same root cause as the ble.sh terminal-probing gotcha above
(no real terminal to make live editor behavior legible), different
symptom — expect it from any shell/line-editor combo that redraws as
you type, not just ble.sh under bash. `--screen` is the fix for
*reading* these captures: it processes the backspace the way a terminal
does, and `eecho hi` renders as the `echo hi` it always was.

## A byte that looks empty in the output might not be

The bug this tool was built to chase turned out to hinge on a single SOH
(0x01) control byte inside a shell variable, sandwiched between two single
quotes: `IFS='<SOH>' read ...`. In every capture from this tool — and in
plain `echo`/`type`/`grep` output generally — that byte is invisible, so
it reads as `IFS=''`, a literally empty string. It isn't. This cost real
time: the diagnosis was built on "it's an empty IFS" for a while before
`od -c` on the actual bytes caught the mistake.

**When a captured value matters at the byte level — not just "does this
text roughly match" — pipe it through `od -c` (or write it to a file on
the target and fetch that separately) instead of trusting what the
terminal rendered.** This tool strips ANSI escapes by default for
readability, which makes this worse, not better, for control bytes that
aren't part of an ANSI sequence — `--raw` doesn't help either, since a
raw SOH is still invisible, just no longer processed at all. There's no
flag that fixes this; it's a rendering problem, not a capture problem,
and it needs a different tool (`od`, `xxd`, a hexdump on the target) for
the part that actually needs byte-exactness. `--screen` sits even
further from the bytes: it renders *layout*, so invisible-and
byte-questionable input disappears from its view entirely — by design,
not as a bug.

## Forgetting the ENTER step is the single most common mistake

Covered in `README.md`'s Steps section as a design decision; worth
repeating here as the mistake, because it's easy to make even knowing the
rule. `'git status'` alone types the text and stops — nothing runs, and
the capture just shows the typed-but-unsubmitted command sitting at the
prompt. It looks like the target ignored the command; it didn't, the
command was never submitted. Every step list that's supposed to *run*
something needs its own `ENTER` (or whatever submits input for that
target) after it.

## Bulk-writing a whole multi-line string is not the same as typing it

Also in `README.md`, worth the concrete failure mode: an early prototype
of this tool took a whole shell function definition as one step and wrote
it in one `os.write()`. Without bracketed-paste framing, the target shell
saw each embedded newline as "run whatever's on this line right now," so
a multi-line `function foo { ... }` arrived as several syntactically
broken partial commands — `unexpected end of file`, `unexpected token`,
that kind of failure, not an obvious "you sent this wrong." If a repro
needs to define something multi-line, either use the `PASTE:` step (next
section), or write it to a file first (a separate step, or out-of-band
before running this tool at all) and use a single step that sources it —
`source /path/to/file.sh` — rather than typing the file's contents as
steps.

## `PASTE:` is the honest way to hand a line editor a multi-line blob

The framing the bulk-paste gotcha above is missing. `PASTE:<text>` wraps
the payload in the real bracketed-paste markers (`\e[200~` … `\e[201~`)
and sends the whole thing as one write — so a line editor that has
*enabled bracketed-paste mode* (readline, zle, ble.sh all do; you can
watch for the `\e[?2004h` they send at prompt time) treats the payload
as one paste instead of a burst of keystrokes: embedded newlines sit
literal in the edit buffer instead of submitting each line early, and
the editor skips the per-keystroke machinery — completion triggers,
autosuggestions, syntax-highlight redraws — that typing the same
characters one step at a time would set off. For repros that need a
multi-line definition *inside* the session under test, this is both the
correct-shape way and the quiet way; the payload takes the same
`bind`-style escapes as `RAW:` (`\e`, `\t`, `\xHH`, …), so tabs and
escapes inside the blob survive. Two honest edges: a target that never
enabled bracketed-paste mode just sees stray framing bytes (usually
ignored) and the payload typed in plainly — newlines submitting — so
`PASTE:` is not magic against a target that doesn't speak it; and
remember `--steps-file` strips surrounding whitespace from each line, so
a payload whose trailing spaces matter should come from a positional
argument, which is preserved exactly.

## The bug you're chasing can catch your own diagnostic commands too

This is specific to bugs that intercept or corrupt *input processing
itself* (this one shadowed the `read` builtin), but it's worth knowing
going in: if you use steps to run diagnostic commands that redefine
functions, source files, or otherwise do nontrivial work in the same live
session where the bug is active, those commands can get caught by the
bug too. In this session, an attempt to monkey-patch a function via a
`source`+`eval` step, sent as one step containing a function name with
`/` and `#` in it, itself tripped the same `read`-interception bug being
diagnosed, and produced a confusing secondary error that took a moment to
recognize as "the tool's own diagnostic broke," not "a new bug." When a
repro session is investigating input corruption specifically, prefer the
simplest possible steps (plain commands, one thing per step) over
compound one-liners with heavy quoting, and consider writing anything
complex to a file on the target first and sourcing it, rather than typing
it live.

## A credential typed as a step is transcript content

Everything a step contains ends up in the record: `-v` echoes every
step to stderr as it is sent, the capture (stdout, and any
`--capture-out` file) contains the pty's echo of it, positional steps
sit in your own shell history as argv, and `--steps-file` paths land
in whatever is versioned. A credential pasted as a step — `export
API_TOKEN=…` ENTER — is now in scrollback, history, and any capture
file, forever, in plaintext. `--screen` doesn't help (it renders the
same session), and a future redaction flag (backlog) wouldn't help
either: the secret already crossed the pty to the target — that's
what typing it there *means*.

The better shape is for no step to ever contain the secret: hand the
*target* what it needs out-of-band, so the tool only ever drives
already-authenticated sessions. In rough order of preference:

- **Inherited environment.** The tool passes the launcher's full
  environment through to the target, so a secret exported in the
  shell (or devShell) that launches this tool reaches the target
  without any step containing it — and without the agent running the
  tool ever seeing the plaintext. (`--env` exists for *non-secret*
  additions; its values sit in the tool's own argv, visible in `ps`
  to other local users — don't put secrets there.)
- **Target-side state.** A provisioned config, keyfile, or profile on
  the target machine — the repro then just *uses* the session like a
  human who is already logged in would.
- **Encrypted at rest, exposed out-of-band** (sops, agenix, your
  deploy secret manager) for secrets that have to live anywhere near
  a workflow. This tool deliberately ships no vault and no nudge
  mechanism beyond this paragraph: keep your secrets in *your*
  encrypted-at-rest store and keep them out of your steps.

For byte-level debugging the capture stays faithful by design — which
is exactly why the fix belongs before the step list, not after the
capture.

## Fetch large or exact captures out-of-band when you can

For anything beyond a few lines, or anything where exact bytes matter,
it's more reliable to have a step redirect output to a file on the target
(`declare -f _some_function > /tmp/out.txt`), then fetch that file with a
plain `ssh host cat /tmp/out.txt` (or equivalent) outside this tool
entirely, than to try to parse it back out of a `terminal-puppeteer.py`
capture. The capture is a terminal transcript — prompts, echoed input,
ANSI noise even after stripping, wrapped/redrawn lines from ble.sh-style
line editors — mixed in with the actual content you want. A plain file
fetch has none of that.

## Tune the delays before assuming a negative result

If a repro depends on something slow (a network call inside a completion
function, a background job, disk I/O), the default `--step-delay`/
`--settle` (1.5s each) may cut off before the effect you're checking for
actually happens. A clean-looking capture with no error in it isn't
necessarily proof the bug doesn't reproduce — it might just mean the
window closed before the bug had a chance to fire. Raise the delays (or
add an explicit step like `'sleep 3' ENTER` before the thing you actually
care about) before concluding a negative result is real.

## Over ssh, everything here happens one tty further away

The `--ssh` path (`ssh -tt`) is verified end to end over a real ssh
transport — commands execute, the 24x80 window propagates to the remote
pty (a client with *no* tty on its side gets a 0x0 remote pty: `stty
size` answers `0 0`, so don't diagnose window-size bugs through a bare
ssh one-liner), job control works (`echo $-` shows the `m`), and CTRL-C
kills a remote foreground job (it reports status 130). But latency
multiplies every timing gotcha above, and two new ones showed up during
that verification:

- **CTRL-C delivery is racy.** The `^C` echo always appears (the byte
  reached the remote pty), but the SIGINT sometimes lands late enough
  that the queued follow-up steps sit un-executed past any reasonable
  `--settle`. Retry before concluding the interrupt doesn't work — the
  same repro run back-to-back can fail then pass.
- **Pace on the remote's output, not on delays.** The login-shell case
  (`--ssh host` with dotfiles active) has the ble.sh-style redraw storm
  one connection away, and typed-ahead bytes vanish during redraws
  exactly as they do locally (the fish lesson, over the network).

## `--answer-queries` is a fish fix, not a ble.sh fix

With ble.sh as the target (any recent bash setup with
`ble.sh` in `.bashrc`), `--answer-queries` makes things *worse*: the
canned cursor-position replies arrive after ble.sh's glyph-measuring
probe has moved on, land as unrequested input while the prompt is idle,
and drive endless prompt redraws — each redraw flushing typed steps.
Left unanswered, ble.sh's own probe storm (one burst of ~20
cursor-position probes) settles by itself in about two seconds and the
prompt goes quiet. So: unanswered beats mis-answered for ble.sh; the
flag stays what 0.3.1 made it — the thing that fixes fish, opt-in
because the replies are canned lies.

## A step named `sleep N` or `wait N` is a driver wait, not a typed command

`SLEEP <seconds>`/`WAIT <seconds>` is matched case-insensitively, so the
step `sleep 30` makes *the driver* wait 30 seconds and sends nothing —
typing a literal `sleep 30` command to the target silently does nothing
for 30 seconds, then sends an empty line (verified the embarrassing way:
a "CTRL-C doesn't interrupt sleep" repro where the sleep was never sent
at all, and `$?` read `0` because the ENTER had typed an empty line).
Use `RAW:sleep 30\r` to actually type it — one write, which is also the
right shape for the target's escape-sequence parser anyway.

## A WAIT-FOR pattern can match your own typed echo

The pty echoes every step as it's typed, and `WAIT-FOR` matches the
stripped stream — which includes those echoes. `echo done-$?` followed
by `WAIT-FOR:done-` matches the *echo of the command*, not its output,
and the run succeeds before the command has run. Pick markers that
exist only in output: either a string the input can't contain
(`WAIT-FOR` on `/done-1[0-9][0-9]/` when the input says `done-$?`), or
assert on the final capture instead of pacing on it.

