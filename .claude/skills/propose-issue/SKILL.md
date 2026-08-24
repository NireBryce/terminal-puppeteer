---
name: propose-issue
description: How to propose filing a GitHub issue when you notice a genuine bug in this repo's own code/docs while working on something else.
---

# Proposing repo bugs for the issue tracker

## Applies to

You're doing something else — reading `terminal-puppeteer.py`, checking a
claim in `README.md`/`NOTES.md`, running the tests — and you notice an
actual defect along the way: a claim the code contradicts, a test that
fails, dead code, a doc example that no longer works. Not something you
were asked to look into (fix that directly and say so), and not a style
opinion (no failure scenario, no issue).

Ported from `NireBryce/nixos-configs`'s own `propose-issue` skill,
2026-08-24. Adapted here: that repo has three separate markdown-based
"record it, but not a tracker" mechanisms (a code comment, a numbered
`lessons-learned.md` entry, `wiki/open-threads.md`) built up before it
started using issues at all. This repo doesn't have that history — it's
been issue-native since `NireBryce/terminal-puppeteer` existed (issue #1,
filed and fixed the same day the repo was created) — so the two
`nixos-configs`-specific record-keeping mechanisms below are
`DEVLOG.md`/`NOTES.md`, not a third markdown queue to check separately.

## This never extends to a third-party repo

Every `gh issue create` in this skill hardcodes
`--repo NireBryce/terminal-puppeteer`. That's not incidental. If a defect
actually belongs to something this project depends on (Python itself, an
OS-level pty quirk), filing there is a different, heavier action with its
own rule in `CLAUDE.md`: never without Elly saying so explicitly, in those
words, unprompted — not satisfied by this skill's own step 3
`AskUserQuestion`. This skill still applies for tracking such a defect
*here* (`bug` label, a `DEVLOG.md` entry noting the upstream cause); filing
it at the third-party project is a separate ask made by name.

## Why propose instead of just filing

Filing is outward-facing the moment `gh issue create` returns — same
reason the `ship` skill gates a merge, just one confirmation instead of
two, since closing a wrongly-filed issue costs nothing the way an unwound
merge does.

## Steps

1. **Verify it, don't recall it.** Re-open the file, re-run the test, or
   re-run the command. A half-remembered impression is how a false issue
   gets filed.
2. **Check it isn't already tracked.** Run
   `gh issue list --repo NireBryce/terminal-puppeteer --search "<keywords>" --state all`
   and grep `DEVLOG.md`/`NOTES.md` for the topic. Stop here if it's already
   covered.
3. **Ask, showing the real content.** Use `AskUserQuestion` with the title
   and a short body already drafted — the decision should land on the
   actual text — and name which label you'd use (`bug` for a defect,
   `documentation` for a doc that's wrong, `enhancement` for a gap that
   isn't strictly broken).
4. **On yes:**

   ```sh
   gh issue create --repo NireBryce/terminal-puppeteer \
     --title "..." --label bug \
     --body "..."
   ```

   Body: what's wrong, where, how you noticed it, a fix sketch if one's
   obvious. Close it the same way this repo's other issues do:

   ```
   🤖 Filed with [Claude Code](https://claude.com/claude-code)
   ```

   Report the issue URL back in the reply.
5. **On no:** don't file it, say so, and still leave whatever `DEVLOG.md`
   note the finding warrants regardless — declining the issue doesn't
   mean the knowledge should evaporate too.

## Calibrate

Small solo tool, not production, but it's the thing an agent's own
debugging depends on being correct — a wrong claim in `README.md` or a
silently-broken timing guarantee (see issue #1) is worth proposing even
though nothing here is high-stakes the way `nixos-configs` wiping `/root`
is. Mention genuinely minor warts in the reply and leave it at that; run
this flow on things worth a round-trip.
