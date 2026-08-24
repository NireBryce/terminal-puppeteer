{ lib, python3Packages }:

python3Packages.buildPythonApplication {
  pname = "terminal-puppeteer";
  version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
  pyproject = true;

  src = ./.;

  build-system = [ python3Packages.setuptools ];

  # Stdlib only, both the tool and its tests -- nothing to propagate.
  doCheck = true;
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
