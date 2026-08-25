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
whatever you're actually testing. Don't chase it. The program still runs
correctly underneath; it just can't detect terminal capabilities it would
otherwise use to render more precisely. If a target hangs waiting
indefinitely on one of these instead of timing out and continuing, that's
a real limitation of this approach — there's no cheap general fix short of
implementing a real terminal-query responder, which is more machinery than
most repros need. Setting `TERM=dumb` (or another minimal value) avoids
some of this for programs that check `$TERM` before deciding whether to
probe at all -- `--env TERM=dumb` is the race-free way to do it (in the
target's environment from the moment it starts, before it's read a
single byte), preferred over setting it as an early step (`'export
TERM=dumb' ENTER`), which still races whatever probing happens before
that step lands.

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
you type, not just ble.sh under bash.

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
aren't part of an ANSI sequence — `--raw` doesn't help either, since a raw
SOH is still invisible, just no longer processed at all. There's no flag
that fixes this; it's a rendering problem, not a capture problem, and it
needs a different tool (`od`, `xxd`, a hexdump on the target) for the part
that actually needs byte-exactness.

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
needs to define something multi-line, write it to a file first (a
separate step, or out-of-band before running this tool at all) and use a
single step that sources it — `source /path/to/file.sh` — rather than
typing the file's contents as steps.

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
