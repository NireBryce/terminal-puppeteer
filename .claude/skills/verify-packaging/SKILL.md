---
name: verify-packaging
description: How to verify a change to any of the three packaging surfaces by actually building and running it, not just running the checkout.
---

# Verifying packaging changes

## Applies to

Edits to `pyproject.toml`, `flake.nix`, `default.nix`, `package.nix`,
`flake.lock`, `.github/workflows/nix.yml`, the root wrapper
`terminal-puppeteer.py`, or the package layout under
`terminal_puppeteer/` (adding, renaming, or moving a module). Do NOT use
for changes inside existing modules that add no files.

## Why this exists

A rename of the root wrapper to `terminal_puppeteer.py` passed every
test in a checkout and broke every installed copy: the wrapper shadowed
the package it imports (`ModuleNotFoundError: ... 'terminal_puppeteer'
is not a package`). Running in place cannot catch this. There are three
packaging surfaces and passing one says nothing about the others.

## Steps

1. **Never rename `terminal-puppeteer.py`** to match the package. Keep
   the hyphen; CLAUDE.md has the full story.
2. **Wheel round-trip** (scratch dir outside the repo):
   `python3 -m venv /tmp/tp-venv && /tmp/tp-venv/bin/pip install .`
   from the repo root, then run `/tmp/tp-venv/bin/terminal-puppeteer
   --version` and a real step, e.g. `--cmd 'bash -i' 'echo hi' ENTER`.
   Delete the venv afterwards.
3. **Nix, if available:** `nix build` and `nix run . -- --version`;
   `nix-build default.nix` for the no-flakes path; `nix flake check`
   for evaluation. Evaluation passing does not mean it runs, so run the
   built binary. If nix isn't installed, say that you could not verify
   these surfaces, don't imply you did.
4. **Version consistency:** `pyproject.toml`, `_version.py` and the man
   page `.TH` must agree; `package.nix` reads the TOML.
5. **Report per surface:** wheel, `nix build`, `nix-build`, each as
   verified-by-running, or not run and why.
6. CI (`.github/workflows/nix.yml`, `test.yml`) runs these after the
   push; that is slower feedback, not a replacement for steps 2-3.

## See also

`change-behavior`, `ship`.
