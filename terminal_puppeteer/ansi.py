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
