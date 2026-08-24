"""ANSI escape stripping, applied to output by default (--raw keeps
them). See NOTES.md's "byte that looks empty" gotcha for what this
deliberately does and doesn't handle -- a control byte outside a
recognized escape sequence isn't touched by this regex, on purpose.
"""
import re

ANSI = re.compile(rb'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|[()][0-9A-Za-z]|[=>78McD])')
