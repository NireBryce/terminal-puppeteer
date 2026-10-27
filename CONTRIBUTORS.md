# Contributors

This project was built, and is still mostly maintained, by Claude Code
agents working directly in this repo — see README.md's Provenance
section, and `CLAUDE.md` for the conventions those sessions follow.
Nire Bryce owns the repo and reviews at a distance, not line by line.

## Who can contribute

Anyone — human or LLM agent. Explicitly: an issue or PR from an LLM
agent is welcome here, not merely tolerated, as long as it's
well-written and human-readable. This project already leans on agents
for most of its own development; a contribution held to the same bar
this repo already holds its own work to is exactly what's wanted,
regardless of who — or what — drafted it.

What's not welcome: mass-generated, low-effort, or unverified
submissions. An agent that hasn't actually run the code it's touching,
or that pads a PR with generic boilerplate to look more thorough,
wastes review time the same way a careless human contribution would.
See `.claude/agent-styleguide.md` for what "well-written" means here concretely, not
just as a vibe.

## Filing an issue

`gh issue list --repo NireBryce/terminal-puppeteer --search "<keywords>"
--state all` first — check it isn't already tracked (`agent-devlog.md` is also
worth a grep; this repo records fixes there, not just in closed
issues). Then `gh issue create --repo NireBryce/terminal-puppeteer`, or
the web UI. A good issue here: what's wrong, where, how you noticed it,
and — if you're an LLM agent — how you verified it (ran it, read it,
traced through it; see `.claude/agent-styleguide.md` point 2).

## Opening a pull request

Fork, branch, commit, `python3 -m unittest discover tests` before you
open it (stdlib only — no install step; `nix develop` if you'd rather
not rely on whatever `python3` is already on your PATH), then open the
PR against `main`. Small and focused beats large and sweeping; see
`.claude/agent-styleguide.md` point 4.

## Style guide

`.claude/agent-styleguide.md` — a separate file, specifically for LLM-originating
submissions: verify claims before making them, explain why alongside
what, write real tests, and match this repo's own voice rather than
generic AI-PR boilerplate.
