---
name: new-skill
description: How to write a new SKILL.md in this repo, keeping its frontmatter description concise and accurate and its scope details elsewhere.
---

# Writing a new skill

## Applies to

Creating a new `.claude/skills/<name>/SKILL.md` in this repo, or editing
an existing one's frontmatter `description`.

## The rule

Frontmatter `description` is **one sentence: what the skill does or is
for.** No file paths, no parenthetical scope lists, no "use before/when X,
but not Y" trigger conditions — all of that goes in `## Applies to`, the
section immediately after the title.

This isn't just tone: the live skill listing every session gets shows only
`name: description`, and that's the only information available when
deciding whether a skill is relevant *before* loading it. A description
crammed with scope caveats reads as noise there. Trigger detail placed in
`## Applies to` costs nothing once the skill actually fires.

Worked examples already in this repo: `ship`'s description states a flow,
not a trigger list, and still passes the test; `investigate-bug`'s
("How to check whether...") and `propose-issue`'s both lead with the verb.
Ported 2026-08-24 from `NireBryce/nixos-configs`, where this rule exists
because Elly corrected every skill description in that repo on 2026-08-22
after they'd grown into full trigger specs — see that repo's own copy of
this skill for the fuller before/after if it's needed.

## Steps

1. **Pick a name**: kebab-case, matching the directory exactly. One
   `SKILL.md` per directory; no separate registry to update.
2. **Draft the description first, alone.** Test it by covering the body
   and asking: from the name plus this one sentence, would a session
   deciding whether to load this skill understand what it's for? If the
   honest answer needs a second clause, that clause belongs in
   `## Applies to`.
3. **Write `## Applies to` immediately after the title** — triggers,
   non-triggers, named examples, exceptions.
4. **Write the rest of the body** in whatever shape the task needs: `Why
   this exists` (with a concrete story where there is one), `Steps`,
   gotchas, `See also`. Cite real files and commands — check a path is
   real before naming it.
5. **Re-read the description against the finished body.** If the body
   ends up covering more or less than the description claims, fix the
   description — undersold and overclaimed are both wrong.
6. **Check it next to its siblings** (`ship`, `propose-issue`,
   `investigate-bug`). If this one reads noticeably longer or more
   parenthetical, it hasn't had this treatment yet.

## See also

- `ship`, `propose-issue`, `investigate-bug` in this repo — read one
  before writing a new skill; they're the worked examples.
