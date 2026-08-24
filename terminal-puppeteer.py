#!/usr/bin/env python3
"""Thin entry point for terminal-puppeteer.

The real implementation lives in the terminal_puppeteer/ package next to
this file: terminal_puppeteer/cli.py has the full docstring argparse
actually uses for --help, and README.md has the human-facing docs. Kept
this way -- rather than folding everything back into this one file --
so `./terminal-puppeteer.py ...` keeps working exactly as documented
without installing anything: Python puts a script's own directory on
sys.path automatically when run directly, so the sibling package
directory is found whether this runs in place from a checkout or as the
pip-installed script (pyproject.toml's script-files copies this file
alongside a packages-installed copy of terminal_puppeteer/, found the
normal way via site-packages).
"""
from terminal_puppeteer.cli import main

if __name__ == '__main__':
    main()
