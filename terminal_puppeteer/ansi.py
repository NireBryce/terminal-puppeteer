"""ANSI escape stripping, applied to output by default (--raw keeps
them). See NOTES.md's "byte that looks empty" gotcha for what this
deliberately does and doesn't handle -- a control byte outside a
recognized escape sequence isn't touched by this regex, on purpose.
"""
import re

# CSI: everything between the introducer (ESC [) and the final byte is
# parameter bytes 0x30-0x3F ("0-9:;<=>?" -- the colon is modern SGR colon
# syntax like 38:2::r:g:b; < = > ? are the private markers) then
# intermediate bytes 0x20-0x2F, then one final byte 0x40-0x7E. That is
# the VT grammar itself, so the class is exact, not heuristic --
# restricting it any further let private-marker sequences through
# unstripped (ble.sh's XTMODKEYS pushes ESC[>4;1m, DA2/DA3 requests
# ESC[>c and ESC[=c, kitty keyboard flags ESC[>Nu), visible as garbage
# in stripped captures over ssh.
# DCS (ESC P ... ST): fish and ble.sh both speak XTGETTCAP (ESC P +q
# <cap> ESC \), and --answer-queries' XTVERSION reply is DCS; the payload
# cannot contain ESC (it would be the terminator), so the negated class
# is exact like OSC's. An unterminated string at end of capture stays
# untouched -- same policy as every other sequence here: strip what is
# provably a sequence, leave the rest for --raw to explain.
ANSI = re.compile(
    rb'\x1b(?:\[[0-9:;<=>?]*[\x20-\x2f]*[\x40-\x7e]'
    rb'|\][^\x07\x1b]*(?:\x07|\x1b\\)'
    rb'|P[^\x1b]*\x1b\\'
    rb'|[()][0-9A-Za-z]'
    rb'|[=>78McDE])'
)


class Stripper:
    """Incremental ANSI.sub: feed() the raw stream as it arrives,
    advance() after each feed, and result() is always exactly
    ANSI.sub(b'', raw) -- identical bytes, without re-scanning the
    whole capture on every call.

    The consumer is Session.expect(): WAIT-FOR re-checks its pattern
    against the stripped accumulator on every drain tick, so a plain
    ANSI.sub per poll is quadratic in capture size -- invisible at
    repro scale, not invisible for a long --repl session against a
    chatty target. The pattern search itself stays proportional to the
    accumulator (matching the whole transcript is WAIT-FOR's
    semantics); this only removes the re-stripping, which was the
    expensive scan.

    Exactness comes from a boundary with an invariant: no escape
    sequence spans `self._boundary`, so raw[:boundary] strips to a
    fixed prefix that never needs revisiting, and result() is that
    prefix plus a sub of the (short) tail. advance() moves the boundary
    as far forward as the invariant allows, and the last complete
    match's end is the only safe far-limit: every sequence begins with
    an ESC, so a sequence starting before that end would overlap a
    complete match, which cannot happen -- while an INCOMPLETE sequence
    (a truncated OSC still streaming toward its terminator) also starts
    at an ESC at-or-after the last match's end, and the boundary must
    stay in front of it or the cache would keep its header bytes as
    literals forever once it completes behind the boundary. So:

    - if the last ESC sits before the last complete match's end,
      nothing follows that end but plain text (every alternative in
      ANSI starts with ESC) -- cache everything, boundary = end;
    - otherwise a sequence may be dangling from the last ESC -- unless
      the byte right after that ESC proves it can't start one. Every
      alternative is ESC followed by a specific introducer byte
      ([ ] P X ^ _ ( ) = > 7 8 M c D E), so any other follower makes
      the ESC itself literal: the boundary moves past it, to the next
      ESC or the end. Without this, one stray ESC followed by ordinary
      text (a binary blip, a bad paste) would pin the boundary and put
      every later byte in the re-scanned tail.

    The cost that remains: plain text after a dangling-but-plausible
    ESC (e.g. an OSC whose terminator is genuinely still streaming)
    keeps the tail live until the sequence terminates. That is the
    same strip-what-is-provable conservatism as the regex itself, and
    bounded by the sequence's own length.
    """

    # Every byte that can follow ESC and begin a match, straight from
    # the ANSI alternatives above: CSI, OSC, DCS/SOS/PM/APC, charset
    # selection, and the two-byte singles. A bytes object, so the
    # one-byte-slice membership test below is a subsequence check.
    # Derived by hand; the equivalence tests over the zoo and the
    # random chunkings are what catch drift.
    _ESC_FOLLOWERS = b'[]PX^_()=>78McDE'

    def __init__(self):
        self.raw = b''
        self._stripped = b''
        self._boundary = 0

    def feed(self, data):
        self.raw += data

    def advance(self):
        out = self.raw
        last_end = self._boundary
        for match in ANSI.finditer(out, self._boundary):
            last_end = match.end()
        last_esc = out.rfind(b'\x1b', self._boundary)
        if last_esc < last_end:
            boundary = len(out)  # no dangling ESC: plain past the last match
        else:
            follower = out[last_esc + 1:last_esc + 2]
            if follower and follower not in self._ESC_FOLLOWERS:
                # The last ESC can never start a sequence -- literal,
                # and everything up to the next ESC is plain with it.
                next_esc = out.find(b'\x1b', last_esc + 1)
                boundary = len(out) if next_esc == -1 else next_esc
            else:
                boundary = last_end  # a truncated sequence may dangle
        self._stripped += ANSI.sub(b'', out[self._boundary:boundary])
        self._boundary = boundary

    def result(self):
        # The concat copies the stripped prefix each call; that's a
        # memcpy, not a regex scan, and the tail is normally a handful
        # of bytes -- orders of magnitude under the full re-sub this
        # replaces.
        return self._stripped + ANSI.sub(b'', self.raw[self._boundary:])
