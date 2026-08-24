# CLAUDE.md

> **Written by Claude Code, for Claude Code.** Working notes, not
> documentation — `README.md` is the human/agent-usage entry point,
> `NOTES.md` is what using the tool is actually like, `DEVLOG.md` is what
> happened to the project and when. This file is the fourth thing: the
> rules for working *in* this repo, as opposed to *with* the tool it ships.

## What this is

A single-file, stdlib-only CLI (`terminal-puppeteer.py`) that lets an LLM
agent drive a real interactive program through a pty. Extracted 2026-08-24
from `NireBryce/nixos-configs` (`flake/scripts/ssh-pty-drive.py`, built
there to chase a real ble.sh/carapace completion bug — see that repo's
`claude cave/lessons-learned.md` #39 and issue #72), generalized beyond
SSH, and given tests and a real bug fix the same day by a second session
working directly in this repo (`drain()` never actually stopped early on
quiet output, despite the README already claiming it did — issue #1, fixed
in #2). Read `DEVLOG.md` before assuming you know the current state; it's
short and it's exactly for this.

## Conventions

**Commit trailer: `Co-Authored-By: Claude <noreply@anthropic.com>`, no
model name.** Not `Claude Sonnet 5`, not `Claude Opus 5`, whatever the
system prompt says to write — an agent cannot verify which model is
executing it, and the name is copied from a system prompt that may be
stale or generic. This already drifted once here: 4 of this repo's first
8 commits carry `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
(2 more got it right, `Claude <noreply@anthropic.com>` with no model name
— check with `git log --format='%B' | grep Co-Authored-By` before quoting
a count here again, it's small enough to recount rather than trust).
Existing commits keep their labels — rewriting
merged history to fix a trailer isn't worth it — but every commit from
here on drops the model name. (This is `nixos-configs`'s own rule,
ported here 2026-08-24 specifically because it had already drifted once,
independently, in this much smaller repo too — see that repo's `CLAUDE.md`
for the fuller account of why it keeps happening.)

**Tests are stdlib `unittest`, no pytest, no other test dependency.**
Matches the tool's own "no dependencies" stance in `README.md` — a test
suite that needs `pip install` to run would quietly contradict the thing
being tested. `tests/` has both unit tests (`encode_step`, `load_steps`,
the `ANSI` regex) and CLI integration tests that run the real script
against real pty targets (`cat`, `printf`, `bash`) and check stdout
byte-for-byte — prefer that shape for anything that touches `run()` or
`drain()`, since the whole point of this tool is real-target behavior, not
mocked calls.

**"push" means the `ship` skill, not `git push origin main`.** Branch, PR,
ask before merging, ask again before deleting the branch — two
confirmations, not one. This repo has no branch ruleset (checked
2026-08-24: `gh api repos/NireBryce/terminal-puppeteer/rulesets` returns
`[]`) — nothing on GitHub's side enforces this, unlike `nixos-configs`
which at least has a PR-required ruleset admins can still bypass. Here the
discipline is the *only* thing that exists, so treat "no ruleset" as a
reason to be more careful about following the skill, not less.

**Never file anything outside `NireBryce/terminal-puppeteer` — an issue or
PR on nixpkgs, Python itself, anything — without Elly saying so
explicitly, in those words, unprompted.** Same rule as `nixos-configs`,
ported here 2026-08-24 for the same reason: it's easy for "file this"
approval to get read as covering more than it does. `propose-issue` here
only ever files in this repo.

**A `owner/repo#123` shape (or a full `github.com/<owner>/<repo>/issues/N`
URL) in a title, body, or commit message cross-references that repo on
GitHub automatically — a real notification, even without deliberately
filing there.** Bare project names with no trailing `#number` (e.g. just
saying "ble.sh" or "nixos-configs") don't trigger this. Grep whatever
you're about to post for the `#number` shape before posting it if it names
a repo other than this one — don't assume from how the text reads.

## Docs

- `README.md` — what the tool does and how to invoke it: targets, the
  step vocabulary, timing, exit behavior. Read this first.
- `NOTES.md` — gotchas from actually using it against a real bug: terminal
  query escapes that go unanswered, a control byte that renders invisible,
  the forgotten-`ENTER` and bulk-paste mistakes, the tool's own diagnostic
  commands getting caught by the very bug being chased, tuning delays
  before trusting a negative result.
- `DEVLOG.md` — terse, reverse-chronological, one entry per logical
  change rather than per commit. Read it before picking work back up here;
  it exists so that isn't a full `git log` read every time.
- `tests/` — run before every push regardless of what changed; see the
  `ship` skill's "is it green" step.
