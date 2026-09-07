"""Tests for terminal_puppeteer.

Three layers, each importing the real package directly (it's on
sys.path via REPO_ROOT below), not loaded by path:

- Unit tests for the pure helpers (`keys.encode_step`, `cli.load_steps`,
  `ansi.ANSI`, `keys.print_key_list`) that don't need a pty at all.
- Direct tests of `driver.drain`/`driver.run` -- still real fds/a real
  pty/a real subprocess, just called directly instead of through a
  spawned CLI process, so they can check things (timing precision, fd
  counts) the CLI layer below can't reach into as easily.
- CLI integration tests that actually invoke `terminal-puppeteer.py` (the
  thin wrapper at the repo root) as a subprocess against a local `--cmd`
  target (`cat`, `printf`, `bash`) with short delays, and check the
  captured stdout byte-for-byte, exercising the whole thing end to end
  (argparse validation, process termination, real shell targets) the way
  an agent actually would.

Deliberately stdlib-only (`unittest`), matching the project itself.
"""
import contextlib
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

sys.path.insert(0, str(REPO_ROOT))
from terminal_puppeteer import ansi, cli, driver, keys  # noqa: E402

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
        self.assertEqual(keys.encode_step("TAB"), b"\t")
        self.assertEqual(keys.encode_step("ENTER"), b"\r")
        self.assertEqual(keys.encode_step("RET"), b"\r")
        self.assertEqual(keys.encode_step("CR"), b"\r")
        self.assertEqual(keys.encode_step("NEWLINE"), b"\n")
        self.assertEqual(keys.encode_step("CTRL-C"), b"\x03")
        self.assertEqual(keys.encode_step("CTRL-D"), b"\x04")
        self.assertEqual(keys.encode_step("CTRL-R"), b"\x12")
        self.assertEqual(keys.encode_step("ESC"), b"\x1b")
        self.assertEqual(keys.encode_step("BACKSPACE"), b"\x7f")

    def test_named_keys_are_case_insensitive(self):
        self.assertEqual(keys.encode_step("tab"), b"\t")
        self.assertEqual(keys.encode_step("Enter"), b"\r")
        self.assertEqual(keys.encode_step("ctrl-c"), b"\x03")
        self.assertEqual(keys.encode_step("Ctrl-d"), b"\x04")

    def test_arrow_and_navigation_keys(self):
        self.assertEqual(keys.encode_step("UP"), b"\x1b[A")
        self.assertEqual(keys.encode_step("DOWN"), b"\x1b[B")
        self.assertEqual(keys.encode_step("RIGHT"), b"\x1b[C")
        self.assertEqual(keys.encode_step("LEFT"), b"\x1b[D")
        self.assertEqual(keys.encode_step("HOME"), b"\x1b[H")
        self.assertEqual(keys.encode_step("END"), b"\x1b[F")
        self.assertEqual(keys.encode_step("DELETE"), b"\x1b[3~")
        self.assertEqual(keys.encode_step("PGUP"), b"\x1b[5~")
        self.assertEqual(keys.encode_step("PGDN"), b"\x1b[6~")
        self.assertEqual(keys.encode_step("down"), b"\x1b[B")  # case-insensitive too

    def test_ctrl_letter_is_general_not_just_cdr(self):
        # Used to be three hardcoded entries (C, D, R); now any A-Z via the
        # standard ord(letter) - 64 terminal control-code formula.
        self.assertEqual(keys.encode_step("CTRL-A"), b"\x01")
        self.assertEqual(keys.encode_step("CTRL-C"), b"\x03")  # old hardcoded value, unchanged
        self.assertEqual(keys.encode_step("CTRL-D"), b"\x04")  # ditto
        self.assertEqual(keys.encode_step("CTRL-L"), b"\x0c")
        self.assertEqual(keys.encode_step("CTRL-R"), b"\x12")  # ditto
        self.assertEqual(keys.encode_step("CTRL-U"), b"\x15")
        self.assertEqual(keys.encode_step("CTRL-W"), b"\x17")
        self.assertEqual(keys.encode_step("CTRL-Z"), b"\x1a")
        self.assertEqual(keys.encode_step("ctrl-a"), b"\x01")  # case-insensitive

    def test_ctrl_non_letter_suffix_is_not_a_key(self):
        # Only A-Z is a recognized CTRL- suffix; anything else falls
        # through to literal text like any other non-matching step.
        self.assertEqual(keys.encode_step("CTRL-1"), b"CTRL-1")
        self.assertEqual(keys.encode_step("CTRL-"), b"CTRL-")
        self.assertEqual(keys.encode_step("CTRL-AB"), b"CTRL-AB")

    def test_enter_ret_cr_are_the_same_byte(self):
        self.assertEqual(keys.encode_step("ENTER"), keys.encode_step("RET"))
        self.assertEqual(keys.encode_step("RET"), keys.encode_step("CR"))

    def test_literal_text_is_utf8_encoded_verbatim(self):
        self.assertEqual(keys.encode_step("git status"), b"git status")
        self.assertEqual(keys.encode_step("café"), "café".encode())

    def test_literal_text_that_merely_resembles_a_key_name_stays_literal(self):
        # Not in SPECIAL_KEYS, so 'tabby' must be sent as-is, not as TAB.
        self.assertEqual(keys.encode_step("tabby"), b"tabby")

    def test_empty_literal(self):
        self.assertEqual(keys.encode_step(""), b"")


class RawStepTests(unittest.TestCase):
    """RAW:<escapes> steps (#45): bytes through a bind/readline-style
    escape parser, as one write -- an ESC step followed by '[I' text is
    two writes, and the gap between them is a window for the target's
    escape-sequence parser to time out and discard the partial sequence."""

    def test_csi_sequence_arrives_as_one_write(self):
        # The case that hit it: konsole sends \e[I / \e[O (focus in/out)
        # when mode 1004 is enabled; there was no way to inject those.
        self.assertEqual(keys.encode_step("RAW:\\e[I"), b"\x1b[I")
        self.assertEqual(keys.encode_step("RAW:\\e[O"), b"\x1b[O")

    def test_prefix_is_case_insensitive(self):
        self.assertEqual(keys.encode_step("raw:\\e[I"), b"\x1b[I")
        self.assertEqual(keys.encode_step("Raw:\\t"), b"\t")

    def test_single_char_escapes(self):
        self.assertEqual(keys.decode_raw(r"\a\b\t\n\v\f\r"),
                         b"\a\b\t\n\v\f\r")
        self.assertEqual(keys.decode_raw(r"\e"), b"\x1b")
        self.assertEqual(keys.decode_raw(r"\\"), b"\\")

    def test_control_key_escapes(self):
        # ord(key) & 0x1f, the formula bind itself uses: letters both
        # cases, plus the @[\]^_ range -- \C-[ is \x1b, same byte as \e.
        self.assertEqual(keys.decode_raw(r"\C-a"), b"\x01")
        self.assertEqual(keys.decode_raw(r"\C-A"), b"\x01")
        self.assertEqual(keys.decode_raw(r"\c-z"), b"\x1a")
        self.assertEqual(keys.decode_raw(r"\C-["), b"\x1b")
        self.assertEqual(keys.decode_raw(r"\C-@"), b"\x00")

    def test_hex_escapes(self):
        self.assertEqual(keys.decode_raw(r"\x1b"), b"\x1b")
        self.assertEqual(keys.decode_raw(r"\x41"), b"A")
        self.assertEqual(keys.decode_raw(r"\x7"), b"\x07")  # one digit is enough
        self.assertEqual(keys.decode_raw(r"\x414243"), b"A4243")  # two digits max, then literal

    def test_unknown_escapes_stay_literal(self):
        # Predictability over silent reinterpretation: same rule as
        # `echo -e`, never guesswork about what plain text was meant as.
        self.assertEqual(keys.decode_raw(r"\q"), b"\\q")
        self.assertEqual(keys.decode_raw(r"\x"), b"\\x")
        self.assertEqual(keys.decode_raw(r"\xzz"), b"\\xzz")
        self.assertEqual(keys.decode_raw("trailing\\"), b"trailing\\")
        self.assertEqual(keys.decode_raw(r"\C-"), b"\\C-")

    def test_plain_text_passes_through_as_utf8(self):
        self.assertEqual(keys.decode_raw("hi there"), b"hi there")
        self.assertEqual(keys.decode_raw("café"), "café".encode())

    def test_empty_raw_sends_nothing(self):
        self.assertEqual(keys.encode_step("RAW:"), b"")

    def test_literal_steps_dont_grow_escape_syntax(self):
        # Without the explicit RAW: prefix, a backslash is still just a
        # backslash -- that's the whole reason the prefix exists.
        self.assertEqual(keys.encode_step(r"\e[I"), b"\\e[I")


class ParseWaitStepTests(unittest.TestCase):
    """SLEEP/WAIT <seconds>: a driver-side pause that sends nothing --
    unlike a target-side 'sleep 5' ENTER, which runs a command in the
    session under test (#44)."""

    def test_parses_seconds(self):
        self.assertEqual(keys.parse_wait_step("SLEEP 5"), 5.0)
        self.assertEqual(keys.parse_wait_step("WAIT 0.5"), 0.5)
        self.assertEqual(keys.parse_wait_step("sleep 2"), 2.0)
        self.assertEqual(keys.parse_wait_step("wait\t1.25"), 1.25)  # any whitespace

    def test_non_wait_steps_return_none(self):
        for step in ("TAB", "ENTER", "SLEEPY", "WAITING 5", "SLEEP", "WAIT"):
            with self.subTest(step=step):
                self.assertIsNone(keys.parse_wait_step(step))

    def test_unusable_durations_raise(self):
        # A malformed wait step must be a clean usage error, never
        # literal text typed into the target.
        for step in ("SLEEP abc", "WAIT -1", "SLEEP nan", "SLEEP inf",
                     "WAIT 5, five"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    keys.parse_wait_step(step)


class ParseWaitForTests(unittest.TestCase):
    """WAIT-FOR:<pattern>: pace on the target's output instead of a
    fixed delay (#46). Matched against the ANSI-stripped stream by
    default -- matching raw bytes is where a line editor redrawing
    mid-marker interleaves escapes into the pattern."""

    def test_substring_pattern(self):
        p = keys.parse_wait_for_step("WAIT-FOR:ready")
        self.assertTrue(p.search(b"startup noise... ready now"))
        self.assertIsNone(p.search(b"never mind"))

    def test_regex_pattern_when_wrapped_in_slashes(self):
        p = keys.parse_wait_for_step("WAIT-FOR:/MA.R?K/")
        self.assertTrue(p.search(b"MA1RK"))   # . = 1, R? present
        self.assertTrue(p.search(b"MARK"))    # R? absent
        self.assertIsNone(p.search(b"MAxxK"))  # . can't span two chars

    def test_slash_wrapping_is_what_selects_regex(self):
        # Without the slashes, regex metacharacters are literal -- a
        # pattern like '/etc/hosts' (no trailing slash) stays a substring.
        p = keys.parse_wait_for_step("WAIT-FOR:a.b")
        self.assertTrue(p.search(b"a.b"))
        self.assertIsNone(p.search(b"axb"))

    def test_patterns_are_bytes_over_stripped_output(self):
        # The driver strips ANSI before matching; the pattern itself is
        # compiled against bytes to match that stream directly.
        p = keys.parse_wait_for_step("WAIT-FOR:/r[e]ady/")
        self.assertTrue(p.search(b"\x1b[31mready\x1b[0m"))

    def test_empty_pattern_raises(self):
        with self.assertRaises(ValueError):
            keys.parse_wait_for_step("WAIT-FOR:")

    def test_invalid_regex_raises(self):
        with self.assertRaises(ValueError):
            keys.parse_wait_for_step("WAIT-FOR:/[/")

    def test_non_wait_for_steps_return_none(self):
        for step in ("WAIT-FOR", "TAB", "SLEEP 5", "WAIT 1"):
            with self.subTest(step=step):
                self.assertIsNone(keys.parse_wait_for_step(step))


class PrintKeyListTests(unittest.TestCase):
    """print_key_list() backs --list-keys (see VersionAndListKeysTests
    for the CLI-level check); these test its formatting logic directly."""

    @staticmethod
    def _output():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keys.print_key_list()
        return buf.getvalue()

    def test_groups_enter_ret_cr_into_one_row(self):
        # SPECIAL_KEYS has three separate entries mapping to the same
        # byte (b'\r') -- print_key_list groups them into a single row
        # rather than printing ENTER/RET/CR as three identical lines.
        out = self._output()
        self.assertIn("ENTER, RET, CR", out)

    def test_includes_the_general_ctrl_letter_row(self):
        # Not itself a SPECIAL_KEYS entry -- CTRL-<A-Z> is a separate
        # line print_key_list adds by hand, the one row not derived
        # from iterating SPECIAL_KEYS.
        out = self._output()
        self.assertIn("CTRL-<A-Z>", out)

    def test_every_special_key_name_appears_somewhere(self):
        out = self._output()
        for name in keys.SPECIAL_KEYS:
            with self.subTest(name=name):
                self.assertIn(name, out)

    def test_lists_the_non_key_step_forms_too(self):
        # --list-keys is the one-screen step-vocabulary reference; the
        # RAW:/SLEEP/WAIT-FOR forms belong on it, not just in --help.
        out = self._output()
        for form in ("RAW:", "SLEEP <seconds>", "WAIT-FOR:<pattern>"):
            with self.subTest(form=form):
                self.assertIn(form, out)


class NearMissWarningTests(unittest.TestCase):
    """warn_if_near_miss is advisory only -- it never changes what
    encode_step would send, only whether a heads-up lands on stderr."""

    @staticmethod
    def _warns(step):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            keys.warn_if_near_miss(step)
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
        self.assertEqual(keys.encode_step("CTRLC"), b"CTRLC")

    def test_does_not_warn_on_the_other_step_forms(self):
        # RAW:/SLEEP/WAIT-FOR steps are their own exact-match vocabulary --
        # 'RAW:CR' is three-quarters CR by difflib's math and is not a
        # typo of it, and a malformed SLEEP/WAIT-FOR gets a proper usage
        # error at validation time instead.
        for step in ("RAW:CR", "RAW:TAB", "SLEEP 5", "wait 1",
                     "WAIT-FOR:ENTER", "SLEEP abc"):
            with self.subTest(step=step):
                self.assertEqual(self._warns(step), "")


class LoadStepsTests(unittest.TestCase):
    @staticmethod
    def _args(steps=(), steps_file=None):
        return types.SimpleNamespace(steps=list(steps), steps_file=steps_file)

    def test_positional_steps_only(self):
        args = self._args(steps=["a", "ENTER"])
        self.assertEqual(cli.load_steps(args), ["a", "ENTER"])

    def test_steps_file_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("first\nENTER\n")
            path = f.name
        try:
            args = self._args(steps_file=path)
            self.assertEqual(cli.load_steps(args), ["first", "ENTER"])
        finally:
            os.unlink(path)

    def test_steps_file_appended_after_positional(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("from-file\n")
            path = f.name
        try:
            args = self._args(steps=["from-cli"], steps_file=path)
            self.assertEqual(cli.load_steps(args), ["from-cli", "from-file"])
        finally:
            os.unlink(path)

    def test_steps_file_skips_blank_lines_and_comments(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# a comment\n\n   \nreal-step\n# trailing comment\n")
            path = f.name
        try:
            args = self._args(steps_file=path)
            self.assertEqual(cli.load_steps(args), ["real-step"])
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
            self.assertEqual(cli.load_steps(args), ["padded"])
        finally:
            os.unlink(path)


class AnsiRegexTests(unittest.TestCase):
    def test_strips_csi_sgr_color_codes(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b[31mred\x1b[0m"), b"red")

    def test_strips_csi_with_private_marker(self):
        # e.g. cursor-visibility toggles: \x1b[?25l / \x1b[?25h
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b[?25lhidden\x1b[?25h"), b"hidden")

    def test_strips_osc_sequence_terminated_by_bel(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b]0;window title\x07rest"), b"rest")

    def test_strips_charset_selection(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b(Btext\x1b)0"), b"text")

    def test_strips_known_single_char_escapes(self):
        for ch in b"=>78McD":
            seq = b"\x1b" + bytes([ch])
            with self.subTest(seq=seq):
                self.assertEqual(ansi.ANSI.sub(b"", seq + b"x"), b"x")

    def test_leaves_plain_text_untouched(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"hello world"), b"hello world")

    def test_does_not_strip_a_bare_control_byte(self):
        # NOTES.md's SOH gotcha: a control byte that isn't part of a
        # recognized ANSI sequence is not "ANSI noise" and must survive
        # stripping -- --raw not helping with this is the whole point of
        # that note.
        self.assertEqual(ansi.ANSI.sub(b"", b"IFS='\x01'"), b"IFS='\x01'")


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

    def test_malformed_wait_step_is_a_clean_usage_error(self):
        # 'SLEEP abc' must never fall through to being typed as literal
        # text into the target -- that's a silent footgun.
        result = run_cli(["--cmd", "cat", "SLEEP abc", "ENTER"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"wait step", result.stderr)
        self.assertIn(b"SLEEP abc", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_malformed_wait_for_steps_are_clean_usage_errors(self):
        for step in ("WAIT-FOR:", "WAIT-FOR:/[/"):
            with self.subTest(step=step):
                result = run_cli(["--cmd", "cat", step])
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"WAIT-FOR step", result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_non_numeric_wait_for_timeout_is_a_usage_error(self):
        result = run_cli(["--cmd", "cat", "--wait-for-timeout", "abc", "x"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--wait-for-timeout", result.stderr)


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


class DrainTests(unittest.TestCase):
    """Direct tests of driver.drain() against a real os.pipe() fd --
    faster and more precise than only exercising this indirectly through
    the full CLI/pty stack (see CliTimingTests for that side), and still
    a real fd, not a mock."""

    def test_exits_early_once_quiet_after_receiving_data(self):
        r, w = os.pipe()
        try:
            os.write(w, b"hello")
            t0 = time.time()
            out = driver.drain(r, 5)  # generous budget
            elapsed = time.time() - t0
            self.assertEqual(out, b"hello")
            self.assertLess(elapsed, 1.0)
        finally:
            os.close(r)
            os.close(w)

    def test_waits_the_full_budget_if_nothing_ever_arrives(self):
        r, w = os.pipe()
        try:
            t0 = time.time()
            out = driver.drain(r, 0.5)
            elapsed = time.time() - t0
            self.assertEqual(out, b"")
            self.assertGreaterEqual(elapsed, 0.5)
        finally:
            os.close(r)
            os.close(w)

    def test_stops_on_eof_once_the_write_end_closes(self):
        # A different exit path than the quiet-timeout one above: EOF
        # (os.read returning b'') breaks immediately, full budget or not.
        r, w = os.pipe()
        os.write(w, b"bye")
        os.close(w)
        try:
            t0 = time.time()
            out = driver.drain(r, 5)
            elapsed = time.time() - t0
            self.assertEqual(out, b"bye")
            self.assertLess(elapsed, 1.0)
        finally:
            os.close(r)


class RunDirectTests(unittest.TestCase):
    """Direct tests of driver.run() -- real pty, real subprocess, just
    imported and called directly instead of through the CLI subprocess
    layer, so these can check things (like fd counts) the CLI-level
    tests in CliIntegrationTests have no way to reach into."""

    @staticmethod
    def _open_fd_count():
        return len(os.listdir("/proc/self/fd"))

    @unittest.skipUnless(os.path.isdir("/proc/self/fd"), "no /proc/self/fd here")
    def test_no_fd_leak_on_a_normal_run(self):
        before = self._open_fd_count()
        driver.run(["cat"], ["hi", "ENTER"], 0.3, 0.3, 0.2)
        after = self._open_fd_count()
        self.assertEqual(before, after)

    @unittest.skipUnless(os.path.isdir("/proc/self/fd"), "no /proc/self/fd here")
    def test_no_fd_leak_when_popen_fails(self):
        # Issue #21/#8's fix, formalized: run() closes both pty fds
        # itself on a failed Popen, rather than leaking them. Previously
        # only verified once by hand (counting /proc/self/fd across 20
        # separate subprocess invocations, so nothing leaked *within* a
        # process either way); this checks it directly, in-process,
        # against the real function.
        before = self._open_fd_count()
        with self.assertRaises(OSError):
            driver.run(
                ["tp-definitely-not-a-real-command-xyz"], ["x"], 0.1, 0.1, 0.1
            )
        after = self._open_fd_count()
        self.assertEqual(before, after)


class DriverStepTests(unittest.TestCase):
    """Direct tests of the driver-side step forms (#44 SLEEP, #46
    WAIT-FOR) and the query responder (#43) -- real pty, real
    subprocess, called directly so the CLI layer's byte-exact checks
    below stay focused on end-to-end behavior."""

    def test_sleep_step_sends_nothing(self):
        # A plain `cat` echoes every byte written to it and produces
        # nothing on its own -- so an empty capture is direct evidence
        # the SLEEP step wrote no bytes, which is the whole point of a
        # driver-side wait (#44).
        out = driver.run(["cat"], ["SLEEP 0.3"], 0.1, 0.1, 0.1)
        self.assertEqual(out, b"")

    def test_sleep_step_waits_the_full_duration(self):
        # `late` is printed ~1s after startup; a 2s SLEEP must cover it,
        # a 0.2s one must not (5x margins each way, bash spawn included).
        argv = ["bash", "-c", "(sleep 1; echo late) & cat"]
        out = driver.run(argv, ["SLEEP 2"], 0.1, 0.1, 0.2)
        self.assertIn(b"late", out)
        out = driver.run(argv, ["WAIT 0.2"], 0.1, 0.1, 0.2)
        self.assertNotIn(b"late", out)

    def test_wait_for_step_paces_on_target_output(self):
        # The marker only exists in the target's output -- nothing typed
        # beforehand contains it -- so a match proves the step kept
        # draining until the target actually said it (#46).
        argv = ["bash", "-c", "sleep 0.5; echo READY-9; cat"]
        out = driver.run(
            argv, ["WAIT-FOR:READY-9", "hi", "ENTER"], 0.2, 0.2, 0.2,
            wait_for_timeout=5,
        )
        self.assertIn(b"READY-9", out)
        self.assertIn(b"hi", out)

    def test_wait_for_step_matches_stripped_output(self):
        # Raw stream has \x1b[31m between MA and RK; the stripped one
        # doesn't -- the default match must be against the stripped one.
        # run() returns the raw capture; strip it the same way the CLI
        # does before asserting on the match.
        argv = ["bash", "-c", "printf 'MA\\033[31mRK\\n'; cat"]
        out = driver.run(argv, ["WAIT-FOR:MARK", "x"], 0.2, 0.2, 0.2)
        self.assertIn(b"MARK", ansi.ANSI.sub(b"", out))

    def test_wait_for_step_timeout_fails_loudly(self):
        # A pattern that never arrives must raise, not silently become a
        # slow no-op (#46) -- the CLI turns this into exit 1 + a message
        # with the pattern and the last output seen.
        with self.assertRaises(driver.WaitForTimeout):
            driver.run(
                ["cat"], ["WAIT-FOR:tp-never-arrives"], 0.1, 0.1, 0.1,
                wait_for_timeout=0.5,
            )

    def test_wait_for_step_matches_output_seen_before_the_step(self):
        # Matching is against everything accumulated so far (connect-wait
        # and earlier steps included) -- a marker that already arrived
        # must not hang waiting for a repeat.
        argv = ["bash", "-c", "echo early-bird; cat"]
        out = driver.run(argv, ["WAIT-FOR:early-bird"], 0.1, 0.1, 0.5)
        self.assertIn(b"early-bird", out)

    def test_answer_queries_replies_to_each_query_occurrence(self):
        # The target sends a DA2 query; the reply is written into the
        # target's input and comes back via the pty's own echo -- present
        # with the flag, absent without it. Real pty, real responder, no
        # mocks. The echo shows the reply's ESC byte the way a cooked
        # pty's ECHOCTL renders any control char: as the two literal
        # characters ^[, not as \x1b.
        argv = ["bash", "-c", "printf '\\033[>c'; cat"]
        out = driver.run(
            argv, ["x"], 0.3, 0.2, 0.3, answer_queries=True,
        )
        self.assertEqual(out.count(b"^[[>85;3800;0c"), 1)
        out = driver.run(argv, ["x"], 0.3, 0.2, 0.3)
        self.assertNotIn(b"^[[>85;3800;0c", out)

    def test_answer_queries_replies_again_when_a_query_recurs(self):
        # A line editor re-sends its capability queries on every prompt
        # redraw, and a real terminal answers every time. The old
        # once-per-run behavior left repeats unanswered, which is its own
        # fallback trigger: verified against fish 4.8.1 (#37) -- it times
        # out, redraws, and flushes its input buffer, eating keystrokes
        # typed into that window.
        argv = ["bash", "-c", "printf '\\033[6n\\033[6n'; cat"]
        out = driver.run(argv, ["x"], 0.4, 0.3, 0.3, answer_queries=True)
        self.assertEqual(out.count(b"^[[1;1R"), 2)

    def test_answer_queries_covers_the_query_types_fish_sends(self):
        # fish 4.x probes these on startup and hangs before its first
        # prompt without replies (issue #37, verified on real hardware):
        # kitty-keyboard, OSC 11 background color, DA1 with param 0
        # (the \e[0c form), and a DCS termcap query. One run, all four.
        argv = [
            "bash", "-c",
            "printf '\\033[?u\\033]11;?\\033\\\\\\033[0c\\033P+q696e646e\\033\\\\'; cat",
        ]
        out = driver.run(argv, ["x"], 0.4, 0.3, 0.3, answer_queries=True)
        self.assertIn(b"^[[?1u", out)                       # kitty-keyboard
        self.assertIn(b"^[]11;rgb:0000/0000/0000", out)     # OSC 11
        self.assertIn(b"^[[?62;3c", out)                    # DA1 \e[0c
        self.assertIn(b"^[P1+r696e646e", out)               # DCS termcap echo

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "no /proc here")
    def test_target_gets_the_pty_as_controlling_terminal(self):
        # setsid + TIOCSCTTY in run()'s spawn (issue #37): the target
        # runs in its own session, so tcgetpgrp/tcsetpgrp and job control
        # work like a real terminal. Before this, the target inherited
        # the tool's session and the pty was never its controlling
        # terminal -- bash/zsh printed 'no job control' warnings and ran
        # degraded; fish exited outright. Session read straight from
        # /proc/self/stat's session field (6) -- no ps dependency,
        # which doesn't exist in nix build sandboxes.
        argv = [
            "bash", "-c",
            "read -r pid comm state ppid pgrp sid _ < /proc/self/stat; echo $sid; cat",
        ]
        out = driver.run(argv, ["x"], 0.3, 0.3, 0.3)
        target_sid = int(out.split(b"\r\n", 1)[0].strip())
        self.assertNotEqual(target_sid, os.getsid(0))

    def test_bash_interactive_gets_job_control(self):
        # Two symptoms of the same fix: no job-control warning, and `$-`
        # containing `m` (monitor mode) -- with `m`, CTRL-C/CTRL-Z/fg
        # actually reach foreground jobs.
        out = driver.run(
            ["bash", "--norc", "-i"], ["echo $-", "ENTER"], 1.0, 1.0, 0.5,
        )
        self.assertNotIn(b"cannot set terminal process group", out)
        self.assertIn(b"himBH", out)

    def test_ctrl_c_interrupts_a_foreground_job(self):
        # With job control, CTRL-C is a real SIGINT to the foreground
        # process group: sleep dies (exit status 130), the shell
        # survives, and the next line runs. The expanded `done-130`
        # proves execution -- the typed echo of `done-$?` alone would
        # appear either way.
        out = driver.run(
            ["bash", "--norc", "-i"],
            ["sleep 30", "ENTER", "SLEEP 0.5", "CTRL-C",
             "echo done-$?", "ENTER"],
            1.0, 1.0, 0.5,
        )
        self.assertIn(b"done-130", out)

    def test_target_gets_a_real_window_size(self):
        # A real terminal hands the target a size (24 rows, 80 cols);
        # pty.openpty() alone leaves the tty at 0x0, which bash's own
        # 80x24 fallback masks for $COLUMNS but stty -- and anything
        # reading the tty directly -- sees. Surfaced during the #37
        # chase (fish's line editor ran its cursor math on a
        # zero-width tty).
        out = driver.run(["bash", "-c", "stty size; cat"], ["x"], 0.5, 0.5, 0.5)
        self.assertEqual(out.split(b"\r\n", 1)[0], b"24 80")

    def test_target_dying_with_steps_remaining_fails_loudly(self):
        # The #37 masquerade's fix: steps sent to a dead target raise,
        # with the exit status, the count sent/remaining, and the
        # partial capture attached (the target's dying words are the
        # diagnostic).
        with self.assertRaises(driver.TargetDied) as cm:
            driver.run(
                ["bash", "-c", "echo pre-death; exit 5"],
                ["a", "ENTER", "b", "ENTER"],
                0.3, 0.2, 0.3,
            )
        self.assertIn(b"pre-death", cm.exception.output)
        self.assertIn("status 5", str(cm.exception))
        self.assertIn("after 0 of 4 steps", str(cm.exception))
        self.assertIn("next would be 'a'", str(cm.exception))

    def test_target_killed_by_a_signal_names_the_signal(self):
        with self.assertRaises(driver.TargetDied) as cm:
            driver.run(["bash", "-c", "kill -9 $$"], ["x"], 0.3, 0.2, 0.3)
        self.assertIn("SIGKILL", str(cm.exception))

    def test_target_dying_after_the_last_step_is_not_an_error(self):
        # The clean-logout shape: the death happens after the final
        # step, so there is nothing left to send into the void -- this
        # is the normal way a repro ends (exit/CTRL-D as the last step)
        # and must stay exit-0.
        out = driver.run(
            ["bash", "-c", "read line; echo got-$line"],
            ["hi", "ENTER"],
            0.5, 0.5, 0.3,
        )
        self.assertIn(b"got-hi", out)


@unittest.skipUnless(shutil.which("fish"), "fish not on PATH")
class FishIntegrationTests(unittest.TestCase):
    """The issue #37 verification, kept as tests: fish works under this
    tool given both fixes its refusal exposed (controlling terminal +
    answered queries), and fails loudly without the second one. Skipped
    wherever fish isn't installed."""

    ARGV = ["fish", "-N", "-C", "function fish_prompt; echo -n FISHREADY; end"]

    def test_fish_executes_commands_with_answer_queries(self):
        # -N skips user config, -C pins a known prompt marker: the
        # WAIT-FOR paces on fish finishing its startup query storm
        # instead of hoping a fixed delay lands after it (drain()'s
        # early-exit-on-quiet makes bare connect-wait land mid-init,
        # where fish's query reader swallows typed-ahead keystrokes).
        out = driver.run(
            self.ARGV,
            ["WAIT-FOR:FISHREADY", "SLEEP 0.8", "echo hi", "ENTER"],
            2.0, 2.0, 5.0, answer_queries=True,
        )
        # Typed echo + command output, at least.
        self.assertGreaterEqual(out.count(b"hi"), 2)

    def test_fish_fails_loudly_without_answer_queries(self):
        # The controlling terminal alone is not enough: fish still
        # probes kitty/OSC 11/DA1/termcap on startup and hangs before
        # its first prompt on a bare pty. WAIT-FOR turns that hang into
        # a loud failure naming the step, never a slow no-op.
        with self.assertRaises(driver.WaitForTimeout):
            driver.run(
                self.ARGV,
                ["WAIT-FOR:FISHREADY", "SLEEP 0.8", "echo hi", "ENTER"],
                2.0, 2.0, 5.0, wait_for_timeout=4,
            )


class CliIntegrationTests(unittest.TestCase):
    """End-to-end runs against a local `cat` (or similar) under a real pty."""

    def test_early_target_death_exits_1_with_capture_and_explanation(self):
        # CLI shape of the #37 fix: the failed run still shows the
        # capture (stripped, like a successful one) on stdout, explains
        # the death and what was never sent on stderr, and exits 1 --
        # instead of exit-0 with a masquerading capture.
        result = run_cli(
            ["--cmd", 'bash -c "echo pre-death; exit 5"', *FAST,
             "a", "ENTER", "b", "ENTER"],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"pre-death", result.stdout)
        self.assertIn(b"status 5", result.stderr)
        self.assertIn(b"after 0 of 4 steps", result.stderr)
        self.assertIn(b"pty echo", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

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

    @unittest.skipUnless(shutil.which("zsh"), "zsh not available")
    def test_zsh_as_a_target_actually_executes_commands(self):
        # Shell-agnostic by construction (see README's Targets section) --
        # verify a shell other than bash actually works end to end, not
        # just assume it from reading the argv-building code. `-f`: no rc
        # files, so this doesn't depend on whatever theme/plugins happen
        # to be configured on the machine running the suite -- verified
        # directly that the NOTES.md live-redraw gotcha reproduces even
        # bare like this, not just under a customized config, so `-f`
        # doesn't dodge what this is meant to confirm still works.
        result = run_cli(
            ["--cmd", "zsh -f -i", *FAST, "echo tp-zsh-marker-hello", "ENTER"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"tp-zsh-marker-hello", result.stdout)

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

    def test_raw_step_delivers_real_escape_bytes_to_the_target(self):
        # Byte-exact proof, not just echo: stty -icanon so od can read
        # the unwritten-newline sequence straight off the pty, then od
        # shows exactly what arrived -- 1b 5b 49 = \e[I. The pty's own
        # echo of the same write shows up as ^[[I (ECHOCTL renders the
        # real ESC byte as literal two-char ^[); the literal text
        # '\e[I' would echo as four plain characters instead.
        cmd = "bash -c 'stty -icanon; od -An -tx1 -N3; cat'"
        result = run_cli(["--cmd", cmd, "--raw", *FAST, r"RAW:\e[I"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"^[[I", result.stdout)  # the echo
        self.assertNotIn(b"\\e[I", result.stdout)  # not sent as text
        self.assertIn(b"1b 5b 49", result.stdout)  # what cat received

    def test_raw_step_output_strips_like_any_escape_sequence(self):
        # Through the default ANSI strip, the sequence itself is gone --
        # only the ECHOCTL-rendered echo of it comes back.
        result = run_cli(["--cmd", "cat", *FAST, r"RAW:\e[I"])
        self.assertEqual(result.stdout, b"^[[I\n")

    def test_sleep_step_changes_nothing_about_the_capture(self):
        # End-to-end sanity: a driver-side wait between steps is
        # invisible on stdout -- no bytes sent, nothing to echo.
        result = run_cli(["--cmd", "cat", *FAST, "SLEEP 0.3", "hi", "ENTER"])
        self.assertEqual(result.stdout, b"hi\r\nhi\r\n\n")

    def test_wait_for_step_paces_until_the_target_outputs(self):
        # The marker is printed by the target ~0.4s in -- and, unlike the
        # direct driver test above, the earlier steps' own echoes exist
        # here, so the pattern deliberately contains no substring of any
        # typed step text.
        cmd = "bash -c 'sleep 0.4; echo tp-waitfor-ok; cat'"
        result = run_cli(
            ["--cmd", cmd, "--connect-wait", "0.2", "--step-delay", "0.3",
             "--settle", "0.3", "--wait-for-timeout", "5",
             "WAIT-FOR:tp-waitfor-ok", "hi", "ENTER"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"tp-waitfor-ok", result.stdout)
        self.assertIn(b"hi", result.stdout)

    def test_wait_for_step_timeout_exits_1_with_pattern_and_last_output(self):
        # The "fails loudly, not a slow no-op" requirement (#46): exit 1,
        # one-line stderr message naming the step and what was last seen,
        # and no stdout capture printed for a failed run.
        result = run_cli(
            ["--cmd", "cat", "--connect-wait", "0.2", "--step-delay", "0.2",
             "--settle", "0.2", "--wait-for-timeout", "0.5",
             "WAIT-FOR:tp-never-arrives"],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"WAIT-FOR step", result.stderr)
        self.assertIn(b"tp-never-arrives", result.stderr)
        self.assertIn(b"last output seen", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_wait_for_step_can_wait_for_a_regex(self):
        # The form that survives a line editor redrawing mid-marker
        # (#46's actual motivating case): the pattern tolerates characters
        # between the literal ones.
        cmd = "bash -c 'echo tp-ma-rk-end; cat'"
        result = run_cli(
            ["--cmd", cmd, *FAST, "--wait-for-timeout", "5",
             "WAIT-FOR:/tp-ma.?rk/"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"tp-ma-rk-end", result.stdout)

    def test_answer_queries_flag_answers_a_capability_probe(self):
        # The probe (\e[>c) itself survives ANSI stripping (its '>' is
        # outside the strip regex's parameter charset, per NOTES.md). The
        # canned DA2 reply is written into the target's input and comes
        # back through the pty's own echo -- with the ESC byte rendered
        # ECHOCTL-style as the literal two characters ^[.
        cmd = 'bash -c "printf \'\\033[>c\'; cat"'
        result = run_cli(["--cmd", cmd, "--answer-queries", *FAST, "x"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"^[[>85;3800;0c", result.stdout)
        result = run_cli(["--cmd", cmd, *FAST, "x"])
        self.assertNotIn(b"^[[>85;3800;0c", result.stdout)

    def test_bel_note_printed_to_stderr_once_per_run(self):
        # Issue #47: BEL renders as nothing in both stripped and raw
        # output; the advisory note is what makes a bell visible in a
        # capture. stdout keeps the BEL itself (stripping deliberately
        # leaves it, NOTES.md's "byte that looks empty" gotcha) but is
        # otherwise unchanged.
        cmd = 'bash -c "printf \'a\\007b\'; cat"'
        result = run_cli(["--cmd", cmd, *FAST, "x"])
        self.assertEqual(result.stdout, b"a\x07bx\n")
        self.assertIn(
            b"note: 1 BEL (0x07) byte received from the target",
            result.stderr,
        )
        result = run_cli(["--cmd", cmd, "--raw", *FAST, "x"])
        self.assertIn(b"1 BEL (0x07) byte", result.stderr)

    def test_no_bel_note_when_target_never_bells(self):
        result = run_cli(["--cmd", "cat", *FAST, "hi", "ENTER"])
        self.assertNotIn(b"BEL", result.stderr)


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
