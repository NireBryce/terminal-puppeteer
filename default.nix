# The standard flake-compat shim: lets `nix-build`/`nix-shell` (classic
# Nix, channels, no experimental features needed) build the same thing
# flake.nix defines, for anyone not using flakes. flake.nix stays the
# single source of truth either way -- this only re-exposes its
# `packages.<current system>.default` output as a plain derivation, it
# doesn't duplicate any of the actual package definition (that's
# package.nix, which flake.nix's `packages` output also just
# pkgs.callPackage's).
#
# `flake-compat`'s own revision comes from flake.lock (the same input
# pinning every other flake input already goes through), not a
# hardcoded URL/rev here, so there's one place this project's Nix inputs
# are pinned, not two.
(import (
  let
    lock = builtins.fromJSON (builtins.readFile ./flake.lock);
    node = lock.nodes.flake-compat.locked;
  in
  fetchTarball {
    url = "https://github.com/edolstra/flake-compat/archive/${node.rev}.tar.gz";
    sha256 = node.narHash;
  }
) {
  src = ./.;
}).defaultNix.packages.${builtins.currentSystem}.default
