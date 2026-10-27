# terminal-puppeteer wiki

Lets an LLM agent drive a real interactive program through a pty —
sending actual keystrokes with delays between them, and reading back what
the program actually did — instead of the one-shot, no-tty view a normal
tool call gets. Built for bugs that only exist in a live, human-typed
terminal session; an agent has no terminal of its own, and this is the
stand-in.

This wiki is task-oriented: how to *do* the things the tool is for. The
canonical, complete reference is [`README.md`](../README.md); the war
stories are [`NOTES.md`](../NOTES.md). Pages here link rather than
duplicate wherever drift is likely.

## Task pages

- **[Step vocabulary](Step-vocabulary.md)** — every step form: named
  keys, `RAW:`, `PASTE:`, `SLEEP`, `RESIZE`, `WAIT-FOR:`,
  `WAIT-FOR-NEXT:` — exact bytes, rules, and the advisories the tool
  prints when a step looks like a mistake.
- **[Recipes](Recipes.md)** — how to shape a repro: pacing on output
  instead of clocks, multi-line input, reading captures, interactive
  `--repl` sessions, window sizing, ssh, secrets, exiting cleanly.
- **[Gotchas](Gotchas.md)** — the one-line index into NOTES.md: what a
  capture can look like versus what actually happened.

## Run it

No dependencies, no install needed from a checkout (stdlib only;
Python >= 3.9, CI-verified on 3.9/3.13/3.14; POSIX-only by construction,
verified on Linux):

```sh
# in place, from a checkout — grab the whole directory, not just the
# script: the wrapper needs its sibling terminal_puppeteer/ package
./terminal-puppeteer.py --cmd 'bash -i' 'echo $-' ENTER

# pip install . gives a terminal-puppeteer command instead (no .py)
pip install .
terminal-puppeteer --cmd 'bash -i' 'echo $-' ENTER

# Nix, with or without flakes
nix run github:NireBryce/terminal-puppeteer -- --cmd 'bash -i' 'echo $-' ENTER
nix-build default.nix
```

The man page ships in the repo (`man ./man/terminal-puppeteer.1`) and
installs to `share/man/man1/` with `pip install .`.

## The 60-second shape

```sh
# drive a local interactive shell: type a command, submit it, read the
# transcript (ANSI-stripped, capped at --max-output by default;
# --capture-out keeps the complete capture, --full-output prints it all)
./terminal-puppeteer.py --cmd 'bash -i' 'echo hello' ENTER

# over ssh — the case this was built for
./terminal-puppeteer.py --ssh myhost 'git co' TAB CTRL-C

# pace on the target's own output instead of guessed durations
./terminal-puppeteer.py --cmd 'bash -i' 'make' ENTER 'WAIT-FOR:done' 'echo next' ENTER

# interactive: one step per stdin line, one response per step
printf 'echo hi\nENTER\n' | ./terminal-puppeteer.py --cmd 'bash -i' --repl

# what "TAB", "CTRL-U", ... actually send
./terminal-puppeteer.py --list-keys
```

---

*Facts on this page checked against terminal-puppeteer 0.6.0, empirically
(2026-10-26) — originally under issue #64; re-verified through 0.5.1's
security pass and 0.6.0's output-cap changes. If a claim here and the
tool's behavior disagree, the tool wins: file an issue.*
