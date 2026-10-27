---
name: change-behavior
description: The checklist for changing what the CLI or step vocabulary does, so code, tests, docs, version, and devlog all move together.
---

# Changing what terminal-puppeteer does

## Applies to

Any change a user of the tool could observe: a new or changed flag, step
form, output format, exit code, advisory, or default. Also bug fixes that
change behavior a doc already described. Do NOT use for pure refactors
with identical output (still run the tests), or for packaging-only
changes (`verify-packaging`).

## Why this exists

Behavior is described in at least six places that drift independently:
the code, the README, the man page, the wiki pages in `docs/`,
`.claude/NOTES.md`, and the tests' own comments. Issue #1 was exactly
this: the README claimed `drain()` stopped early on quiet output and the
code never did. The wiki stamps ("checked against 0.6.0") are only
honest if a behavior change re-verifies them.

## Steps

1. **Reproduce first.** For a bug, write the failing test before the fix.
   For a feature, write the test that states the behavior. Stdlib
   `unittest` only; prefer a CLI integration test against a real pty
   target (`cat`, `printf`, `bash`) over mocking `run()`/`drain()`.
2. **Make the change** in the right module: `keys.py` (step vocabulary),
   `ansi.py` (escape stripping), `driver.py`/`session.py` (pty
   mechanics), `cli.py` (argparse, `main`). Any new output surface
   follows CLAUDE.md's rule: complete artifact, bounded presentation,
   loud pointer between them. Never silent truncation, never an
   unbounded dump, never a cut without a pointer.
3. **Run the suite:** `python3 -m unittest discover tests`. It takes a
   few minutes (real ptys, a real sshd); run it in the background. Say
   which tests skipped (fish and sshd are optional locally).
4. **Update every doc that describes it.** `grep -n` the flag or step
   name across `README.md`, `man/terminal-puppeteer.1`,
   `docs/Step-vocabulary.md`, `docs/Recipes.md`, `docs/Gotchas.md`,
   `.claude/NOTES.md`, and `--help` text in `cli.py`. A hit you don't
   update should be one you checked is still true.
5. **Decide the version.** User-visible feature: minor bump. Fix: patch.
   Tests/docs only or nothing user-facing: no bump, and say so in the
   devlog heading. A bump touches `pyproject.toml`
   `[project].version`, `terminal_puppeteer/_version.py`, the man page
   `.TH` line, and the "checked against" stamp on each `docs/` page.
   Re-verify what a stamp claims before moving it; if you can't,
   leave the stamp alone.
6. **Log it** with `log-work`: devlog entry, task list update.
7. **Say what you verified** in the PR text: which tests are new, what
   you ran by hand against a real target, what you only reasoned about.
   Land with `ship`.

## See also

`verify-packaging` (if the change touches how it's installed),
`add-gotcha` (if you found a pty quirk along the way),
`.claude/agent-styleguide.md`.
