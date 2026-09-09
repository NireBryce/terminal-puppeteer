# Gotchas

The one-line index. Each entry has a full story with real captures in
[`NOTES.md`](../NOTES.md) — read that before trusting a confusing
capture. Nothing here is hypothetical; every one of these cost real
time once.

- **The pty has no terminal behind it** — capability queries
  (`\e[6n`, `\e[>c`, …) go unanswered and sit in the capture looking
  like corruption. Usually harmless; can silently change a
  probe-then-fallback target's *code path*. `--answer-queries` (opt-in)
  is the fix; `--env TERM=dumb` avoids some probing entirely.
  *NOTES: "query escapes go unanswered"*
- **`--answer-queries` is a fish fix, not a ble.sh fix** — against
  ble.sh the canned replies arrive late, land as idle input, and drive
  endless redraws that swallow your steps. Unanswered beats
  mis-answered there; the probe storm settles by itself in ~2s.
  *NOTES: "`--answer-queries` is a fish fix, not a ble.sh fix"*
- **A dead target used to look like a quiet success** — the pty echoes
  steps whether or not anything reads them. The tool now fails loudly
  (exit 1) when the target dies with steps un-sent; the echo-vs-output
  distinction still matters when *reading* any capture.
  *NOTES: "the target now gets a real controlling terminal"*
- **A live line editor's redraws look like typos** — zsh's per-key
  redraw makes a typed `echo hi` capture as `eecho hi` (backspace left
  as a literal character). Not double-sending. `--screen` renders it
  correctly. *NOTES: "a fancy line editor's own redraws"*
- **A byte that looks empty might not be** — an SOH in a variable
  renders as nothing in every view, `--raw` included. Byte-level
  questions go through `od -c`. *NOTES: "a byte that looks empty"*
- **The forgotten `ENTER`** — no step gets an implicit newline;
  `'git status'` alone sits typed and unsubmitted forever.
  *NOTES: "forgetting the ENTER step"*
- **Bulk-writing is not typing** — a multi-line write submits each
  line as it lands; `PASTE:` frames it as one bracketed paste.
  *NOTES: "bulk-writing a whole multi-line string"*
- **`sleep 30` as a step is a driver wait, not a typed command** — it
  sends nothing for 30 seconds (the advisory warns at validation
  time). `RAW:sleep 30\r` types it for real. *NOTES: "a step named
  `sleep N`"*
- **`WAIT-FOR` can match your own typed echo** — the transcript
  includes input; pick output-only markers or use `WAIT-FOR-NEXT:`.
  It's also case-sensitive. *NOTES: "a WAIT-FOR pattern can match your
  own typed echo"*
- **Your diagnostics can catch the bug you're chasing** — compound
  one-liners sent as steps go through the same input processing under
  test. Prefer plain commands, or source a file. *NOTES: "the bug
  you're chasing can catch your own diagnostic commands"*
- **Secrets in steps are transcript content** — `-v`, the capture, and
  shell history all keep them, in plaintext, forever. Hand them to the
  target out-of-band. *NOTES: "a credential typed as a step"*
- **Fetch large or exact captures out-of-band** — a steps-side
  redirect + plain `ssh host cat /tmp/out.txt` beats parsing a
  transcript. *NOTES: "fetch large or exact captures out-of-band"*
- **Tune the delays before trusting a negative result** — a clean
  capture with no error might just mean the window closed before the
  bug could fire. *NOTES: "tune the delays"*
- **Over ssh everything is one tty further away** — latency multiplies
  the timing gotchas, CTRL-C delivery is racy (retry before
  concluding), and a bare ssh one-liner gets a 0×0 remote pty, so
  don't diagnose window-size bugs through it. *NOTES: "over ssh,
  everything here happens one tty further away"*

---

*Index checked against NOTES.md and terminal-puppeteer 0.5.0
(2026-09-09) — see issue #64.*
