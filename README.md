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

```
terminal-puppeteer.py --ssh HOST 'git co' TAB CTRL-C
terminal-puppeteer.py --cmd 'bash -i' 'echo $-' ENTER
terminal-puppeteer.py --ssh HOST --steps-file repro.txt
```

Stdlib only, no dependencies. See the script's own module docstring
(`terminal-puppeteer.py --help`) for the full explanation and every option.

Extracted from `NireBryce/nixos-configs` (`flake/scripts/ssh-pty-drive.py`,
built there 2026-08-24 to chase a ble.sh/carapace completion bug — see that
repo's `claude cave/lessons-learned.md` #39), generalized here beyond SSH
and renamed to something self-evident without needing to know what a pty is.
