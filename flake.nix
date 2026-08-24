# Nix packaging for terminal-puppeteer. See README.md's "Nix:" line for
# the user-facing quick reference (`nix run`/flake input/`nix-build`);
# this file and package.nix are the actual definitions those refer to.
#
# The real derivation logic lives in package.nix, not here, so that
# default.nix's flake-compat shim (for nix-build without flakes) and any
# future non-flake consumer can pkgs.callPackage it directly instead of
# needing to evaluate this whole flake. This file is just outputs
# plumbing: which systems, and how packages/apps/devShells map onto
# package.nix and nixpkgs.
{
  description = "Let an LLM agent drive a real interactive program through a pty";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Not used by this flake itself -- pulled in only so default.nix (see
    # its own comment) can pin a version of flake-compat via flake.lock,
    # the same way any other input is pinned, rather than default.nix
    # hardcoding a URL and revision of its own that could drift out of
    # sync with what's actually locked.
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, ... }:
    let
      # pty is POSIX-only, so is this tool -- these four cover every
      # platform Nix itself runs on that could plausibly work, no need
      # to enumerate more.
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.callPackage ./package.nix { };
        }
      );

      # `nix run github:NireBryce/terminal-puppeteer -- --version` (etc.)
      # without a separate `nix build`/install step first.
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/terminal-puppeteer";
          meta.description = "Let an LLM agent drive a real interactive program through a pty";
        };
      });

      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          # Stdlib only -- nothing but an interpreter is actually needed
          # to work on this.
          default = pkgs.mkShell {
            packages = [ pkgs.python3 ];
          };
        }
      );
    };
}
