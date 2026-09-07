"""ANSI escape stripping, applied to output by default (--raw keeps
them). See NOTES.md's "byte that looks empty" gotcha for what this
deliberately does and doesn't handle -- a control byte outside a
recognized escape sequence isn't touched by this regex, on purpose.
"""
import re

# OSC sequences end in BEL *or* ST (ESC \) -- modern emitters (fish's
# OSC 133 prompt marks, our own --answer-queries OSC 11 reply, OSC 8
# hyperlinks) use ST, and only stripping the BEL form left the whole
# sequence in "stripped" output. An OSC payload cannot contain ESC or
# BEL (either one would be its terminator), so the negated classes are
# exact, not heuristic. An unterminated OSC at end of capture stays
# untouched -- same policy as every other sequence here: strip what is
# provably a sequence, leave the rest for --raw to explain.
ANSI = re.compile(
    rb'\x1b(?:\[[0-9;?]*[a-zA-Z]'
    rb'|\][^\x07\x1b]*(?:\x07|\x1b\\)'
    rb'|[()][0-9A-Za-z]'
    rb'|[=>78McD])'
)
