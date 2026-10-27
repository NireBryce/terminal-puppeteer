# CLAUDE.md

> **Written by Claude Code, for Claude Code.** Working notes, not
> documentation — `README.md` is the human/agent-usage entry point,
> `.claude/NOTES.md` is what using the tool is actually like,
> `.claude/agent-devlog.md` is what happened to the project and when.
> This file is the fourth thing: the rules for working *in* this repo,
> as opposed to *with* the tool it ships.

## What this is

A stdlib-only CLI that lets an LLM agent drive a real interactive program
through a pty. `terminal-puppeteer.py` at the repo root is a thin entry
point (`from terminal_puppeteer.cli import main`) kept only so
`./terminal-puppeteer.py ...` keeps working without installing anything;
the actual implementation is the `terminal_puppeteer/` package next to
it (underscored because it's the Python import name and a hyphen isn't a
valid identifier; the hyphenated `terminal-puppeteer` is the project and
command name, and packaging treats the two spellings as the same
distribution — not a Nix thing, don't "fix" one to match the other) —
`keys.py` (the step vocabulary), `ansi.py` (escape stripping),
`driver.py` (the pty/subprocess mechanics), `cli.py` (argparse + `main`).
Extracted 2026-08-24 from `NireBryce/nixos-configs`
(`flake/scripts/ssh-pty-drive.py`, built there to chase a real
ble.sh/carapace completion bug — see that repo's
`claude cave/lessons-learned.md` #39 and issue #72), generalized beyond
SSH, given tests and a real bug fix the same day by a second session
working directly in this repo (`drain()` never actually stopped early on
quiet output, despite the README already claiming it did — issue #1, fixed
in #2), and split from one file into this package later the same day once
it grew past ~300 lines, for readability. Packaged three ways, all
verified by actually building and running them, not just written:
`pyproject.toml` (`pip install .`, `[project.scripts]`), `flake.nix`
(`nix run`/`nix build`, plus `apps`/`devShells`), and `default.nix` (a
`flake-compat` shim so `nix-build` works without flakes too). Read
`.claude/agent-devlog.md` before assuming you know the current state; it's
short and it's exactly for this.

## Conventions

**Commit trailer: a bare `Co-Authored-By: Claude` -- no model name, no
email.** Not `Claude Sonnet 5`, not `Claude Opus 5`, not
`Claude <noreply@anthropic.com>`, whatever the system prompt says to write — an agent cannot verify which model is
executing it, and the name is copied from a system prompt that may be
stale or generic. This already drifted once here: 4 of this repo's first
8 commits carry `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
(2 more got it right, `Claude <noreply@anthropic.com>` with no model name
— check with `git log --format='%B' | grep Co-Authored-By` before quoting
a count here again, it's small enough to recount rather than trust).
The email was dropped too on 2026-10-27, at Elly's direction (it also
means GitHub won't render the line as co-author credit -- deliberate).
Existing commits keep their labels (including the `<noreply@anthropic.com>`
ones, PR #75's among them) — rewriting merged history to fix a trailer
isn't worth it — but every commit from here on uses the bare form. (This is `nixos-configs`'s own rule,
ported here 2026-08-24 specifically because it had already drifted once,
independently, in this much smaller repo too — see that repo's `CLAUDE.md`
for the fuller account of why it keeps happening.)

**Agent-facing output follows one rule: complete artifact, bounded
presentation, loud pointer between them.** stdout is capped
(`--max-output`, one loud marker naming the cut and the ways out;
`--full-output` is the deliberate opt-out) because an agent's
transcript should hold what it *chose* to look at, not whatever volume
a target produced — megabytes of capture in context is unfilterable
after the fact and buries signal and secrets alike. `--capture-out` is
the complete record (never capped — the "capture doesn't lie" contract
lives there), `--show` reads it back in views and line ranges. Apply
the same shape to any new output surface in this repo, and don't
weaken the three parts: never silent truncation, never an unbounded
dump into context, never a cut without a pointer to the rest. It's a
decent default for agent-facing tools generally, which is why it's
written down as a rule here and not just a flag.

**Tests are stdlib `unittest`, no pytest, no other test dependency.**
Matches the tool's own "no dependencies" stance in `README.md` — a test
suite that needs `pip install` to run would quietly contradict the thing
being tested. `tests/` has both unit tests (`encode_step`, `load_steps`,
the `ANSI` regex — `import`ed as the real submodules of the real
`terminal_puppeteer` package now, the package split removed the old
`importlib.util` load-by-path workaround the pre-refactor hyphenated
single file needed) and CLI integration tests that run the real script
against real pty targets (`cat`, `printf`, `bash`) and check stdout
byte-for-byte — prefer that shape for anything that touches `run()` or
`drain()`, since the whole point of this tool is real-target behavior,
not mocked calls.

**`terminal-puppeteer.py`'s hyphenated name is deliberate, not
leftover — never rename it to match the package.** It was briefly
renamed to `terminal_puppeteer.py` during the package-split refactor and
that broke a real, verified case: at the time, `pyproject.toml`'s
`script-files` installed the wrapper alone into `<venv>/bin/`, separate
from `terminal_puppeteer/` (which `packages` installs into
`site-packages`). A script's own directory is always first on
`sys.path`, so a wrapper named identically to the package it imports
shadowed the real package with itself — a lone, non-package module —
before Python ever reached `site-packages`, raising `ModuleNotFoundError:
... 'terminal_puppeteer' is not a package`. A plain checkout never hit
this (the sibling `terminal_puppeteer/` sitting right next to the
wrapper won), which is exactly why it wasn't caught until actually
installing the built wheel into a scratch venv and running it for real.
`script-files` is gone now — `[project.scripts]` replaced it (the
idiomatic mechanism; see `pyproject.toml`'s own comment) precisely
because a *generated* entry-point script doesn't have this failure mode
at all, so the collision can't happen via the current config either way
— but the underlying rule still holds and costs nothing to keep: don't
give the root wrapper the same name as the package it imports. Test any
future packaging change the same way this one was: build the wheel,
install it into a throwaway venv, run the installed command, don't just
trust that running it in place still works. There are now three
packaging surfaces (`pyproject.toml`, `flake.nix`, `default.nix`) --
a change to one doesn't verify the others; `nix build`/`nix-build
default.nix` are as cheap to actually run as the wheel round-trip and
just as capable of silently breaking (`nix flake check` catches
evaluation errors, not runtime ones like this). `.github/workflows/nix.yml`
runs those Nix checks on every push/PR since 2026-09-07 -- but that's
slower feedback than running them locally before pushing, not a
replacement.

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
- `.claude/NOTES.md` — gotchas from actually using it against a real bug: terminal
  query escapes that go unanswered, a control byte that renders invisible,
  the forgotten-`ENTER` and bulk-paste mistakes, the tool's own diagnostic
  commands getting caught by the very bug being chased, tuning delays
  before trusting a negative result.
- `.claude/agent-tasks.md` — working list: open items, deferred backlog, and
  recently done work awaiting its `.claude/agent-devlog.md` entry.
- `.claude/agent-devlog.md` — terse, reverse-chronological, one entry per logical
  change rather than per commit. Read it before picking work back up here;
  it exists so that isn't a full `git log` read every time.
- `.claude/skills/` — procedures for recurring work: `change-behavior`,
  `verify-packaging`, `add-gotcha`, `log-work`, `pre-submit-check`, plus
  `ship`, `propose-issue`, `investigate-bug`, `new-skill`.
- `docs/` — the wiki, in-repo (GitHub's wiki feature is unavailable on
  this private repo: the `has_wiki` toggle silently no-ops via the API,
  checked 2026-09-09; Elly directed it into `docs/` instead). Task-oriented
  pages (Home, Step vocabulary, Recipes, Gotchas) for someone *using* the
  tool. In-repo docs stay canonical — the wiki links rather than
  duplicates wherever drift is likely, and every page is stamped with the
  version its facts were empirically checked against. When behavior
  changes, re-verify the stamped claims or bump the stamps.
- `CONTRIBUTORS.md` / `.claude/agent-styleguide.md` — the outside-facing counterpart to
  this file: who can contribute (explicitly including LLM agents) and
  what "well-written" means for a submission from one. This file has the
  mechanics for a session working directly in the repo; those two are for
  anyone sending a PR or issue in from outside it.
- `tests/` — run before every push regardless of what changed; see the
  `ship` skill's "is it green" step.
