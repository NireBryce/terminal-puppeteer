---
name: pre-submit-check
description: A checklist to run before opening an issue or PR against this repo, distilled from the agent style guide.
---

# Before you submit

## Applies to

You are an LLM agent (any tool, any model) about to file an issue or
open a PR against `NireBryce/terminal-puppeteer`, from a fork or from a
clone. In-repo sessions landing their own work also run it, then use
`ship`. Do NOT use it to decide whether to file outside this repo;
that needs Elly's explicit say-so (see CLAUDE.md).

## Why this exists

`CONTRIBUTORS.md` welcomes agent submissions that are verified and
human-readable and rejects mass-generated, unverified ones. The full
rules are `.claude/agent-styleguide.md`; this is the list to walk with
the diff or draft in front of you.

## Checklist

1. **Not a duplicate.** `gh issue list --repo NireBryce/terminal-puppeteer
   --search "<keywords>" --state all`, and grep `.claude/agent-devlog.md`.
2. **Verified, and said so.** You ran it. The text separates "tested
   against a real target" from "reads correctly to me".
3. **Tests for behavior changes**, stdlib `unittest`, real targets over
   mocks. `python3 -m unittest discover tests` passes (say which tests
   skipped).
4. **No new dependency.** If you think you need one, justify it hard.
5. **One logical change.** Unrelated finds become a separate issue.
6. **Docs agree with the code** (`change-behavior` step 4).
7. **Why, not just what.** The commit/PR says how it was found, the
   failure mode, and what you checked afterward.
8. **Voice.** Terse, concrete, no filler or marketing, no "Great catch!".
9. **Disclosure.** Say plainly that an agent wrote or helped write it,
   and add `Co-Authored-By: Claude <noreply@anthropic.com>` (no model
   name) if your tooling supports trailers.
10. **Cross-references.** An `owner/repo#123` shape naming another repo
    notifies that repo. Grep your text for it before posting.

If any item fails, fix it or say in the text that it is unfinished.

## See also

`.claude/agent-styleguide.md`, `CONTRIBUTORS.md`, `propose-issue`,
`ship`.
