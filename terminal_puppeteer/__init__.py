"""Let an LLM agent drive a real interactive program through a pty.

This is the package's public surface -- see terminal_puppeteer.cli for
the full docstring argparse uses for --help, and README.md for the
human-facing docs. Modules: `keys` (the step vocabulary), `ansi`
(output escape stripping), `driver` (the pty/subprocess mechanics),
`cli` (the argparse surface and entry point).
"""
from ._version import __version__
from .cli import main

__all__ = ['main', '__version__']
