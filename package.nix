# The actual Nix derivation for terminal-puppeteer, called from
# flake.nix's `packages` output (via pkgs.callPackage) and from
# default.nix's flake-compat shim -- kept in its own file, rather than
# inlined into flake.nix, precisely so both of those (and any future
# non-flake consumer) can pkgs.callPackage this directly.
#
# This wraps the existing pyproject.toml as-is -- no changes needed
# there for Nix specifically. [project.scripts] and the man page's
# [tool.setuptools.data-files] entry both install correctly through
# Nix's normal wheel build/install, the same way they do through
# `pip install .`/`uv pip install`; verified by actually building this
# and inspecting $out, not assumed from reading pyproject.toml.
{ lib, python3Packages }:

python3Packages.buildPythonApplication {
  pname = "terminal-puppeteer";
  # Read from pyproject.toml rather than duplicated a third time
  # (alongside pyproject.toml's own [project].version and the script's
  # __version__ -- see terminal_puppeteer/_version.py's own "keep in
  # sync" comment for why that pair is still two copies, not one: Nix
  # can read a TOML file at eval time, `__version__` printed by
  # `--version` at runtime can't reach into pyproject.toml the same way
  # without adding an import-time file read of its own).
  version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
  pyproject = true;

  src = ./.;

  build-system = [ python3Packages.setuptools ];

  # Stdlib only, both the tool and its tests -- nothing to propagate.
  doCheck = true;
  # The project's own test suite, run for real as part of the build --
  # not a separate Nix-specific test. One test (the interrupt-cleanup
  # one in CliIntegrationTests) skips cleanly here: no `pgrep` in the
  # build sandbox, exactly the case its existing skipUnless guard is
  # for, not a Nix-specific failure to chase.
  checkPhase = ''
    runHook preCheck
    python3 -m unittest discover tests
    runHook postCheck
  '';

  meta = {
    description = "Let an LLM agent drive a real interactive program through a pty";
    longDescription = ''
      Opens a real pty, spawns a target program attached to it, and sends
      it actual keystrokes with delays between them -- for reproducing
      bugs that only exist in a live, human-typed terminal session, then
      reads back what the program actually did.
    '';
    homepage = "https://github.com/NireBryce/terminal-puppeteer";
    license = lib.licenses.mit;
    mainProgram = "terminal-puppeteer";
    platforms = lib.platforms.unix; # pty is POSIX-only, so is this tool
  };
}
