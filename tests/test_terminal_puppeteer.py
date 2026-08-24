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
import contextlib
import importlib.util
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
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

    def test_arrow_and_navigation_keys(self):
        self.assertEqual(tp.encode_step("UP"), b"\x1b[A")
        self.assertEqual(tp.encode_step("DOWN"), b"\x1b[B")
        self.assertEqual(tp.encode_step("RIGHT"), b"\x1b[C")
        self.assertEqual(tp.encode_step("LEFT"), b"\x1b[D")
        self.assertEqual(tp.encode_step("HOME"), b"\x1b[H")
        self.assertEqual(tp.encode_step("END"), b"\x1b[F")
        self.assertEqual(tp.encode_step("DELETE"), b"\x1b[3~")
        self.assertEqual(tp.encode_step("PGUP"), b"\x1b[5~")
        self.assertEqual(tp.encode_step("PGDN"), b"\x1b[6~")
        self.assertEqual(tp.encode_step("down"), b"\x1b[B")  # case-insensitive too

    def test_ctrl_letter_is_general_not_just_cdr(self):
        # Used to be three hardcoded entries (C, D, R); now any A-Z via the
        # standard ord(letter) - 64 terminal control-code formula.
        self.assertEqual(tp.encode_step("CTRL-A"), b"\x01")
        self.assertEqual(tp.encode_step("CTRL-C"), b"\x03")  # old hardcoded value, unchanged
        self.assertEqual(tp.encode_step("CTRL-D"), b"\x04")  # ditto
        self.assertEqual(tp.encode_step("CTRL-L"), b"\x0c")
        self.assertEqual(tp.encode_step("CTRL-R"), b"\x12")  # ditto
        self.assertEqual(tp.encode_step("CTRL-U"), b"\x15")
        self.assertEqual(tp.encode_step("CTRL-W"), b"\x17")
        self.assertEqual(tp.encode_step("CTRL-Z"), b"\x1a")
        self.assertEqual(tp.encode_step("ctrl-a"), b"\x01")  # case-insensitive

    def test_ctrl_non_letter_suffix_is_not_a_key(self):
        # Only A-Z is a recognized CTRL- suffix; anything else falls
        # through to literal text like any other non-matching step.
        self.assertEqual(tp.encode_step("CTRL-1"), b"CTRL-1")
        self.assertEqual(tp.encode_step("CTRL-"), b"CTRL-")
        self.assertEqual(tp.encode_step("CTRL-AB"), b"CTRL-AB")

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


class NearMissWarningTests(unittest.TestCase):
    """warn_if_near_miss is advisory only -- it never changes what
    encode_step would send, only whether a heads-up lands on stderr."""

    @staticmethod
    def _warns(step):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tp.warn_if_near_miss(step)
        return buf.getvalue()

    def test_warns_on_missing_hyphen(self):
        self.assertIn("CTRL-C", self._warns("CTRLC"))

    def test_warns_on_close_typo(self):
        self.assertIn("ENTER", self._warns("ENTR"))

    def test_does_not_warn_on_an_exact_key_match(self):
        # Already handled correctly by encode_step -- nothing to warn about.
        self.assertEqual(self._warns("ENTER"), "")
        self.assertEqual(self._warns("ctrl-c"), "")
        self.assertEqual(self._warns("CTRL-Q"), "")  # general form, any letter

    def test_does_not_warn_on_ordinary_literal_text(self):
        for step in ("git status", "ls", "pwd", "echo hi", "yes", "exit"):
            with self.subTest(step=step):
                self.assertEqual(self._warns(step), "")

    def test_does_not_change_what_gets_sent(self):
        # The whole point: a near-miss still goes out as literal text,
        # unchanged, same as any other non-matching step.
        self.assertEqual(tp.encode_step("CTRLC"), b"CTRLC")


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

    def test_help_does_not_print_the_description_twice(self):
        # The description passed to ArgumentParser and __doc__'s first
        # paragraph used to be the exact same sentence, so --help printed
        # it once as the description and again as the epilog's own first
        # line.
        result = run_cli(["--help"])
        self.assertEqual(result.returncode, 0)
        needle = b"Let an LLM agent drive a real interactive program through a pty."
        self.assertEqual(result.stdout.count(needle), 1)

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

    def test_cmd_naming_a_nonexistent_executable_is_a_clean_error(self):
        # Issue #21: same class of bug as #10 again, but a runtime failure
        # (subprocess.Popen can't find argv[0]) rather than a usage-time
        # one -- not routed through p.error(), so exit 1 (not 2) and no
        # usage block, just the message.
        result = run_cli(
            ["--cmd", "tp-definitely-not-a-real-command-xyz", "x", "ENTER"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"could not start", result.stderr)
        self.assertIn(b"tp-definitely-not-a-real-command-xyz", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(b"usage:", result.stderr)

    def test_env_without_equals_sign_is_a_clean_error(self):
        result = run_cli(["--cmd", "cat", "--env", "NOEQUALSSIGN", "x"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--env:", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)


class VersionAndListKeysTests(unittest.TestCase):
    def test_version_prints_and_exits_zero(self):
        result = run_cli(["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"terminal-puppeteer.py", result.stdout)
        self.assertRegex(result.stdout, rb"\d+\.\d+\.\d+")

    def test_list_keys_does_not_require_a_target(self):
        # --list-keys alone, no --ssh/--cmd and no steps -- would normally
        # be two separate usage errors, but this bypasses both.
        result = run_cli(["--list-keys"])
        self.assertEqual(result.returncode, 0)

    def test_list_keys_lists_the_actual_keys(self):
        result = run_cli(["--list-keys"])
        for name in ("TAB", "ENTER", "UP", "DOWN", "DELETE", "CTRL"):
            with self.subTest(name=name):
                self.assertIn(name.encode(), result.stdout)

    def test_ssh_and_cmd_still_mutually_exclusive_after_group_made_optional(self):
        # Making the --ssh/--cmd group non-required (so --list-keys can
        # skip it) must not have also relaxed "at most one of the two" --
        # that half of a mutually exclusive group's enforcement doesn't
        # depend on required=True/False.
        result = run_cli(["--ssh", "host", "--cmd", "cat", "x"])
        self.assertEqual(result.returncode, 2)


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

    @unittest.skipUnless(shutil.which("pgrep"), "pgrep not available")
    def test_target_process_is_terminated_if_the_tool_itself_is_interrupted(self):
        # Issue #8: run()'s terminate/wait/kill only ran after the drain
        # loop returned normally, so a Ctrl+C on the tool mid-wait skipped
        # cleanup entirely and orphaned the target. Use a marker in the
        # target's own command line so pgrep can find it unambiguously,
        # interrupt the tool while it's still draining, and confirm the
        # marked process is gone shortly after -- not still running.
        #
        # A compound command (`while :; ...`), not a bare `sleep N`: bash
        # exec-replaces itself for a single trailing simple command, which
        # would swap the marker out of the process's own argv/cmdline and
        # make pgrep blind to it regardless of whether the fix works.
        marker = f"tp-interrupt-test-{uuid.uuid4().hex}"
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT_PATH), "--cmd",
             f"bash -c 'echo {marker}; while :; do sleep 1; done'",
             "--connect-wait", "3", "--step-delay", "3", "--settle", "3",
             "x"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # Give it time to actually spawn the target and start draining.
            deadline = time.time() + 5
            while time.time() < deadline:
                found = subprocess.run(
                    ["pgrep", "-f", marker], stdout=subprocess.PIPE
                ).stdout
                if found:
                    break
                time.sleep(0.1)
            self.assertTrue(found, "target process never showed up")

            # Interrupting the instant the target's own startup is visible
            # to pgrep lands in a narrow, flaky window right at process
            # spawn (empirically: <0.2s after the target first shows up,
            # the tool can still exit cleanly even unfixed, apparently
            # before its own signal handling is fully wired up) that isn't
            # representative of the real scenario -- a human hitting Ctrl+C
            # while the tool is settled into one of its multi-second waits.
            # Land solidly past that window instead.
            time.sleep(0.5)
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)

            time.sleep(0.5)  # let terminate()/wait() in the finally block land
            still_alive = subprocess.run(
                ["pgrep", "-f", marker], stdout=subprocess.PIPE
            ).stdout
            self.assertFalse(
                still_alive, "target process survived the tool being interrupted"
            )
        finally:
            subprocess.run(["pkill", "-9", "-f", marker], stderr=subprocess.DEVNULL)
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_env_sets_a_variable_in_the_target_process(self):
        result = run_cli(["--cmd", "env", *FAST, "--env", "TP_TEST_VAR=hello", "x"])
        self.assertIn(b"TP_TEST_VAR=hello", result.stdout)

    def test_env_merges_with_inherited_environment_rather_than_replacing_it(self):
        # subprocess.Popen's env= REPLACES the whole environment if given
        # at all -- run() has to merge onto os.environ itself, or the
        # target loses PATH and everything else the moment --env is used.
        result = run_cli(["--cmd", "env", *FAST, "--env", "TP_TEST_VAR=hello", "x"])
        self.assertIn(b"PATH=", result.stdout)

    def test_env_repeatable_for_multiple_variables(self):
        result = run_cli(
            ["--cmd", "env", *FAST, "--env", "TP_A=1", "--env", "TP_B=2", "x"]
        )
        self.assertIn(b"TP_A=1", result.stdout)
        self.assertIn(b"TP_B=2", result.stdout)

    def test_no_env_flag_leaves_environment_untouched(self):
        # Default (env=None passed to Popen) must behave exactly like
        # before --env existed -- full, unmodified inherited environment.
        result = run_cli(["--cmd", "env", *FAST, "x"])
        self.assertIn(b"PATH=", result.stdout)

    def test_verbose_echoes_each_step_to_stderr_in_order(self):
        result = run_cli(["--cmd", "cat", *FAST, "-v", "hi", "ENTER"])
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stderr.splitlines() if l.startswith(b">>")]
        self.assertEqual(lines, [b">> hi", b">> ENTER"])

    def test_not_verbose_by_default_prints_nothing_to_stderr(self):
        result = run_cli(["--cmd", "cat", *FAST, "hi", "ENTER"])
        self.assertEqual(result.stderr, b"")

    def test_steps_file_dash_reads_from_stdin(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--cmd", "cat", *FAST,
             "--steps-file", "-"],
            input=b"hi\nENTER\n",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(result.stdout, b"hi\r\nhi\r\n\n")


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
