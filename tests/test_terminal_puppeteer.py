"""Tests for terminal-puppeteer.py.

The script is a single file with a hyphen in its name, so it's loaded by
path via importlib rather than a normal package import.

Two layers:

- Unit tests for the pure helpers (`encode_step`, `load_steps`, the `ANSI`
  regex) that don't need a pty at all.
- CLI integration tests that actually invoke the script as a subprocess
  against a local `--cmd` target (`cat`, `printf`, `bash`) with short
  delays, and check the captured stdout byte-for-byte. These exercise the
  real pty plumbing (`run()`, `drain()`, argparse validation, process
  termination) the way an agent actually would.

Deliberately stdlib-only (`unittest`), matching the project itself.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "terminal-puppeteer.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("terminal_puppeteer", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tp = _load_module()

# Short delays for CLI integration tests -- long enough for a local `cat`/
# `printf`/`bash` to respond, short enough that the suite doesn't crawl.
# drain()'s early-exit-on-quiet-output means these are an upper bound in
# practice, not a fixed cost per step (see CliTimingTests).
FAST = ["--connect-wait", "0.2", "--step-delay", "0.3", "--settle", "0.3"]


def run_cli(args, timeout=30):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


class EncodeStepTests(unittest.TestCase):
    def test_named_keys(self):
        self.assertEqual(tp.encode_step("TAB"), b"\t")
        self.assertEqual(tp.encode_step("ENTER"), b"\r")
        self.assertEqual(tp.encode_step("RET"), b"\r")
        self.assertEqual(tp.encode_step("CR"), b"\r")
        self.assertEqual(tp.encode_step("NEWLINE"), b"\n")
        self.assertEqual(tp.encode_step("CTRL-C"), b"\x03")
        self.assertEqual(tp.encode_step("CTRL-D"), b"\x04")
        self.assertEqual(tp.encode_step("CTRL-R"), b"\x12")
        self.assertEqual(tp.encode_step("ESC"), b"\x1b")
        self.assertEqual(tp.encode_step("BACKSPACE"), b"\x7f")

    def test_named_keys_are_case_insensitive(self):
        self.assertEqual(tp.encode_step("tab"), b"\t")
        self.assertEqual(tp.encode_step("Enter"), b"\r")
        self.assertEqual(tp.encode_step("ctrl-c"), b"\x03")
        self.assertEqual(tp.encode_step("Ctrl-d"), b"\x04")

    def test_enter_ret_cr_are_the_same_byte(self):
        self.assertEqual(tp.encode_step("ENTER"), tp.encode_step("RET"))
        self.assertEqual(tp.encode_step("RET"), tp.encode_step("CR"))

    def test_literal_text_is_utf8_encoded_verbatim(self):
        self.assertEqual(tp.encode_step("git status"), b"git status")
        self.assertEqual(tp.encode_step("café"), "café".encode())

    def test_literal_text_that_merely_resembles_a_key_name_stays_literal(self):
        # Not in SPECIAL_KEYS, so 'tabby' must be sent as-is, not as TAB.
        self.assertEqual(tp.encode_step("tabby"), b"tabby")

    def test_empty_literal(self):
        self.assertEqual(tp.encode_step(""), b"")


class LoadStepsTests(unittest.TestCase):
    @staticmethod
    def _args(steps=(), steps_file=None):
        return types.SimpleNamespace(steps=list(steps), steps_file=steps_file)

    def test_positional_steps_only(self):
        args = self._args(steps=["a", "ENTER"])
        self.assertEqual(tp.load_steps(args), ["a", "ENTER"])

    def test_steps_file_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("first\nENTER\n")
            path = f.name
        try:
            args = self._args(steps_file=path)
            self.assertEqual(tp.load_steps(args), ["first", "ENTER"])
        finally:
            os.unlink(path)

    def test_steps_file_appended_after_positional(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("from-file\n")
            path = f.name
        try:
            args = self._args(steps=["from-cli"], steps_file=path)
            self.assertEqual(tp.load_steps(args), ["from-cli", "from-file"])
        finally:
            os.unlink(path)

    def test_steps_file_skips_blank_lines_and_comments(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# a comment\n\n   \nreal-step\n# trailing comment\n")
            path = f.name
        try:
            args = self._args(steps_file=path)
            self.assertEqual(tp.load_steps(args), ["real-step"])
        finally:
            os.unlink(path)

    def test_steps_file_lines_are_stripped(self):
        # Unlike a positional arg (preserved exactly), a step read from a
        # file has surrounding whitespace stripped -- a literal step that
        # needs leading/trailing spaces can't come from --steps-file.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("  padded  \n")
            path = f.name
        try:
            args = self._args(steps_file=path)
            self.assertEqual(tp.load_steps(args), ["padded"])
        finally:
            os.unlink(path)


class AnsiRegexTests(unittest.TestCase):
    def test_strips_csi_sgr_color_codes(self):
        self.assertEqual(tp.ANSI.sub(b"", b"\x1b[31mred\x1b[0m"), b"red")

    def test_strips_csi_with_private_marker(self):
        # e.g. cursor-visibility toggles: \x1b[?25l / \x1b[?25h
        self.assertEqual(tp.ANSI.sub(b"", b"\x1b[?25lhidden\x1b[?25h"), b"hidden")

    def test_strips_osc_sequence_terminated_by_bel(self):
        self.assertEqual(tp.ANSI.sub(b"", b"\x1b]0;window title\x07rest"), b"rest")

    def test_strips_charset_selection(self):
        self.assertEqual(tp.ANSI.sub(b"", b"\x1b(Btext\x1b)0"), b"text")

    def test_strips_known_single_char_escapes(self):
        for ch in b"=>78McD":
            seq = b"\x1b" + bytes([ch])
            with self.subTest(seq=seq):
                self.assertEqual(tp.ANSI.sub(b"", seq + b"x"), b"x")

    def test_leaves_plain_text_untouched(self):
        self.assertEqual(tp.ANSI.sub(b"", b"hello world"), b"hello world")

    def test_does_not_strip_a_bare_control_byte(self):
        # NOTES.md's SOH gotcha: a control byte that isn't part of a
        # recognized ANSI sequence is not "ANSI noise" and must survive
        # stripping -- --raw not helping with this is the whole point of
        # that note.
        self.assertEqual(tp.ANSI.sub(b"", b"IFS='\x01'"), b"IFS='\x01'")


class ArgparseValidationTests(unittest.TestCase):
    def test_target_is_required(self):
        result = run_cli(["literal"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--ssh --cmd", result.stderr)

    def test_ssh_and_cmd_are_mutually_exclusive(self):
        result = run_cli(["--ssh", "host", "--cmd", "cat", "literal"])
        self.assertEqual(result.returncode, 2)

    def test_at_least_one_step_is_required(self):
        result = run_cli(["--cmd", "cat"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"no steps given", result.stderr)

    def test_ssh_arg_rejected_without_ssh(self):
        result = run_cli(["--cmd", "cat", "--ssh-arg=-o", "literal", "ENTER"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--ssh-arg only applies to --ssh", result.stderr)

    def test_malformed_cmd_is_a_clean_error_not_a_traceback(self):
        # Issue #10: an unbalanced quote used to raise a raw
        # ValueError/traceback out of shlex.split instead of the
        # argparse-style error every other bad input gets.
        result = run_cli(["--cmd", "echo 'unterminated", "x", "ENTER"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--cmd:", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_missing_steps_file_is_a_clean_error_not_a_traceback(self):
        # Issue #10: same class of bug, via load_steps's open() call.
        result = run_cli(
            ["--cmd", "cat", "--steps-file", "/nonexistent/path.txt", "x"]
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--steps-file:", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)


class CliIntegrationTests(unittest.TestCase):
    """End-to-end runs against a local `cat` (or similar) under a real pty."""

    def test_step_without_enter_is_typed_but_never_submitted(self):
        # README/NOTES.md: no step gets an implicit trailing newline, so
        # 'no' 'submit' just sits typed at the prompt -- cat only ever
        # echoes it via pty local echo, it never sees a completed line to
        # echo back a second time.
        result = run_cli(["--cmd", "cat", *FAST, "no", "submit"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"nosubmit\n")

    def test_enter_step_submits_the_line(self):
        # Once ENTER lands, cat sees a full line and echoes it too: once
        # from the pty's local echo, once from cat itself.
        result = run_cli(["--cmd", "cat", *FAST, "hi", "ENTER"])
        self.assertEqual(result.stdout, b"hi\r\nhi\r\n\n")

    def test_named_key_matching_is_case_insensitive_end_to_end(self):
        result = run_cli(["--cmd", "cat", *FAST, "hi", "enter"])
        self.assertEqual(result.stdout, b"hi\r\nhi\r\n\n")

    # `printf ...; cat`, not bare `printf ...`: a bare one-shot printf exits
    # (and closes its end of the pty) as soon as it's done printing, racing
    # the "x" step sent right after it -- issue #4, seen as an intermittent
    # dropped `x` on both of these tests, not just this one. `cat` keeps the
    # target alive to reliably receive and echo the step once printf's
    # output is already flushed, removing the race instead of tolerating it.
    def test_ansi_stripped_by_default(self):
        cmd = "bash -c \"printf 'a\\033[31mb\\033[0mc'; cat\""
        result = run_cli(["--cmd", cmd, *FAST, "x"])
        self.assertEqual(result.stdout, b"abcx\n")

    def test_raw_flag_preserves_ansi(self):
        cmd = "bash -c \"printf 'a\\033[31mb\\033[0mc'; cat\""
        result = run_cli(["--raw", "--cmd", cmd, *FAST, "x"])
        self.assertEqual(result.stdout, b"a\x1b[31mb\x1b[0mcx\n")

    def test_steps_file_steps_run_after_positional_steps(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# comment\n\nfirst\nENTER\n")
            path = f.name
        try:
            result = run_cli(
                ["--cmd", "cat", *FAST, "before", "ENTER", "--steps-file", path]
            )
            self.assertEqual(
                result.stdout, b"before\r\nbefore\r\nfirst\r\nfirst\r\n\n"
            )
        finally:
            os.unlink(path)

    def test_target_process_is_terminated_on_exit(self):
        # README's "Exit behavior": only the target process is torn down
        # (SIGTERM, then SIGKILL after 5s if still alive) -- nothing is
        # typed to close it. Use a target that ignores SIGTERM so the run
        # must fall through to the SIGKILL path, and check the whole
        # invocation still completes (bounded well under the 5s+overhead
        # SIGKILL path, not hanging forever).
        script = "trap '' TERM; while :; do sleep 0.1; done"
        t0 = time.time()
        result = run_cli(["--cmd", f'bash -c "{script}"', *FAST, "x"], timeout=20)
        elapsed = time.time() - t0
        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed, 15)


class CliTimingTests(unittest.TestCase):
    def test_step_delay_is_cut_short_once_output_goes_quiet(self):
        """README: "All three stop early once output goes quiet for
        200ms, so raising them costs nothing when the target responds
        fast." (see issue #1 -- drain() didn't do this for a while).

        `cat`'s echo of a single unterminated step is on the wire almost
        instantly and then goes quiet, so a generous --step-delay of 2s
        should be cut well short of its full budget rather than consumed
        in full.
        """
        t0 = time.time()
        run_cli(
            ["--cmd", "cat", "--connect-wait", "0.1", "--step-delay", "2",
             "--settle", "0.1", "hi"]
        )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.5)

    def test_step_delay_still_runs_full_budget_when_target_stays_silent(self):
        """The early exit only fires after some output has arrived --
        a target that produces nothing at all (e.g. still starting up)
        must still get the full wait, not an instant bail-out."""
        t0 = time.time()
        run_cli(
            ["--cmd", "bash -c 'sleep 5'", "--connect-wait", "1.2",
             "--step-delay", "0.1", "--settle", "0.1", "x"]
        )
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 1.2)


if __name__ == "__main__":
    unittest.main()
