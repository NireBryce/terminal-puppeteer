---
name: investigate-bug
description: How to check whether a reported bug or symptom is already a known, tracked thread before investigating it yourself.
---

# Checking before investigating

## Applies to

Someone reports an error, a crash, or unexpected behavior with this tool,
and you're about to start reproducing or diagnosing it. Run this
**before** that — not after you've already found something and are
deciding whether to write it up (that's `propose-issue`, the filing side
of this same problem; this is the checking side). Doesn't apply when the
user already points you at the specific cause or file.

## Why this exists

Ported from `NireBryce/nixos-configs`'s own `investigate-bug` skill,
2026-08-24, written there after a bug got fully re-derived from scratch —
hours of live debugging — before anyone checked it had already been
diagnosed two days earlier, one link from that repo's own top-level docs
index. Ported here proactively, not because the same thing has happened
in this repo yet: this tool exists specifically to make an agent's own
debugging cheaper, and skipping the "has this been seen before" check is
exactly the kind of avoidable cost this tool's whole existence argues
against.

## Steps

1. **Before reproducing anything**, run
   `gh issue list --repo NireBryce/terminal-puppeteer --search "<keywords>" --state all`
   with a couple of guesses from the report's own wording, and grep
   `agent-devlog.md` and `NOTES.md` for the same terms. Three commands, not one
   script — this repo is small enough that a wrapper isn't worth
   maintaining yet; revisit that if `agent-devlog.md` and the issue count both
   grow enough that checking by hand gets tedious.
2. **A hit means read it fully** — the issue and/or the `agent-devlog.md` entry
   — before doing anything else. Pick up from where it left off rather
   than re-deriving from zero. If it's stale or wrong, fix *that* rather
   than starting a parallel investigation.
3. **No hit**: proceed as normal. Reproduce for real — this tool exists
   because a one-shot tool call and a real interactive session behave
   differently; use it on itself the same way it'd be used on anything
   else, via a throwaway target (`--cmd 'bash -i'`, `--cmd 'cat'`) rather
   than reasoning about `run()`/`drain()` from reading the source alone.
   Once something is actually diagnosed, follow `propose-issue`'s flow to
   file or track it, and add a `agent-devlog.md` entry for anything that
   actually changed the project.
4. **State fixed vs. verified precisely.** A fix that passes locally but
   hasn't been checked against a real target (SSH, a real shell, whatever
   the report used) is *written*, not *confirmed* — say which, in the
   issue and `agent-devlog.md` both, not just in the conversation. See issue #1
   for the shape this actually took here: a README claim that turned out
   false only once someone wrote a test against real behavior instead of
   trusting the docstring.

## See also

- `propose-issue` skill — the filing side of this same problem.
- `agent-devlog.md` — where a finished investigation's outcome should end up,
  so the next check in step 1 actually finds it.
