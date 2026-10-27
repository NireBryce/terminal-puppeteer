"""Render a raw pty capture as a terminal grid: a stdlib-only ANSI
interpreter over the layout-determining subset of C0/CSI, so a capture
that reads like corrupted text in a flat byte stream -- ble.sh's
`[▽][▶]...` cursor-glyph probe, zsh's `eecho hi` backspace artifact --
reads as what a real terminal would actually be showing.

The boundary, stated honestly: this is a layout renderer, not a
terminal emulator. It implements exactly the operations that determine
WHERE characters sit -- cursor positioning (CUP, CUU/CUD/CUF/CUB,
CHA/CNL/CPL, VPA/HPA, CR/LF/BS/TAB, IND/RI/NEL, save/restore, xterm's
deferred autowrap), erase (EL, ED, ECH), character insert/delete (ICH,
DCH), line insert/delete (IL/DL), scrolling (SU/SD, and DECSTBM scroll
regions, since editor redraws depend on a protected status line) -- and
parse-and-discards everything else (SGR, private modes, OSC/DCS
strings, charset selection): it colors nothing, toggles no modes, and
keeps no alternate screen. Unknown-but-grammatical sequences discard
invisibly; truncated sequences render nothing, which is what a real
terminal shows while still waiting for the sequence to finish -- the
bytes themselves stay available for forensics through --raw/
--capture-out/od, the same division of labor as ansi.py's
strip-what-is-provable policy.

Every codepoint occupies one cell (wide CJK glyphs included), and
unhandled control bytes render as nothing -- a byte-level question is
not what this answers. See README.md's "Viewing the output" and the
man page's CAVEATS.
"""
import re

DEFAULT_ROWS = 24
DEFAULT_COLS = 80
TAB_WIDTH = 8

# VT grammar, character-wise (ansi.py's regex is the byte-wise twin of
# these classes): parameter bytes 0x30-0x3F ("0-9:;<=>?", the colon is
# modern subparam syntax, < = > ? the private markers), intermediate
# bytes 0x20-0x2F, one final byte 0x40-0x7E.
_CSI_PARAM = re.compile(r'[0-9:;<=>?]*')
_CSI_INTERMEDIATE = re.compile(r'[\x20-\x2f]*')
_CSI_FINAL = re.compile(r'[\x40-\x7e]')


# Relative cursor moves by CSI final byte: (row step, column step, whether
# the move also lands on column 0). CUU/CUD/CUF/CUB, CNL/CPL.
_CURSOR_STEPS = {
    'A': (-1, 0, False),
    'B': (1, 0, False),
    'C': (0, 1, False),
    'D': (0, -1, False),
    'E': (1, 0, True),
    'F': (-1, 0, True),
}


def utf8_safe_len(buf):
    """Length prefix of `buf` with no partial multi-byte character at
    its end -- the incremental feeder's twin of "don't consume a
    truncated escape sequence": a character split across two feeds
    would otherwise decode to replacement glyphs. 0 means "all of it
    is one character still waiting for its tail" (or an empty buffer).
    A run of continuation bytes with no lead before them is stray, not
    partial -- holding it would freeze the feeder waiting for a lead
    that was already consumed -- so it passes through for the decoder
    to replace. Invalid lead bytes (0xfe/0xff) likewise: not this
    function's business."""
    idx = len(buf) - 1
    while idx >= 0 and (buf[idx] & 0xC0) == 0x80:
        idx -= 1
    if idx < 0:
        return len(buf)  # stray continuations, no lead: not partial
    lead = buf[idx]
    if lead < 0x80:
        return len(buf)
    if lead & 0xE0 == 0xC0:
        need = 2
    elif lead & 0xF0 == 0xE0:
        need = 3
    elif lead & 0xF8 == 0xF0:
        need = 4
    else:
        return len(buf)  # invalid lead: let the decoder replace it
    return idx if len(buf) - idx < need else len(buf)


class Screen:
    """A rows x cols grid of characters with a cursor, scroll region,
    and the interpreter state (deferred autowrap, saved cursor). feed()
    consumes capture text; text() renders the current screen."""

    def __init__(self, rows=DEFAULT_ROWS, cols=DEFAULT_COLS):
        self.rows = rows
        self.cols = cols
        self.grid = [[' '] * cols for _ in range(rows)]
        self.row = 0
        self.col = 0
        # Scroll region, 0-indexed inclusive (DECSTBM). Full screen
        # until set; LF/IND/RI/IL/DL/SU/SD scroll within it, which is
        # what keeps an editor's status line pinned while text above
        # scrolls.
        self.top = 0
        self.bottom = rows - 1
        # xterm's deferred autowrap: reaching the last column arms the
        # wrap, but the wrap happens only when the NEXT printable
        # arrives -- an explicit cursor move lands on the same line.
        # Immediate wrapping would leave phantom blank lines under any
        # full-width redraw.
        self._pending_wrap = False
        self._saved = (0, 0)

    # -- feeding ---------------------------------------------------------

    def feed(self, text):
        idx, length = 0, len(text)
        while idx < length:
            char = text[idx]
            if char == '\x1b':
                idx = self._escape(text, idx)
                continue
            if char == '\r':
                self.col = 0
                self._pending_wrap = False
            elif char in '\n\x0b\x0c':
                # LF (VT/FF act like LF); column stays -- a pty target
                # in cooked mode sends CRLF anyway, and this matches a
                # real terminal's LF-without-LNM.
                self._pending_wrap = False
                self.linefeed()
            elif char == '\b':
                if self.col > 0:
                    self.col -= 1
                self._pending_wrap = False
            elif char == '\t':
                self._pending_wrap = False
                self.col = min(
                    (self.col // TAB_WIDTH + 1) * TAB_WIDTH, self.cols - 1)
            elif char < ' ' or char == '\x7f':
                pass  # other C0 controls and DEL render as nothing
            else:
                self.put(char)
            idx += 1
        return self

    def put(self, char):
        if self._pending_wrap:
            self._pending_wrap = False
            self.col = 0
            self.linefeed()
        self.grid[self.row][self.col] = char
        if self.col == self.cols - 1:
            self._pending_wrap = True
        else:
            self.col += 1

    # -- cursor motion and scrolling -------------------------------------

    def linefeed(self):
        if self.row == self.bottom:
            self.scroll_up(1)
        elif self.row < self.rows - 1:
            self.row += 1

    def reverse_linefeed(self):
        if self.row == self.top:
            self.scroll_down(1)
        elif self.row > 0:
            self.row -= 1

    def scroll_up(self, count):
        # Delete lines at the region top, blank-fill at its bottom --
        # inserting/deleting at top/bottom rather than slicing keeps
        # this trivially region-correct.
        count = max(1, min(count, self.bottom - self.top + 1))
        for _ in range(count):
            del self.grid[self.top]
            self.grid.insert(self.bottom, [' '] * self.cols)

    def scroll_down(self, count):
        count = max(1, min(count, self.bottom - self.top + 1))
        for _ in range(count):
            del self.grid[self.bottom]
            self.grid.insert(self.top, [' '] * self.cols)

    def _clamp_cursor(self):
        self.row = min(max(self.row, 0), self.rows - 1)
        self.col = min(max(self.col, 0), self.cols - 1)

    # -- escape dispatch ---------------------------------------------------

    def _escape(self, text, idx):
        """text[idx] is ESC. Returns the index of the first unconsumed
        character. Complete sequences dispatch or discard; a sequence
        cut short renders nothing (a real terminal is still waiting for
        its tail); a control byte aborting a CSI is processed normally."""
        length = len(text)
        if idx + 1 >= length:
            return length  # lone trailing ESC: renders nothing
        kind = text[idx + 1]
        if kind == '[':
            return self._csi(text, idx + 2)
        if kind == ']':
            return self._string(text, idx + 2, bel=True)    # OSC
        if kind in 'PX^_':
            return self._string(text, idx + 2, bel=False)   # DCS/SOS/PM/APC
        if kind in '()*+#':
            # Charset selection, DEC line-size -- consume one more char.
            return min(idx + 3, length)
        if kind == 'D':    # IND
            self._pending_wrap = False
            self.linefeed()
        elif kind == 'E':  # NEL
            self._pending_wrap = False
            self.col = 0
            self.linefeed()
        elif kind == 'M':  # RI
            self._pending_wrap = False
            self.reverse_linefeed()
        elif kind == '7':  # DECSC
            self._saved = (self.row, self.col)
        elif kind == '8':  # DECRC
            self._restore_cursor()
        elif kind == 'c':  # RIS: full reset
            self.__init__(self.rows, self.cols)
        elif kind == '\\':  # stray ST
            pass
        # Anything else: a complete two-byte sequence we don't implement
        # (=, >, keypad modes, ...) -- consumed, renders nothing.
        return idx + 2

    def _csi(self, text, idx):
        """Parse the VT grammar from text[idx] (just past 'ESC [')."""
        length = len(text)
        param_match = _CSI_PARAM.match(text, idx)
        params_str, idx = param_match.group(0), param_match.end()
        if idx >= length:
            return length  # truncated: renders nothing
        idx = _CSI_INTERMEDIATE.match(text, idx).end()
        if idx >= length:
            return length
        final = text[idx]
        if not _CSI_FINAL.match(final):
            # Grammar violation: the ESC and parameters discard; the
            # offending byte is reprocessed by the main loop.
            return idx
        self._dispatch_csi(params_str, final)
        return idx + 1

    def _dispatch_csi(self, params_str, final):
        # Private-marker sequences (DEC modes like ?2004h, DA2 >c, ...)
        # are parseable but not layout -- discarded, like SGR. Tuple
        # membership, not `in '<=>?'`: params_str[:1] is '' for bare
        # sequences (ESC[K, ESC[H), and '' in <any string> is True --
        # the empty prefix would silently discard every bare CSI.
        if params_str[:1] in ('<', '=', '>', '?'):
            return
        params = []
        for part in params_str.split(';'):
            part = part.split(':')[0]  # ignore subparams (38:2::...)
            params.append(int(part) if part.isdigit() else None)

        def param(idx, default):
            # CSI's default for a missing param is almost always 1 or
            # (for the second CUP param) 1; an explicit 0 means the
            # default too.
            value = params[idx] if idx < len(params) else None
            return default if not value else value

        if final in 'Hf':    # CUP/HVP
            self._set_cursor(param(0, 1) - 1, param(1, 1) - 1)
        elif final in _CURSOR_STEPS:    # CUU/CUD/CUF/CUB/CNL/CPL
            row_step, col_step, to_line_start = _CURSOR_STEPS[final]
            amount = param(0, 1)
            self._set_cursor(
                self.row + row_step * amount,
                0 if to_line_start else self.col + col_step * amount)
        elif final in 'G`':  # CHA/HPA
            self._set_cursor(self.row, param(0, 1) - 1)
        elif final == 'd':   # VPA
            self._set_cursor(param(0, 1) - 1, self.col)
        elif final == 'J':   # ED
            self._erase_display(param(0, 0))
        elif final == 'K':   # EL
            self._erase_line(param(0, 0))
        elif final == 'X':   # ECH
            self._erase_chars(param(0, 1))
        elif final == '@':   # ICH
            self._insert_chars(param(0, 1))
        elif final == 'P':   # DCH
            self._delete_chars(param(0, 1))
        elif final == 'L':   # IL
            self._insert_lines(param(0, 1))
        elif final == 'M':   # DL
            self._delete_lines(param(0, 1))
        elif final == 'S':   # SU
            self.scroll_up(param(0, 1))
        elif final == 'T':   # SD
            self.scroll_down(param(0, 1))
        elif final == 'r':   # DECSTBM
            self._set_scroll_region(param(0, 1), param(1, self.rows))
        elif final == 's':   # SCO save cursor
            self._saved = (self.row, self.col)
        elif final == 'u':   # SCO restore cursor
            self._restore_cursor()
        # else: SGR (m), mode set/reset (h/l), reports (n/c/t), ... --
        # grammatical, not layout, discarded.

    def _set_cursor(self, row, col):
        """Move the cursor (clamped to the grid) and disarm the deferred
        autowrap -- every explicit cursor move does both."""
        self.row, self.col = row, col
        self._clamp_cursor()
        self._pending_wrap = False

    def _restore_cursor(self):
        self._set_cursor(*self._saved)

    def _set_scroll_region(self, top, bottom):
        """DECSTBM, 1-indexed inclusive; an invalid region is ignored."""
        if 1 <= top < bottom <= self.rows:
            self.top, self.bottom = top - 1, bottom - 1
            # DECSTBM homes the cursor, same as a real terminal.
            self._set_cursor(0, 0)

    def _erase_chars(self, count):
        count = min(count, self.cols - self.col)
        for col_idx in range(self.col, self.col + count):
            self.grid[self.row][col_idx] = ' '

    def _insert_chars(self, count):
        count = min(count, self.cols - self.col)
        row = self.grid[self.row]
        row[self.col:] = [' '] * count + row[self.col:self.cols - count]

    def _delete_chars(self, count):
        count = min(count, self.cols - self.col)
        row = self.grid[self.row]
        row[self.col:] = row[self.col + count:] + [' '] * count

    def _insert_lines(self, count):
        if not self.top <= self.row <= self.bottom:
            return
        # Clamped to the region at/below the cursor, same shape as
        # scroll_up's clamp to the region: the CSI parameter has no
        # length cap, and the capture is the target's input -- an
        # ESC[999999999999L in it must be a bounded edit, not hours of
        # del/insert spinning the loop.
        count = min(count, self.bottom - self.row + 1)
        for _ in range(count):
            del self.grid[self.bottom]
            self.grid.insert(self.row, [' '] * self.cols)

    def _delete_lines(self, count):
        if not self.top <= self.row <= self.bottom:
            return
        count = min(count, self.bottom - self.row + 1)
        for _ in range(count):
            del self.grid[self.row]
            self.grid.insert(self.bottom, [' '] * self.cols)

    def _erase_display(self, mode):
        if mode == 0:    # cursor to end of screen
            self._erase_line(0)
            for row_idx in range(self.row + 1, self.rows):
                self.grid[row_idx] = [' '] * self.cols
        elif mode == 1:  # start of screen to cursor (inclusive)
            self._erase_line(1)
            for row_idx in range(self.row):
                self.grid[row_idx] = [' '] * self.cols
        elif mode == 2:  # whole screen
            self.grid = [[' '] * self.cols for _ in range(self.rows)]
        # mode 3 (clear scrollback): nothing to clear here, no-op

    def _erase_line(self, mode):
        row = self.grid[self.row]
        if mode == 0:    # cursor (inclusive) to end of line
            for col_idx in range(self.col, self.cols):
                row[col_idx] = ' '
        elif mode == 1:  # start of line to cursor (inclusive)
            for col_idx in range(self.col + 1):
                row[col_idx] = ' '
        elif mode == 2:
            self.grid[self.row] = [' '] * self.cols

    def _string(self, text, idx, bel):
        """OSC (ESC ]) / DCS-family (ESC P X ^ _): discard up to the
        terminator -- BEL or ESC \\ for OSC, ESC \\ for the rest.
        Truncated renders nothing (the terminal is still waiting)."""
        length = len(text)
        while idx < length:
            if text[idx] == '\x07' and bel:
                return idx + 1
            if text[idx] == '\x1b':
                if idx + 1 < length and text[idx + 1] == '\\':
                    return idx + 2
                return idx  # aborted by a new escape: reprocess it
            idx += 1
        return length

    # -- rendering ---------------------------------------------------------

    def text(self):
        """One line per row, each row's trailing whitespace trimmed, and
        trailing empty rows dropped -- the leading blank rows stay,
        since a prompt sitting at row 6 IS the layout."""
        lines = [''.join(row).rstrip() for row in self.grid]
        return '\n'.join(lines).rstrip('\n')


def render(capture, rows=DEFAULT_ROWS, cols=DEFAULT_COLS):
    """One-shot helper: raw capture bytes in, the rendered screen out."""
    return Screen(rows, cols).feed(capture.decode('utf-8', 'replace')).text()
