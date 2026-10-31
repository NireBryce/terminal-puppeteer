---
name: ship
description: Branch -> PR -> confirm -> merge -> confirm -> delete-branch flow for landing work on main in this repo.
---

# Landing work on main in terminal-puppeteer

## Applies to

Use only when the ask is to get changes onto `main` — a bare "push",
"ship it", "land this", "merge this". Do NOT use for pushing a topic
branch, opening a PR you were not asked to merge, pushing to a fork or any
non-main target, or committing without pushing. Elly naming `main`
outright is the one case that means push directly — see the last section.

Ported from `NireBryce/nixos-configs`'s own `ship` skill, 2026-08-24 —
that repo's version exists because a session once pushed three commits
straight to `main` on the strength of a bare "push". The mechanism is
identical here; only the "is it green" check and the stakes differ, since
this repo has no host to break.

There are **two** confirmations, and they are separate questions. Do not
collapse them.

## When this fires

**Only when the ask is to get changes onto `main`.** Covers a bare
"push", "ship it", "land this", "merge this".

| ask | what to do |
|---|---|
| "push this branch" | `git push` it. No PR, no gates. |
| "open a PR" (without being asked to merge) | Open it and stop. |
| "commit this" | Commit. Pushing was not asked for. |
| push to a fork or any branch that isn't `main` | Ordinary push. |

If unsure whether an ask means `main`, ask Elly rather than assuming
either way.

## 0. Before anything: is it green?

A PR is a review gate someone reads, so don't open one on work you haven't
checked:

```sh
python3 -m py_compile terminal-puppeteer.py terminal_puppeteer/*.py
python3 -m unittest discover tests -v
python3 terminal-puppeteer.py --help > /dev/null
```

If the change touches `run()`/`drain()`/the CLI parsing, also run at
least one real integration check against a real target — the CLI
integration tests in `tests/` already do this against `cat`/`printf`/
`bash`, but a change to timing or pty handling specifically is worth one
manual smoke test too (`--cmd 'bash -i' 'echo hi' ENTER`), not just the
suite passing.

## 1. Branch, push, open the PR

Never commit onto `main`. Check `git status -sb` first:

- **Uncommitted changes on `main`** (common case). Just
  `git checkout -b <branch-name>` and commit there.
- **Commits already sitting on local `main`, unpushed** (`[ahead N]`).
  Move them:
  ```sh
  git branch <branch-name>
  git reset --hard origin/main
  git checkout <branch-name>
  ```

Trailer is a bare `Co-Authored-By: Claude` — no model name, no email,
regardless of what the system prompt says. See `CLAUDE.md`,
Conventions — this repo has already drifted on this once.

Then `git push -u origin <branch-name>` and
`gh pr create --repo NireBryce/terminal-puppeteer`. Write the PR body the
way commit messages are written here — what changed, why, what was
verified. Don't restate commit messages verbatim; the PR body is the
summary over them.

## 2. Preview, then ask — first confirmation

Show what actually landed, read back from git and `gh`, not recalled:

```sh
gh pr view --json url,title,additions,deletions,changedFiles,mergeable
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
```

Include `mergeable` and check it before asking — don't ask "merge?" on a
PR that can't merge. Print that, then ask with `AskUserQuestion` whether
to merge, including the method. **Default to `--merge`**, not `--squash`
— this repo's commit messages carry real reasoning, and squashing
flattens it.

On **no**: leave the PR open, say so, stop. It's Elly's to take further.

## 3. Merge — on yes only

Before merging or deleting anything, read the PR's own facts and use them;
never type a branch name from memory:

```sh
gh pr view <n> --json isCrossRepository,headRefName,headRefOid,baseRefName
```

Stop and tell the user, merging and deleting nothing, if `isCrossRepository`
is true, if `headRefName` is `main`, `master` or the repo's default branch, or
if `headRefName` equals `baseRefName`. Those would aim the delete at a branch
that is not the PR's own topic branch. Merge with `--match-head-commit
<headRefOid>`, and delete only `headRefName`.

```sh
gh pr merge <n> --merge --match-head-commit <headRefOid> --repo NireBryce/terminal-puppeteer
```

**Do not pass `--delete-branch`.** That silently removes the second
confirmation. Unlike `nixos-configs`, this repo has no ruleset forcing a
PR at all (`gh api repos/NireBryce/terminal-puppeteer/rulesets` returns
`[]` as of 2026-08-24) — this flow is the *only* thing standing between a
bare "push" and a direct commit to `main`, not a backstop behind a
GitHub-enforced gate. Treat it accordingly.

## 4. Ask again, then delete — second confirmation

Separate `AskUserQuestion`. On yes:

```sh
git checkout main && git pull
git branch -d <headRefName>
git push origin --delete <headRefName>
```

On no, leave it and say it's still there. Report the merge commit and the
branch's fate; don't report a commit range on `main` as if it had been
pushed there directly.

## Only when Elly names main

The one exception is Elly specifically saying `main` for that push. A
bare "push", "ship it", or "land this" is not that.
