# Style guide for LLM-originating submissions

For an LLM agent filing an issue or opening a PR against this repo —
see `CONTRIBUTORS.md` for the general invitation and the practical
steps (`gh issue create`, forking, running tests). This is what
"well-written and human-readable" means here, concretely, not just as a
vibe.

1. **Verify, don't assume.** Run the code, run the tests, reproduce the
   bug. This project's own bug fixes (issues #1, #4, #8, #9, #21) were
   all found by writing a test against real behavior instead of
   trusting a docstring or a plausible-sounding read of the source. "This
   should work" that hasn't actually been run is a red flag here, not a
   contribution.

2. **State what's verified vs. what's reasoned, and keep them separate.**
   "Tested against a real target" and "reads correctly to me" are
   different claims — say which one you're making, in the PR/issue text
   itself, not just in your own reasoning. `CLAUDE.md`'s
   `investigate-bug` skill has the fuller version of this rule, worth
   reading even if you're not a Claude Code session.

3. **Explain why, not just what.** A commit or PR that says "fix bug"
   without saying how it was found, what the actual failure mode is, and
   what was checked afterward isn't reviewable. Look at this repo's own
   commit messages (`git log`) for the expected shape and level of
   detail.

4. **One logical change per PR.** Bundling an unrelated cleanup into a
   bug-fix PR makes both harder to review and harder to revert
   independently if one turns out wrong.

5. **Tests are required for behavior changes.** Stdlib `unittest` only —
   no pytest, no other test dependency, matching this project's own "no
   dependencies" stance in README.md. Prefer exercising real behavior
   over mocks wherever practical: this repo's own CLI tests spawn real
   pty targets (`cat`, `bash`, `printf`) rather than mocking
   `subprocess`/`pty`, because the whole point of the tool is real-target
   behavior.

6. **No new dependencies** without a very strong reason. This is a
   stdlib-only, one-file tool by design — a PR that adds a
   `requirements.txt` or a `pip install`-time dependency needs to justify
   itself hard, to both the tool and its test suite.

7. **Match the existing voice.** Terse, concrete, dashes for asides, real
   examples over abstract claims, no marketing language, no filler
   ("Great catch!", "This comprehensive fix..."), no unnecessary hedging
   or apologizing. Read README.md, NOTES.md, and agent-devlog.md before writing
   prose for this repo — they're the style reference, not this
   paragraph's description of them.

8. **Read first, then write.** Check `agent-devlog.md` and the issue tracker
   (open and closed) before filing or fixing something that might
   already be known, already tracked, or already fixed. `CLAUDE.md`'s
   `investigate-bug` skill exists specifically because skipping this
   check once cost hours re-deriving a bug that had already been
   diagnosed two days earlier, one link from the docs index.

9. **Say what you are.** If an LLM agent authored or substantially
   assisted a submission, say so plainly in the PR or issue description,
   and in a `Co-Authored-By` trailer if your tooling supports one.
   README.md already discloses this about the project's own authorship
   (see Provenance) — holding outside contributions to a lower
   disclosure standard than the project holds itself to would be
   backwards.

10. **Stay in scope.** A PR that quietly does more than its title says
    — extra "while I was in there" changes — is harder to trust and
    review than one that does exactly what it claims. Noticed something
    else along the way? Mention it separately (a comment, a follow-up
    issue) rather than folding it in unannounced.

If you're a Claude Code session working directly in this repository,
rather than sending a PR or issue in from outside: read `CLAUDE.md`
instead, or as well — it has repo-specific mechanics (the exact commit
trailer format, the `ship`/`propose-issue`/`investigate-bug` skills)
this file doesn't repeat.
