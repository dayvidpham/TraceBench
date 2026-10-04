{
  description = "TraceBench dev environment: Go, Python/uv, and corpus tooling";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        go = pkgs.go_1_26;
      in
      {
        devShells.default = pkgs.mkShell {
          name = "tracebench-dev";
          packages = with pkgs; [
            # Go toolchain: the repository module and the snapshot module.
            go
            gopls
            gotools
            go-tools
            # Python tooling: the corpus loader runs through uv.
            uv
            python3
            # Repository and corpus tooling.
            git
            gh
            jq
            curl
            sqlite
            # Harbor's podman provider shells out to `docker compose`; the
            # devShell provides v2 so Harbor runs need no nix-shell wrapper.
            docker-compose
          ];
          shellHook = ''
            echo "tracebench dev shell: $(go version | cut -d' ' -f3), $(uv --version)"
            export CGO_ENABLED=1
            [ -f .envrc.local ] && source .envrc.local || true
          '';
        };
      });
}
