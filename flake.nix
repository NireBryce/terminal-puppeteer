{
  description = "Let an LLM agent drive a real interactive program through a pty";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, ... }:
    let
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
