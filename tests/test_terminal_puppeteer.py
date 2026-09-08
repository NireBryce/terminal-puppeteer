"""Tests for terminal_puppeteer.

Three layers, each importing the real package directly (it's on
sys.path via REPO_ROOT below), not loaded by path:

- Unit tests for the pure helpers (`keys.encode_step`, `cli.load_steps`,
  `ansi.ANSI`, `keys.print_key_list`) that don't need a pty at all.
- Direct tests of `driver.drain`/`driver.run` and `session.Session` --
  still real fds/a real pty/a real subprocess, just called directly
  instead of through a spawned CLI process, so they can check things
  (timing precision, fd counts, reaping) the CLI layer below can't
  reach into as easily.
- CLI integration tests that actually invoke `terminal-puppeteer.py` (the
  thin wrapper at the repo root) as a subprocess against a local `--cmd`
  target (`cat`, `printf`, `bash`) and check the captured stdout
  byte-for-byte, exercising the whole thing end to end (argparse
  validation, process termination, real shell targets) the way an agent
  actually would -- including the `--repl` sentinel protocol, driven
  through a piped stdin.

Deliberately stdlib-only (`unittest`), matching the project itself.
"""
import contextlib
import io
import os
import re
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
from terminal_puppeteer import ansi, cli, driver, keys, screen, session  # noqa: E402

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


def run_cli_input(args, stdin_bytes, timeout=30):
    # The --repl shape: stdin is the step stream, piped here.
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        input=stdin_bytes,
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


class PasteStepTests(unittest.TestCase):
    """PASTE:<text>: the payload wrapped in real bracketed-paste framing
    (\\e[200~ ... \\e[201~), sent as one write. The only way to express a
    multi-line paste in one steps-file line: with bracketed-paste mode
    enabled, a line editor treats the whole payload as one paste and
    holds its newlines literal, instead of executing each line as it
    arrives (README's bulk-paste gotcha)."""

    def test_payload_wrapped_in_bracketed_paste_framing(self):
        self.assertEqual(keys.encode_step("PASTE:hi"), b"\x1b[200~hi\x1b[201~")

    def test_prefix_is_case_insensitive(self):
        self.assertEqual(keys.encode_step("paste:hi"), b"\x1b[200~hi\x1b[201~")
        self.assertEqual(keys.encode_step("Paste:x"), b"\x1b[200~x\x1b[201~")

    def test_payload_goes_through_the_bind_style_escapes(self):
        # Same decoder RAW: uses -- the framing is the difference, not a
        # second escape syntax.
        self.assertEqual(keys.encode_step(r"PASTE:a\tb"), b"\x1b[200~a\tb\x1b[201~")
        self.assertEqual(keys.encode_step(r"PASTE:\e[3~"), b"\x1b[200~\x1b[3~\x1b[201~")
        self.assertEqual(keys.encode_step(r"PASTE:\q"), b"\x1b[200~\\q\x1b[201~")

    def test_multi_line_payload_survives_as_one_paste(self):
        # The point of the form: embedded newlines are paste contents,
        # not step separators or submits.
        self.assertEqual(
            keys.encode_step("PASTE:line1\nline2\nline3"),
            b"\x1b[200~line1\nline2\nline3\x1b[201~",
        )

    def test_it_is_one_write_framing_and_payload_together(self):
        # encode_step returns the whole thing as one value -- the driver
        # writes it with a single os.write, so the target's paste parser
        # never sees framing and contents separated.
        self.assertTrue(
            keys.encode_step("PASTE:hi").startswith(b"\x1b[200~hi\x1b[201~")
        )

    def test_empty_payload_raises(self):
        # A bare paste framing carries nothing and would only confuse
        # the target -- same shape as WAIT-FOR's empty pattern: a
        # ValueError the CLI turns into a usage error.
        for step in ("PASTE:", "paste:"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    keys.encode_step(step)
                with self.assertRaises(ValueError):
                    keys.parse_paste_step(step)

    def test_non_paste_steps_return_none(self):
        for step in ("hi", "TAB", "RAW:\\e[I", "WAIT-FOR:x"):
            with self.subTest(step=step):
                self.assertIsNone(keys.parse_paste_step(step))


class ParseResizeStepTests(unittest.TestCase):
    """RESIZE <rows> <cols>: a driver-side resize of the pty mid-run --
    TIOCSWINSZ, then the kernel SIGWINCHes the target's foreground
    group. --rows/--cols set the starting size; this changes it."""

    def test_parses_rows_and_cols(self):
        self.assertEqual(keys.parse_resize_step("RESIZE 40 120"), (40, 120))
        self.assertEqual(keys.parse_resize_step("resize 24 80"), (24, 80))
        self.assertEqual(keys.parse_resize_step("RESIZE  40\t120"), (40, 120))

    def test_non_resize_steps_return_none(self):
        # Bare 'RESIZE' mirrors bare 'SLEEP': the regex wants arguments,
        # so without them it's not recognized at all and goes out as
        # literal text.
        for step in ("TAB", "ENTER", "RESIZE", "RESIZED 40 120"):
            with self.subTest(step=step):
                self.assertIsNone(keys.parse_resize_step(step))

    def test_wrong_arity_raises(self):
        for step in ("RESIZE 40", "RESIZE 40 120 30"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    keys.parse_resize_step(step)

    def test_non_integer_dimensions_raise(self):
        for step in ("RESIZE abc 120", "RESIZE 40 8.5", "RESIZE '' 80"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    keys.parse_resize_step(step)

    def test_dimensions_below_one_raise(self):
        # A 0x0 (or 0xN) tty is exactly what the --rows/--cols defaults
        # exist to prevent -- RESIZE doesn't get to un-set it either.
        for step in ("RESIZE 0 80", "RESIZE 40 0", "RESIZE -1 80"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    keys.parse_resize_step(step)

    def test_dimensions_over_the_winsize_field_raise(self):
        # The winsize struct's fields are 16-bit shorts; beyond 65535
        # the driver's TIOCSWINSZ pack would raise struct.error mid-run
        # -- caught here, where it's a usage error instead.
        with self.assertRaises(ValueError):
            keys.parse_resize_step("RESIZE 65536 80")
        self.assertEqual(keys.parse_resize_step("RESIZE 65535 65535"),
                         (65535, 65535))


class ParseWaitForNextTests(unittest.TestCase):
    """WAIT-FOR-NEXT:<pattern>: WAIT-FOR's matching, but only over
    output arriving after the step starts -- the echo of your own
    typing and a prompt that already contained the marker cannot
    satisfy it (NOTES.md's WAIT-FOR gotcha)."""

    def test_substring_pattern(self):
        p = keys.parse_wait_for_next_step("WAIT-FOR-NEXT:ready")
        self.assertTrue(p.search(b"ready now"))
        self.assertIsNone(p.search(b"never mind"))

    def test_regex_pattern_when_wrapped_in_slashes(self):
        p = keys.parse_wait_for_next_step("WAIT-FOR-NEXT:/MA.R?K/")
        self.assertTrue(p.search(b"MA1RK"))
        self.assertIsNone(p.search(b"MAxxK"))

    def test_patterns_are_bytes(self):
        p = keys.parse_wait_for_next_step("WAIT-FOR-NEXT:/r[e]ady/")
        self.assertTrue(p.search(b"ready"))

    def test_empty_pattern_raises(self):
        with self.assertRaises(ValueError):
            keys.parse_wait_for_next_step("WAIT-FOR-NEXT:")

    def test_invalid_regex_raises(self):
        with self.assertRaises(ValueError):
            keys.parse_wait_for_next_step("WAIT-FOR-NEXT:/[/")

    def test_non_wait_for_next_steps_return_none(self):
        for step in ("TAB", "SLEEP 5", "WAIT-FOR:ready"):
            with self.subTest(step=step):
                self.assertIsNone(keys.parse_wait_for_next_step(step))

    def test_wait_for_next_is_not_also_a_wait_for_step(self):
        # No prefix ambiguity: WAIT-FOR-NEXT: does not start with
        # WAIT-FOR: (the seventh character differs), so the two parse
        # functions partition the vocabulary cleanly -- pinned here so
        # a reordering can't silently make plain WAIT-FOR swallow the
        # NEXT form.
        self.assertIsNone(keys.parse_wait_for_step("WAIT-FOR-NEXT:ready"))


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
        # non-key forms belong on it, not just in --help.
        out = self._output()
        for form in ("RAW:", "PASTE:", "SLEEP <seconds>", "RESIZE <rows> <cols>",
                     "WAIT-FOR:<pattern>", "WAIT-FOR-NEXT:<pattern>"):
            with self.subTest(form=form):
                self.assertIn(form, out)


class ScreenRenderTests(unittest.TestCase):
    """Golden-grid tests for screen.render(), one per sequence family --
    the renderer is a layout-only ANSI interpreter, so each family pins
    where characters end up, not what they look like."""

    def _render(self, raw, rows=24, cols=80):
        return screen.render(raw, rows=rows, cols=cols)

    def test_plain_text(self):
        self.assertEqual(self._render(b'hello'), 'hello')

    def test_crlf_lines(self):
        self.assertEqual(self._render(b'line1\r\nline2'), 'line1\nline2')

    def test_the_zsh_backspace_artifact_renders_clean(self):
        # The NOTES.md gotcha, fixed by rendering: 'e' typed, the editor
        # redraws via backspace, 'echo hi' overwrites it. A flat capture
        # reads 'eecho hi'; a terminal shows 'echo hi'.
        self.assertEqual(self._render(b'e\x08echo hi'), 'echo hi')

    def test_cup_positions(self):
        out = self._render(b'\x1b[2;3HX', rows=3, cols=5)
        self.assertEqual(out.split('\n')[1], '  X')

    def test_cursor_movement(self):
        # Right 3, LF down (column stays), write: X lands at (1, 3).
        out = self._render(b'\x1b[3C\nX', rows=3, cols=8)
        self.assertEqual(out.split('\n')[1], '   X')
        # CHA to column 2 (1-based): X overwrites the 'b'.
        out = self._render(b'abc\r\x1b[2GX', rows=1, cols=5)
        self.assertEqual(out, 'aXc')

    def test_el_ed_ech(self):
        # EL 0 from column 2 wipes to end of line.
        out = self._render(b'abcde\x1b[2G\x1b[K', rows=1, cols=10)
        self.assertEqual(out, 'a')
        # ED 2 clears the screen without moving the cursor, so home
        # explicitly before writing.
        out = self._render(b'aaa\r\nbbb\x1b[2J\x1b[HX', rows=3, cols=10)
        self.assertEqual(out, 'X')
        # ECH blanks 3 cells at the cursor without moving it; the X
        # then overwrites the first blanked cell.
        out = self._render(b'abcde\r\x1b[3XX', rows=1, cols=10)
        self.assertEqual(out, 'X  de')

    def test_ich_dch(self):
        # ICH 2 opens two blanks under the cursor, pushing 'abcd' right.
        out = self._render(b'abcd\r\x1b[2@', rows=1, cols=10)
        self.assertEqual(out, '  abcd')
        # DCH 2 deletes under the cursor, pulling 'cd' left.
        out = self._render(b'abcd\r\x1b[2P', rows=1, cols=10)
        self.assertEqual(out, 'cd')

    def test_il_dl(self):
        # IL opens a blank line at the cursor, pushing the rest down.
        out = self._render(b'a\r\nb\r\nc\x1b[1;1H\x1b[1L', rows=4, cols=5)
        self.assertEqual(out.split('\n'), ['', 'a', 'b', 'c'])
        # DL deletes the cursor's line, pulling the rest up.
        out = self._render(b'a\r\nb\r\nc\x1b[2;1H\x1b[1M', rows=4, cols=5)
        self.assertEqual(out.split('\n'), ['a', 'c'])

    def test_scrolls_at_the_bottom(self):
        # 24 A-lines on a 24-row screen: the first scrolls off, so 23
        # lines of A remain before the final B.
        out = self._render(b'A\r\n' * 24 + b'B')
        lines = out.split('\n')
        self.assertEqual(lines[0], 'A')
        self.assertEqual(lines[-1], 'B')
        self.assertEqual(out.count('A'), 23)

    def test_scroll_region_pins_a_status_line(self):
        # DECSTBM 1;23: rows 1-23 scroll, the status line below the
        # region stays put -- the editor-redraw case IL/DL exist for.
        # A final bare 'B' fills the row the last scroll left blank, so
        # the screen ends full and 'no blank lines' is checkable. (The
        # B-fill is deliberate: an A-fill would put an 'A' inside
        # STATUS itself and make the count off by one.)
        raw = (b'\x1b[1;23r\x1b[24;1HSTATUS\x1b[1;1H'
               + b'B\r\n' * 30 + b'B')
        out = self._render(raw, rows=24, cols=20)
        lines = out.split('\n')
        self.assertEqual(lines[-1], 'STATUS')
        self.assertNotIn('', lines)
        self.assertEqual(out.count('B'), 23)

    def test_sgr_and_mode_sequences_discard_invisibly(self):
        out = self._render(b'\x1b[31mred\x1b[0m\x1b[?2004h\x1b[>4;1m')
        self.assertEqual(out, 'red')

    def test_osc_and_dcs_discard(self):
        self.assertEqual(self._render(b'\x1b]0;title\x07ok'), 'ok')
        self.assertEqual(self._render(b'\x1b]8;;http://x\x1b\\ok'), 'ok')
        self.assertEqual(self._render(b'\x1bP+q541b\x1b\\ok'), 'ok')

    def test_truncated_sequences_render_nothing(self):
        # A real terminal is still waiting for the tail -- it shows
        # nothing. The bytes stay in --raw/--capture-out for forensics.
        self.assertEqual(self._render(b'ok\x1b[31'), 'ok')
        self.assertEqual(self._render(b'ok\x1b'), 'ok')
        self.assertEqual(self._render(b'ok\x1b]0;cut'), 'ok')

    def test_unknown_grammatical_final_discards(self):
        self.assertEqual(self._render(b'\x1b[99zok'), 'ok')

    def test_deferred_autowrap(self):
        # Filling the last column arms the wrap; it fires only when the
        # next printable arrives -- 'abcde' alone stays on row 0.
        self.assertEqual(self._render(b'abcde', rows=2, cols=5), 'abcde')
        self.assertEqual(self._render(b'abcdef', rows=2, cols=5),
                         'abcde\nf')

    def test_explicit_move_after_full_line_does_not_wrap(self):
        out = self._render(b'abcde\x1b[1;1HX', rows=2, cols=5)
        self.assertEqual(out, 'Xbcde')

    def test_wide_characters_occupy_one_cell(self):
        # Honest limitation, documented: the renderer is about layout,
        # not glyph metrics -- a double-width glyph sits in one cell.
        self.assertEqual(self._render('龼x'.encode(), rows=1, cols=4), '龼x')

    def test_control_bytes_render_nothing(self):
        # The NOTES.md SOH gotcha cuts both ways: --screen answers
        # layout questions, not byte questions -- the SOH is *gone*
        # from this view, which is exactly why od/--raw exist.
        self.assertEqual(self._render(b'a\x01b\x07c'), 'abc')

    def test_reverse_index_and_nel(self):
        # RI at the top margin scrolls down; the column is preserved,
        # so CR first puts the cursor where 'b' should land.
        out = self._render(b'a\r\x1bMb', rows=2, cols=5)
        self.assertEqual(out, 'b\na')
        # NEL is CR+LF.
        self.assertEqual(self._render(b'ab\x1bEc', rows=2, cols=5), 'ab\nc')

    def test_save_restore_cursor(self):
        # DECSC saves (0,0) after the CR; two LFs down, DECRC brings
        # the cursor back and X overwrites the 'a'.
        out = self._render(b'abc\r\x1b7\n\n\x1b8X', rows=3, cols=5)
        self.assertEqual(out.split('\n')[0], 'Xbc')

    def test_ris_resets_everything(self):
        out = self._render(b'junk\x1bcfresh', rows=2, cols=10)
        self.assertEqual(out, 'fresh')

    def test_tab_stops(self):
        out = self._render(b'a\tb', rows=1, cols=20)
        self.assertEqual(out, 'a       b')

    def test_the_blesh_probe_capture_renders_legibly(self):
        # The acceptance case from the retired PLAN.md (TODO.md's
        # --screen Done entry): ble.sh's cursor-glyph probe
        # as it lands in a capture -- an unanswered DA2 request
        # (\e[>c) followed by literal bracketed glyphs -- must render
        # to the plain text it actually is, not garbage.
        raw = b'\x1b[>c[\xe2\x96\xbd][\xef\xbc\x8b] probe tail'
        out = self._render(raw, rows=3, cols=40)
        self.assertTrue(out.startswith('[▽][＋] probe tail'), repr(out))


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
        # The prefixed step forms are their own exact-match vocabulary --
        # 'RAW:CR' is three-quarters CR by difflib's math and is not a
        # typo of it, and a malformed form gets a proper usage error at
        # validation time instead. ('wait 1' is deliberate here no more:
        # lowercase sleep/wait now get their own advisory, tested below.)
        for step in ("RAW:CR", "RAW:TAB", "PASTE:CR", "SLEEP 5", "SLEEP abc",
                     "WAIT-FOR:ENTER", "WAIT-FOR-NEXT:ENTER"):
            with self.subTest(step=step):
                self.assertEqual(self._warns(step), "")

    def test_advises_on_lowercase_sleep_and_wait(self):
        # The NOTES.md mistake in advisory form: a lowercase 'sleep 30'
        # IS a recognized step -- a driver-side wait that sends nothing --
        # but reads like a command being typed. The note points at the
        # RAW: form that types it for real.
        stderr = self._warns("sleep 30")
        self.assertIn("driver-side", stderr)
        self.assertIn("sends nothing", stderr)
        self.assertIn(r"RAW:sleep 30\r", stderr)
        self.assertIn("driver-side", self._warns("wait 5"))

    def test_no_advisory_for_uppercase_or_non_wait_spellings(self):
        # The uppercase spellings are the documented driver form -- no
        # note. Only the looks-like-a-typed-command shape advises, so
        # mixed case and near-misses stay silent too.
        for step in ("SLEEP 30", "WAIT 5", "Sleep 2", "sleepy 5",
                     "git sleep 5"):
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

    def test_strips_csi_with_gt_private_marker(self):
        # The parameter area is 0x30-0x3F, so > = < ? are all legal
        # there -- ble.sh emits ESC[>4;1m (XTMODKEYS) and ESC[>c (DA2
        # request), both of which survived stripping when only ? was
        # allowed, visible as garbage in stripped captures over ssh.
        for seq in (b"\x1b[>4;1m", b"\x1b[>4;2m", b"\x1b[>c", b"\x1b[>1u"):
            with self.subTest(seq=seq):
                self.assertEqual(ansi.ANSI.sub(b"", seq + b"x"), b"x")

    def test_strips_da3_request_csi(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b[=cq"), b"q")

    def test_strips_colon_sgr(self):
        # Modern SGR colon syntax (ITU T.416): 38:2::r:g:b.
        raw = b"\x1b[38:2::255:0:0mcolored\x1b[0m"
        self.assertEqual(ansi.ANSI.sub(b"", raw), b"colored")

    def test_strips_csi_with_intermediate_byte(self):
        # CSI space q (cursor style) has an intermediate byte 0x20-0x2F
        # between parameters and final.
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b[ qstyle\x1b[?25h"), b"style")

    def test_strips_dcs_string(self):
        # DCS (ESC P ... ESC \): fish and ble.sh speak XTGETTCAP
        # (ESC P +q <cap> ESC \) and --answer-queries' XTVERSION reply
        # is DCS; there was no DCS branch at all before.
        for seq in (b"\x1bP+q541b\x1b\\", b"\x1bP>|Konsole (250203)\x1b\\"):
            with self.subTest(seq=seq):
                self.assertEqual(ansi.ANSI.sub(b"", seq + b"x"), b"x")

    def test_leaves_unterminated_dcs_untouched(self):
        # Same strip-what-is-provable policy as OSC: no ST, no strip.
        raw = b"\x1bP+q541b truncated"
        self.assertEqual(ansi.ANSI.sub(b"", raw), raw)

    def test_does_not_corrupt_utf8_whose_bytes_look_like_csi_syntax(self):
        # ӣ is U+04E3 = \xd3\x9b in UTF-8; 0x9b is the 8-bit CSI. The
        # regex must key on the 7-bit ESC introducer only, never on raw
        # bytes inside multi-byte characters.
        self.assertEqual(ansi.ANSI.sub(b"", b"\xd3\x9bplain"), b"\xd3\x9bplain")

    def test_strips_osc_sequence_terminated_by_bel(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b]0;window title\x07rest"), b"rest")

    def test_strips_osc_sequence_terminated_by_st(self):
        # The other legal OSC terminator (ESC \) -- used by fish's OSC
        # 133 prompt marks, our own --answer-queries OSC 11 reply, and
        # OSC 8 hyperlinks. Only stripping the BEL form left the whole
        # sequence sitting in "stripped" output as visible garbage
        # (seen directly in this project's own repro captures).
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b]0;title\x1b\\rest"), b"rest")

    def test_strips_osc8_hyperlinks(self):
        raw = b"\x1b]8;;http://example.com\x1b\\link text\x1b]8;;\x1b\\end"
        self.assertEqual(ansi.ANSI.sub(b"", raw), b"link textend")

    def test_strips_st_terminated_osc_containing_esc_like_text(self):
        # A payload stops at the first ESC: the ST is ESC \, so a
        # payload that already ended and a new sequence begins must not
        # swallow past it.
        raw = b"\x1b]0;a\x1b\\x\x1b]0;b\x1b\\y"
        self.assertEqual(ansi.ANSI.sub(b"", raw), b"xy")

    def test_leaves_unterminated_osc_untouched(self):
        # Strip-what-is-provable policy: no terminator, no strip.
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b]0;truncated"), b"\x1b]0;truncated")

    def test_strips_charset_selection(self):
        self.assertEqual(ansi.ANSI.sub(b"", b"\x1b(Btext\x1b)0"), b"text")

    def test_strips_known_single_char_escapes(self):
        for ch in b"=>78DEMc":
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

    def test_zero_or_negative_rows_cols_are_usage_errors(self):
        # A 0x0 tty is exactly the bug the window-size defaults exist to
        # fix -- the flags may override the size, not un-set it.
        for flag in ("--rows", "--cols"):
            with self.subTest(flag=flag):
                result = run_cli(["--cmd", "cat", flag, "0", "x"])
                self.assertEqual(result.returncode, 2)
                self.assertIn(flag.encode(), result.stderr)


class VersionAndListKeysTests(unittest.TestCase):
    def test_pyproject_and_package_version_are_in_sync(self):
        # Both carry the version by hand (each with a "keep in sync"
        # comment) -- this turns the comment into a check. Reads the
        # one line straight out of pyproject.toml: stdlib tomllib needs
        # 3.11 and the suite supports 3.9.
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        m = re.search(r'^version = "(.*?)"', pyproject, re.M)
        self.assertIsNotNone(m, "no version line in pyproject.toml")
        self.assertEqual(m.group(1), cli.__version__)

    def test_python_dash_m_entry_point(self):
        # __main__.py makes `python -m terminal_puppeteer` work like
        # the root wrapper and the installed script -- pin that.
        result = subprocess.run(
            [sys.executable, "-m", "terminal_puppeteer", "--version"],
            capture_output=True, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(cli.__version__.encode(), result.stdout)

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

    def test_rows_and_cols_flags_reach_the_tty(self):
        out = driver.run(
            ["bash", "-c", "stty size; cat"], ["x"], 0.5, 0.5, 0.5,
            rows=40, cols=120,
        )
        self.assertEqual(out.split(b"\r\n", 1)[0], b"40 120")

    def test_wait_for_fails_fast_when_the_target_dies_mid_wait(self):
        # A dead target can never produce the pattern, so burning the
        # whole WAIT-FOR budget would be a slow no-op with a misleading
        # "no match" message -- the death is the real failure and it
        # surfaces immediately, naming the status and the step. The
        # target must outlive the step's start (connect-wait) for the
        # mid-wait path to be the one that fires; a target already dead
        # before the step is run()'s own check.
        with self.assertRaises(driver.TargetDied) as cm:
            driver.run(
                ["bash", "-c", "sleep 1.5; exit 3"],
                ["WAIT-FOR:tp-never-arrives"],
                0.3, 0.2, 0.3, wait_for_timeout=15,
            )
        self.assertIn("status 3", str(cm.exception))
        self.assertIn("was still waiting", str(cm.exception))
        self.assertIn("cannot arrive", str(cm.exception))

    def test_target_dying_with_steps_remaining_fails_loudly(self):
        # The #37 masquerade's fix: a dying target fails the run loudly
        # instead of quietly absorbing steps -- exit via TargetDied,
        # the exit status named, the partial capture attached (the
        # target's dying words are the diagnostic).
        #
        # The shape is #58-robust: the death is TRIGGERED by the ENTER
        # step, the first WAIT-FOR paces on the dying words actually
        # arriving (bash has then run its last statement), and the
        # second WAIT-FOR is the catcher -- its liveness loop keeps
        # checking until the target is really dead, so nothing here
        # races a fixed instant. That matters more than it sounds:
        # under load bash's STARTUP can take ~1s (typed input just
        # queues; the kernel echoes it either way), and even the gap
        # between bash's last two statements once stretched past half
        # a second -- any single-point-in-time check can lose that
        # race, and a lost race used to mean a quiet exit 0.
        with self.assertRaises(driver.TargetDied) as cm:
            driver.run(
                ["bash", "-c", "read line; echo DYING-WORDS; exit 5"],
                ["hello", "ENTER", "WAIT-FOR:DYING-WORDS",
                 "WAIT-FOR:tp-never-arrives"],
                0.3, 0.2, 0.3, wait_for_timeout=10,
            )
        self.assertIn(b"DYING-WORDS", cm.exception.output)
        self.assertIn("status 5", str(cm.exception))
        # Deterministic by construction: WHICH check catches the death
        # varies with timing -- a fast death (the normal case) lands
        # during the ENTER step's own drain and the very next pre-step
        # check raises the i-of-N message; a slow one is absorbed by
        # the final WAIT-FOR's liveness loop ("was still waiting" /
        # "cannot arrive", budget 10s). Either way the run fails
        # loudly; nothing races a fixed instant. The two message
        # templates are each pinned exactly where the timing IS
        # deterministic: the i-of-N shape by the Session tests (which
        # wait out the death explicitly and know the index), the
        # liveness-loop shape by test_wait_for_fails_fast... (#58:
        # bash's startup, and even the gap between its last two
        # statements, can stretch past any fixed window under load).

    def test_target_killed_by_a_signal_names_the_signal(self):
        # Same #58-robust shape, with a signal death: the target echoes
        # a marker, then kills itself; the check after the pacing beat
        # sees the SIGKILL.
        with self.assertRaises(driver.TargetDied) as cm:
            driver.run(
                ["bash", "-c", "read line; echo SIGNALLED; kill -9 $$"],
                ["hi", "ENTER", "WAIT-FOR:SIGNALLED",
                 "WAIT-FOR:tp-never-arrives"],
                0.3, 0.2, 0.3, wait_for_timeout=10,
            )
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

    def test_paste_step_delivers_real_bracketed_paste_bytes(self):
        # Byte-exact proof the PASTE form sends framing and payload as
        # one write, same shape as the RAW od test above: stty -icanon
        # so od reads it straight off the pty. Expected bytes:
        # 1b 5b 32 30 30 7e 'h' 'i' 1b 5b 32 30 31 7e
        # = \e[200~hi\e[201~. The pty's own echo shows up ECHOCTL-style
        # as ^[[200~..., the literal text 'PASTE:hi' would echo as text.
        out = driver.run(
            ["bash", "-c", "stty -icanon; od -An -tx1 -N14; cat"],
            ["PASTE:hi"], 0.5, 0.3, 0.3,
        )
        self.assertIn(b"1b 5b 32 30 30 7e 68 69 1b 5b 32 30 31 7e", out)

    def test_resize_step_resizes_the_pty_mid_run(self):
        # stty reads the tty directly -- no shell cooperation needed --
        # so seeing 24 80 then 40 120 proves the TIOCSWINSZ landed and
        # SIGWINCH was delivered between the two reads.
        out = driver.run(
            ["bash", "--norc", "-i"],
            ["stty size", "ENTER", "RESIZE 40 120", "stty size", "ENTER"],
            1.0, 1.0, 0.5,
        )
        self.assertIn(b"24 80", out)
        self.assertIn(b"40 120", out)

    def test_wait_for_next_step_ignores_output_from_before_the_step(self):
        # The marker arrives during connect-wait, then never again: a
        # plain WAIT-FOR would match it instantly; NEXT must not, and
        # the wait must time out rather than silently succeed.
        with self.assertRaises(driver.WaitForTimeout):
            driver.run(
                ["bash", "-c", "echo TP-NEXT-MARK; cat"],
                ["WAIT-FOR-NEXT:TP-NEXT-MARK"],
                0.1, 0.1, 0.3, wait_for_timeout=1,
            )

    def test_wait_for_next_step_matches_output_arriving_after_it(self):
        # Same marker twice: once before the step (must not match it),
        # once after (must). The wait succeeding at all proves the
        # boundary was honored -- the first occurrence was already in
        # the accumulator when the step started.
        argv = ["bash", "-c", "echo TP-NEXT-MARK; (sleep 0.4; echo TP-NEXT-MARK) & cat"]
        out = driver.run(
            argv, ["WAIT-FOR-NEXT:TP-NEXT-MARK"], 0.2, 0.2, 0.3,
            wait_for_timeout=5,
        )
        self.assertEqual(out.count(b"TP-NEXT-MARK"), 2)

    def test_wait_for_next_step_ignores_the_echo_of_earlier_steps(self):
        # The NOTES.md gotcha, fixed by form: the marker exists in the
        # stream twice -- once as the pty's echo of the typed step
        # (before the NEXT step starts), once in the target's reply
        # (after). The target sleeps longer than step-delay, so the
        # NEXT step starts before the reply exists: matching it at all
        # proves the boundary was honored, and the count proves the
        # wait didn't end on the echo (a boundary-less match would end
        # the wait early and miss the reply entirely).
        argv = ["bash", "-c", "read line; sleep 0.6; echo reply-$line; cat"]
        out = driver.run(
            argv,
            ["marker-99", "ENTER", "WAIT-FOR-NEXT:marker-99"],
            0.2, 0.3, 0.3, wait_for_timeout=5,
        )
        self.assertIn(b"reply-marker-99", out)
        self.assertEqual(out.count(b"marker-99"), 2)

    def test_capture_out_writes_the_raw_capture(self):
        # The file is the raw stream exactly as returned -- --raw's
        # stripping is the CLI's stdout business and must not touch it.
        argv = ["bash", "-c", "printf 'MA\\033[31mRK\\n'; cat"]
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            out = driver.run(
                argv, ["x"], 0.3, 0.2, 0.3, capture_path=path,
            )
            with open(path, "rb") as f:
                self.assertEqual(f.read(), out)
            self.assertIn(b"\x1b[31m", out)
        finally:
            os.unlink(path)

    def test_capture_out_written_when_the_target_dies_mid_run(self):
        # The partial capture is the diagnostic -- the failure path is
        # exactly what the file is for. Death triggered by the ENTER
        # step and paced with WAIT-FOR + a SLEEP beat (#58-robust shape,
        # same reasoning as the failing-loudly test above).
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(driver.TargetDied) as cm:
                driver.run(
                    ["bash", "-c", "read line; echo DYING-WORDS; exit 5"],
                    ["hello", "ENTER", "WAIT-FOR:DYING-WORDS",
                     "WAIT-FOR:tp-never-arrives"],
                    0.3, 0.2, 0.3, wait_for_timeout=10, capture_path=path,
                )
            with open(path, "rb") as f:
                self.assertEqual(f.read(), cm.exception.output)
            self.assertIn(b"DYING-WORDS", cm.exception.output)
        finally:
            os.unlink(path)

    def test_capture_out_written_when_a_wait_for_times_out(self):
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(driver.WaitForTimeout):
                driver.run(
                    ["bash", "-c", "echo early; cat"],
                    ["WAIT-FOR:tp-never-arrives"],
                    0.1, 0.1, 0.3, wait_for_timeout=0.5, capture_path=path,
                )
            with open(path, "rb") as f:
                self.assertIn(b"early", f.read())
        finally:
            os.unlink(path)


class SessionTests(unittest.TestCase):
    """Direct tests of session.Session -- the core driver.run() and
    --repl both drive. Real pty, real subprocess, called directly so
    the fd-leak/reap guarantees are pinned on the class itself, not
    just observed through run() (RunDirectTests' shape)."""

    def test_send_step_types_and_drains(self):
        s = session.Session(['cat'], step_delay=0.3, connect_wait=0.2).start()
        try:
            s.send_step('hi')
            self.assertIn(b'hi', s.output)
        finally:
            s.close()

    def test_send_step_dispatches_the_driver_forms(self):
        # The dispatch belongs to Session now, not run(): a SLEEP writes
        # nothing (cat produces nothing on its own, so an empty capture
        # is the proof) and a PASTE arrives framed.
        s = session.Session(['cat'], step_delay=0.3, connect_wait=0.2).start()
        try:
            s.send_step('SLEEP 0.2')
            self.assertEqual(s.output, b'')
            s.send_step('PASTE:x')
            self.assertIn(b'^[[200~x^[[201~', s.output)
        finally:
            s.close()

    def test_expect_matches_output(self):
        argv = ['bash', '-c', 'sleep 0.4; echo READY-7; cat']
        s = session.Session(argv, step_delay=0.2, connect_wait=0.2,
                            wait_for_timeout=5).start()
        try:
            s.expect(re.compile(b'READY-7'))
            self.assertIn(b'READY-7', s.output)
        finally:
            s.close()

    def test_expect_timeout_raises(self):
        s = session.Session(['cat'], step_delay=0.2, connect_wait=0.2,
                            wait_for_timeout=0.5).start()
        try:
            with self.assertRaises(session.WaitForTimeout):
                s.expect(re.compile(b'tp-never-arrives'))
        finally:
            s.close()

    def test_expect_only_new_ignores_output_from_before(self):
        # The marker lands during connect-wait; an only_new expect must
        # not match it and must time out rather than silently succeed.
        argv = ['bash', '-c', 'echo TP-SESS-MARK; cat']
        s = session.Session(argv, step_delay=0.2, connect_wait=0.3,
                            wait_for_timeout=0.8).start()
        try:
            with self.assertRaises(session.WaitForTimeout):
                s.expect(re.compile(b'TP-SESS-MARK'), only_new=True)
        finally:
            s.close()

    def test_resize_changes_the_pty_size(self):
        s = session.Session(['bash', '--norc', '-i'], step_delay=0.6,
                            connect_wait=0.8, settle=0.5).start()
        try:
            s.send_step('stty size')
            s.send_step('ENTER')
            self.assertIn(b'24 80', s.output)
            s.resize(40, 120)
            s.send_step('stty size')
            s.send_step('ENTER')
            self.assertIn(b'40 120', s.output)
        finally:
            s.close()

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'no /proc/self/fd here')
    def test_close_reaps_the_target_and_leaks_no_fds(self):
        before = len(os.listdir('/proc/self/fd'))
        s = session.Session(['cat'], step_delay=0.2, connect_wait=0.2).start()
        s.close()
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))
        self.assertIsNotNone(s.proc)
        self.assertIsNotNone(s.proc.poll())  # reaped, not merely killed

    def test_close_is_idempotent(self):
        s = session.Session(['cat'], step_delay=0.2, connect_wait=0.2).start()
        s.close()
        s.close()  # must not raise; finally blocks call it unconditionally

    def test_target_death_raises_with_capture_attached(self):
        # Death triggered by the ENTER step, then the test WAITS for
        # the death it caused (the process handle is right here) before
        # asserting the guard fires -- no fixed window to race (#58:
        # startup latency under load can stretch past any fixed check).
        s = session.Session(
            ['bash', '-c', 'read line; echo pre-death; exit 5'],
            step_delay=0.2, connect_wait=0.3,
        ).start()
        try:
            s.send_step('hello')
            s.send_step('ENTER')
            deadline = time.time() + 10
            while s.proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(s.proc.poll())
            with self.assertRaises(session.TargetDied) as cm:
                s.send_step('a', on_death="after 2 of 3 steps; "
                                          "1 not sent, next would be 'a'")
            self.assertIn(b'pre-death', cm.exception.output)
            self.assertIn("after 2 of 3 steps", str(cm.exception))
        finally:
            s.close()

    def test_default_death_narrative_names_the_step(self):
        # A library caller sending without run()'s narrative still gets
        # the masquerade warning (#37) naming what was never read.
        # Same wait-for-the-death-you-caused shape as above.
        s = session.Session(['bash', '-c', 'read line; exit 5'],
                            step_delay=0.2, connect_wait=0.3).start()
        try:
            s.send_step('hi')
            s.send_step('ENTER')
            deadline = time.time() + 10
            while s.proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(s.proc.poll())
            with self.assertRaises(session.TargetDied) as cm:
                s.send_step('b')
            self.assertIn("before step 'b' could be sent", str(cm.exception))
            self.assertIn("pty echo", str(cm.exception))
        finally:
            s.close()

    def test_capture_path_written_on_close(self):
        with tempfile.NamedTemporaryFile(suffix='.cap', delete=False) as f:
            path = f.name
        try:
            s = session.Session(['cat'], step_delay=0.3, connect_wait=0.2,
                                capture_path=path).start()
            s.send_step('x')
            s.close()
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), s.output)
        finally:
            os.unlink(path)

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'no /proc/self/fd here')
    def test_failed_start_leaks_no_fds(self):
        # The caller-side shape the docstring prescribes: close() in a
        # finally even when start() itself raised -- and it must be a
        # no-op then, not a second fd closer or a crash.
        before = len(os.listdir('/proc/self/fd'))
        s = session.Session(['tp-definitely-not-a-real-command-xyz'],
                            step_delay=0.1, connect_wait=0.1)
        with self.assertRaises(OSError):
            s.start()
        s.close()
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))


def _fish_major():
    """None when fish is absent; its major version otherwise. The fish
    tests were verified against fish 4.x (issue #37's chase) -- 3.x
    probes less on startup and takes different code paths, so CI
    installs the release-4 series and other fish skips these tests."""
    fish = shutil.which("fish")
    if not fish:
        return None
    out = subprocess.run(
        [fish, "--version"], capture_output=True, text=True
    ).stdout
    m = re.search(r"version (\d+)", out)
    return int(m.group(1)) if m else None


_FISH4 = (_fish_major() or 0) >= 4


def _localhost_ssh():
    """True if a real ssh transport to localhost works without
    interaction (key auth already in place). The ssh-transport tests
    exercise the actual `ssh -tt` path -- remote pty allocation, remote
    shell over the protocol -- against the local sshd, so they run
    wherever passwordless localhost ssh is set up (author machine, CI
    after its sshd step) and skip elsewhere instead of failing on
    ambient auth differences."""
    if not shutil.which("ssh"):
        return False
    probe = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "-o", "StrictHostKeyChecking=accept-new", "localhost", "true"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=20,
    )
    return probe.returncode == 0


_LOCAL_SSH = _localhost_ssh()


@unittest.skipUnless(_FISH4, "fish 4 not on PATH")
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


@unittest.skipUnless(_LOCAL_SSH, "passwordless localhost ssh not available")
class SshTransportTests(unittest.TestCase):
    """The live --ssh verification, kept as tests: a real ssh transport
    -- `ssh -tt`, remote pty allocation, remote shell over the protocol
    -- against the local sshd (the genuinely-different-host case is
    SecondHostSshTests below). Targets a plain remote
    `bash --norc --noprofile -i` on purpose: the point here is the
    transport, not the author's dotfiles (whose ble.sh redraw storm eats
    keystrokes on its own schedule -- NOTES.md)."""

    ARGV = ["ssh", "-tt", "localhost", "bash", "--norc", "--noprofile", "-i"]

    # positional tail for driver.run: step_delay, settle, connect_wait.
    def _delays(self):
        return (1.5, 2.5, 4.0)

    def test_ssh_transport_round_trip_window_size_and_job_control(self):
        # The three battery checks that motivated the TODO item, over
        # one connection: commands execute end to end (the marker only
        # appears if the remote shell ran the line), the tool's 24x80
        # window propagates through ssh's pty request (surfaced as a
        # real finding during the live verification: a client without a
        # tty yields 0 0 on the remote side), and the remote shell has
        # job control over -tt ($- gains its m).
        out = driver.run(
            self.ARGV,
            ["echo SSHRT-$((20+22))", "ENTER", "stty size", "ENTER",
             "echo flags=$-", "ENTER"],
            *self._delays(),
        )
        self.assertIn(b"SSHRT-42", out)
        self.assertIn(b"24 80", out)
        flags = re.search(rb"flags=([a-zA-Z]+)", out)
        self.assertIsNotNone(flags)
        self.assertIn(b"m", flags.group(1))

    def test_ssh_arg_reaches_the_remote_ssh_process(self):
        # The = form: argparse can't take `--ssh-arg -o` (a value that
        # starts with - reads as the next option), which is exactly what
        # the README used to document as its example. End to end through
        # the CLI so the parse and the passthrough are both pinned.
        result = run_cli(
            ["--ssh", "localhost", "--ssh-arg=-o", "--ssh-arg=ConnectTimeout=5",
             "--connect-wait", "4", "--step-delay", "1.5", "--settle", "2.5",
             "echo SSHARG-OK", "ENTER"],
            timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"SSHARG-OK", result.stdout)


@unittest.skipUnless(
    os.environ.get("TERMINAL_PUPPETEER_SSH_HOST"),
    "set TERMINAL_PUPPETEER_SSH_HOST (passwordless key auth assumed) "
    "to verify the tool against a genuinely different machine",
)
class SecondHostSshTests(unittest.TestCase):
    """The half of the --ssh verification loopback can't cover: a
    different machine's sshd, OS, and network path. Opt-in via the
    environment rather than skipped forever -- CI has no second host,
    and an unconfigured run must not probe anyone's machines. Everything
    typed is read-only (echo/stty) and the session is bash --norc, so
    the target machine sees a few log lines and nothing else."""

    HOST = os.environ.get("TERMINAL_PUPPETEER_SSH_HOST", "")

    def test_second_host_round_trip_window_size_and_job_control(self):
        out = driver.run(
            ["ssh", "-tt", self.HOST, "bash", "--norc", "--noprofile", "-i"],
            ["echo SECONDHOST-$((40+2))", "ENTER", "stty size", "ENTER",
             "echo flags=$-", "ENTER"],
            1.5, 2.5, 5.0,
        )
        self.assertIn(b"SECONDHOST-42", out)
        self.assertIn(b"24 80", out)
        flags = re.search(rb"flags=([a-zA-Z]+)", out)
        self.assertIsNotNone(flags)
        self.assertIn(b"m", flags.group(1))

    def test_second_host_interrupt_reaches_the_remote_foreground_job(self):
        out = driver.run(
            ["ssh", "-tt", self.HOST, "bash", "--norc", "--noprofile", "-i"],
            ["RAW:sleep 3\\r", "WAIT-FOR:/sleep 3[\\r\\n]/", "SLEEP 0.5",
             "CTRL-C", "SLEEP 1", "echo result-$?", "ENTER",
             "WAIT-FOR:/result-1[0-9][0-9]/"],
            1.5, 3.0, 5.0,
        )
        self.assertIn(b"result-130", out)

    def _delays(self):
        # positional tail for driver.run: step_delay, settle,
        # connect_wait (defaults matching the flags above).
        return (1.5, 2.5, 4.0)


class CliIntegrationTests(unittest.TestCase):
    """End-to-end runs against a local `cat` (or similar) under a real pty."""

    def test_target_death_exits_1_with_capture_and_explanation(self):
        # CLI shape of the #37 fix: the failed run still shows the
        # capture (stripped, like a successful one) on stdout, explains
        # the death and what was never sent on stderr, and exits 1 --
        # instead of exit-0 with a masquerading capture. The death is
        # TRIGGERED by the ENTER step and paced with WAIT-FOR + a SLEEP
        # beat (#58-robust shape): WAIT-FOR proves bash ran its last
        # statement, so the check that fires is not racing process
        # startup -- under load bash can take ~1s to even reach its
        # first command, and a startup-exiting target used to stay
        # "alive" past every fixed check, turning the run into a quiet
        # exit 0.
        result = run_cli(
            ["--cmd", 'bash -c "read line; echo pre-death; exit 5"', *FAST,
             "hello", "ENTER", "WAIT-FOR:pre-death",
             "WAIT-FOR:tp-never-arrives"],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"pre-death", result.stdout)
        self.assertIn(b"status 5", result.stderr)
        # Which message template fires varies with timing (see the
        # direct test above); the loud-failure shape does not.
        self.assertNotIn(b"Traceback", result.stderr)

    def test_rows_and_cols_flags_end_to_end(self):
        result = run_cli(
            ["--cmd", "bash -c 'stty size; cat'", "--rows", "40",
             "--cols", "120", *FAST, "x"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"40 120", result.stdout)

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

    def test_paste_step_arrives_framed(self):
        # End-to-end shape of PASTE at the CLI layer: the pty echo of
        # the single write, ECHOCTL-rendered (ESC as ^[), nothing sent
        # as literal 'PASTE:...' text.
        result = run_cli(["--cmd", "cat", *FAST, "PASTE:hi"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"^[[200~hi^[[201~\n")

    def test_empty_paste_step_is_a_clean_usage_error(self):
        result = run_cli(["--cmd", "cat", "PASTE:"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"PASTE step", result.stderr)
        self.assertIn(b"PASTE:", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_resize_step_end_to_end(self):
        # The RESIZE verification from the plan, through the whole CLI:
        # stty reads the tty directly, so 24 80 -> 40 120 across the
        # step is the SIGWINCH/TIOCSWINSZ delivery proof.
        result = run_cli(
            ["--cmd", "bash --norc -i", "--connect-wait", "0.8",
             "--step-delay", "0.6", "--settle", "0.5",
             "stty size", "ENTER", "RESIZE 40 120", "stty size", "ENTER"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"24 80", result.stdout)
        self.assertIn(b"40 120", result.stdout)

    def test_malformed_resize_steps_are_clean_usage_errors(self):
        for step in ("RESIZE abc 80", "RESIZE 40", "RESIZE 40 120 30",
                     "RESIZE 0 80", "RESIZE 70000 80"):
            with self.subTest(step=step):
                result = run_cli(["--cmd", "cat", step])
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"RESIZE step", result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_empty_wait_for_next_step_is_a_clean_usage_error(self):
        result = run_cli(["--cmd", "cat", "WAIT-FOR-NEXT:"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"WAIT-FOR-NEXT step", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_wait_for_next_step_ignores_the_typed_echo_end_to_end(self):
        # The discriminating end-to-end: the marker is typed (so its
        # echo precedes the NEXT step), and the target only prints it
        # again 0.6s in -- past FAST's settle. If NEXT wrongly matched
        # the echo, the run would end before the real output and the
        # count would be 1; matching only post-step output gives 2.
        cmd = "bash -c 'read line; sleep 0.6; echo tp-next-ok; cat'"
        result = run_cli(
            ["--cmd", cmd, "--connect-wait", "0.3", "--step-delay", "0.3",
             "--settle", "0.3", "--wait-for-timeout", "5",
             "tp-next-ok", "ENTER", "WAIT-FOR-NEXT:tp-next-ok"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(result.stdout.count(b"tp-next-ok"), 2)

    def test_capture_out_flag_writes_raw_capture_stdout_stays_stripped(self):
        # --capture-out never changes stdout (stripped by default, as
        # always); the file gets the raw stream, not the stripped one.
        cmd = "bash -c \"printf 'a\\033[31mb\\033[0mc'; cat\""
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            result = run_cli(["--cmd", cmd, "--capture-out", path, *FAST, "x"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"abcx\n")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"a\x1b[31mb\x1b[0mcx")
        finally:
            os.unlink(path)

    def test_capture_out_file_written_when_the_target_dies(self):
        # The failure path is the point: a failed run leaves the partial
        # capture in the file, same content the exit-1 stdout shows.
        # Death triggered by the ENTER step, paced with WAIT-FOR + a
        # SLEEP beat (#58-robust shape).
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            result = run_cli(
                ["--cmd", 'bash -c "read line; echo pre-death; exit 5"',
                 "--capture-out", path, *FAST,
                 "hello", "ENTER", "WAIT-FOR:pre-death",
                 "WAIT-FOR:tp-never-arrives"],
            )
            self.assertEqual(result.returncode, 1)
            with open(path, "rb") as f:
                self.assertIn(b"pre-death", f.read())
        finally:
            os.unlink(path)

    def test_lowercase_sleep_step_prints_the_advisory_to_stderr(self):
        # The advisory rides the same validation pass as the near-miss
        # warning, so it lands on stderr before the run starts.
        result = run_cli(["--cmd", "cat", *FAST, "sleep 0.2", "hi"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"driver-side", result.stderr)
        self.assertIn(b"RAW:sleep 0.2\\r", result.stderr)


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


class ReplTests(unittest.TestCase):
    """The --repl sentinel protocol, driven through a piped stdin: every
    step line gets exactly one response (its new output, then
    '<< DONE'; '<< DEAD <why>' when the step fails), '#' lines are
    comments, ':quit'/EOF settle-and-close with exit 0, DEAD exits 1."""

    def _repl(self, stdin_bytes, *extra, timeout=30):
        return run_cli_input(
            ['--cmd', 'cat', *FAST, '--repl', *extra], stdin_bytes, timeout
        )

    def test_sentinel_protocol_through_quit(self):
        result = self._repl(b'hi\nENTER\n:quit\n')
        self.assertEqual(result.returncode, 0)
        # Startup block + one per step: exactly one DONE per response.
        self.assertEqual(result.stdout.count(b'<< DONE'), 3)
        self.assertTrue(result.stdout.endswith(b'<< DONE\n'))
        self.assertIn(b'hi', result.stdout)

    def test_eof_settles_and_closes(self):
        result = self._repl(b'hi\nENTER\n')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count(b'<< DONE'), 3)

    def test_comments_and_blank_lines_are_skipped(self):
        # Not steps, so no sentinel for them -- DONE only for the real
        # step plus the startup block.
        result = self._repl(b'# a comment\n\nhi\n:quit\n')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count(b'<< DONE'), 2)

    def test_dead_sentinel_exits_1(self):
        # Deterministic death ordering: the target exits after the
        # ENTER step completes (read line, echo it, exit), so the
        # following step is the one that hits the guard -- no race on
        # startup timing (issue #58's shape).
        result = run_cli_input(
            ['--cmd', 'bash -c "read line; echo got-$line; exit 4"',
             *FAST, '--repl'],
            b'hello\nENTER\nafter\n',
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count(b'<< DONE'), 3)  # startup + 2 steps
        self.assertIn(b'got-hello', result.stdout)
        self.assertIn(b'<< DEAD target exited with status 4', result.stdout)
        self.assertIn(b"before step 'after' could be sent", result.stdout)

    def test_malformed_step_is_a_usage_error(self):
        # Same vocabulary, same rejection as the one-shot path: 'SLEEP
        # abc' must never be typed into the target mid-session either.
        result = self._repl(b'SLEEP abc\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'wait step', result.stderr)
        self.assertNotIn(b'Traceback', result.stderr)

    def test_repl_rejects_positional_steps(self):
        # stdin IS the step stream in --repl mode; positional steps
        # would collide with it.
        result = run_cli(["--cmd", "cat", *FAST, "--repl", "x"])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'no positional steps', result.stderr)

    def test_repl_rejects_steps_file(self):
        result = run_cli(["--cmd", "cat", *FAST, "--repl",
                          '--steps-file', 'some-file.txt'])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'--steps-file does not apply', result.stderr)

    def test_raw_flag_keeps_escapes_in_the_deltas(self):
        # Delta stripping follows --raw the same way whole-capture
        # printing does: default strips the printf's color codes out of
        # the startup block, --raw keeps them.
        cmd = "bash -c \"printf 'a\\033[31mb\\033[0mc'; cat\""
        result = run_cli_input(['--cmd', cmd, *FAST, '--repl'], b':quit\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'ab', result.stdout)
        self.assertNotIn(b'\x1b[', result.stdout)
        result = run_cli_input(['--cmd', cmd, '--raw', *FAST, '--repl'],
                               b':quit\n')
        self.assertIn(b'\x1b[31m', result.stdout)

    def test_capture_out_composes_with_repl(self):
        # The full-session capture file, same raw-bytes guarantee as the
        # one-shot path, written by Session.close at the end.
        with tempfile.NamedTemporaryFile(suffix='.cap', delete=False) as f:
            path = f.name
        try:
            result = self._repl(b'hi\n:quit\n', '--capture-out', path)
            self.assertEqual(result.returncode, 0)
            with open(path, 'rb') as f:
                self.assertIn(b'hi', f.read())
        finally:
            os.unlink(path)


class ScreenCliTests(unittest.TestCase):
    """--screen through the whole CLI: the grid replaces the byte
    stream on stdout, --capture-out still gets the raw bytes, and the
    flag refuses to combine with --repl."""

    def test_screen_renders_the_final_grid_end_to_end(self):
        # The integration acceptance shape: prompt/echo appears ONCE
        # on the rendered screen -- a flat transcript interleaves a live
        # line editor's redraws, a grid shows the screen as it was.
        result = run_cli(
            ["--cmd", "bash --norc -i", "--connect-wait", "0.8",
             "--step-delay", "0.6", "--settle", "0.5", "--screen",
             "echo SCREEN-OK", "ENTER"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count(b"echo SCREEN-OK"), 1)
        self.assertIn(b"SCREEN-OK", result.stdout)

    def test_screen_composes_with_capture_out(self):
        # stdout is the grid (no ANSI in it by construction); the file
        # still gets the raw capture, byte for byte.
        cmd = "bash -c \"printf 'a\\033[31mb\\033[0mc'; cat\""
        with tempfile.NamedTemporaryFile(suffix=".cap", delete=False) as f:
            path = f.name
        try:
            result = run_cli(
                ["--cmd", cmd, "--screen", "--capture-out", path, *FAST, "x"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn(b"ab", result.stdout)
            self.assertNotIn(b"\x1b[", result.stdout)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"a\x1b[31mb\x1b[0mcx")
        finally:
            os.unlink(path)

    def test_screen_conflicts_with_repl(self):
        # --screen renders a FINAL capture; a live REPL session has
        # none, so the combination is a usage error rather than a
        # silently meaningless grid.
        result = run_cli_input(
            ["--cmd", "cat", *FAST, "--repl", "--screen"], b"hi\n"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--screen", result.stderr)
        self.assertIn(b"--repl", result.stderr)


if __name__ == "__main__":
    unittest.main()
