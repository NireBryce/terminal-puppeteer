---
name: log-work
description: How to write an agent-devlog entry and keep agent-tasks.md in step with it.
---

# Logging finished work

## Applies to

Whenever a logical change is done, before landing it: after a feature,
a fix, a docs pass, a decision not to do something that was considered.
Do NOT use for work-in-progress notes (they belong in the conversation
or the PR) or for gotcha stories (`add-gotcha`; those go in NOTES.md).

## Why this exists

`.claude/agent-devlog.md` exists so the next session doesn't re-read
the whole git log, and CLAUDE.md tells sessions to read it first.
That only works if entries are terse and current, and if
`.claude/agent-tasks.md` doesn't contradict them.

## Steps

1. **One entry per logical change, not per commit,** newest on top,
   under the `---` rule at the head of the file.
2. **Heading:** `## YYYY-MM-DD — <version>: <short title>`; for no bump,
   `## YYYY-MM-DD — <title> (no version bump)`. Use the real date.
3. **A few bullets.** What changed, why, what was verified (tests
   before to after counts, what was run by hand). Reference commit
   hashes, issue and PR numbers instead of restating them. If it needs
   paragraphs it belongs in NOTES.md or the commit message.
4. **Update `.claude/agent-tasks.md`:** an item you finished moves from
   Open or Backlog to Done with its PR; an item you discovered goes in
   Open, or in Backlog if consciously deferred, with its blocker. Done
   items eventually fall off, because the devlog is authoritative.
5. **Use the current file names** (`agent-devlog.md`,
   `agent-tasks.md`) in new text.
6. **Don't edit past entries** except to fix a factual error.

## See also

`change-behavior`, `ship`.
