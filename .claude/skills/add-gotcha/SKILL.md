---
name: add-gotcha
description: How to record a newly found pty or terminal quirk so the story, the index entry, and a pinning test all land together.
---

# Recording a gotcha

## Applies to

You hit something where a capture looked wrong, corrupted, or empty and
the cause was how a pty, shell, or line editor behaves, not a bug in
this tool (or a bug that has a lasting "how to read the output"
consequence). Do NOT use for ordinary bugs (`investigate-bug`,
`propose-issue`) or for tool behavior changes (`change-behavior`).

## Why this exists

A gotcha in only one place is half-recorded: `.claude/NOTES.md` has the
full story, `docs/Gotchas.md` is the one-line index agents actually
scan, and without a test the claim rots. `docs/Gotchas.md` says it:
nothing there is hypothetical, every entry cost real time once.

## Steps

1. **Check it isn't already there:** grep `.claude/NOTES.md`,
   `docs/Gotchas.md`, and `.claude/agent-devlog.md` for the symptom's
   words (`investigate-bug` has the three-command search).
2. **Reproduce it with the tool** against a real target and keep the
   actual capture. Reasoned-only explanations are labeled as such.
3. **Write the story in `.claude/NOTES.md`:** a heading naming the
   symptom as a reader sees it, the real capture, the cause, what to do
   instead. First person is fine there; it is a field-notes file.
4. **Add the one-liner to `docs/Gotchas.md`** in its existing shape:
   bold symptom, what's really happening, the flag or workaround, then
   `*NOTES: "<heading>"*` pointing at the story. Re-check the page's
   "checked against" stamp.
5. **Cross-link** from `docs/Recipes.md` or the README section where a
   reader would hit it, if there is one.
6. **Pin it with a test** where it can be done deterministically against
   a real pty target. Prefer WAIT-FOR over clocks; see issue #58 for
   what a timing-dependent test costs.
7. Log it with `log-work`.

## See also

`.claude/agent-styleguide.md` point 7 for voice; NOTES.md itself is the
style reference.
