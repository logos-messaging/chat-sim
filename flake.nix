{
  description = "chat-sim - simulations of logos-chat groups";

  nixConfig = {
    extra-substituters = [ "https://cache.nix.logos.co/public" ];
    extra-trusted-public-keys = [ "public:l4HrXgL4nw246+LBh2SOJyhz64BoGegOYLheT/iIAPU=" ];
  };

  inputs = {
    # The revision the simulation runs, which Cargo.toml pins logos-chat's crates at too.
    logos-chat.url = "github:logos-messaging/logos-chat/b750c7572012969393d9cbbbd130c297bc7898cc";
    # logos-chat's own, so the toolchain is the store path its CI pushes to the Logos cache.
    nixpkgs.follows = "logos-chat/nixpkgs";
    rust-overlay.follows = "logos-chat/rust-overlay";
  };

  outputs = { self, nixpkgs, rust-overlay, logos-chat }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f {
        inherit system;
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ rust-overlay.overlays.default ];
        };
      });
    in
    {
      packages = forAllSystems ({ pkgs, ... }:
        let
          rustToolchain = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
          rustPlatform = pkgs.makeRustPlatform { cargo = rustToolchain; rustc = rustToolchain; };
          harness-run = rustPlatform.buildRustPackage {
            pname = "harness-run";
            version = "0.1.0";
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./Cargo.toml
                ./Cargo.lock
                ./crates
              ];
            };
            cargoLock = {
              lockFile = ./Cargo.lock;
              allowBuiltinFetchGit = true;
            };
            cargoBuildFlags = [ "-p" "harness" "--bin" "harness-run" ];
            nativeBuildInputs = [ pkgs.pkg-config pkgs.cmake pkgs.perl pkgs.protobuf ];
            # CI runs the tests in the dev shell.
            doCheck = false;
            meta.mainProgram = "harness-run";
          };
        in
        {
          inherit harness-run;
        }
      );

      # `nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs`:
      # tools/sim with the runner built and passed to it.
      apps = forAllSystems ({ pkgs, system, ... }:
        let
          packages = self.packages.${system};
          # tools/sim finds the registry and tools/forks by their place in the repository.
          sim = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [ ./tools ./.github/scripts/mock-registry.py ];
          };
          app = name: description: text: {
            type = "app";
            program = pkgs.lib.getExe (pkgs.writeShellApplication {
              inherit name text;
              runtimeInputs = [ pkgs.python3 ];
            });
            meta.description = description;
          };
        in
        {
          groupv2-simulation = app "groupv2-simulation"
            "tools/sim with its members in one process, on a virtual clock"
            ''exec python3 ${sim}/tools/sim "$@" --one-process ${pkgs.lib.getExe packages.harness-run}'';
        }
      );

      devShells = forAllSystems ({ pkgs, ... }:
        let
          rustToolchain = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
        in
        {
          default = pkgs.mkShell {
            nativeBuildInputs = [
              rustToolchain
              pkgs.pkg-config
              pkgs.cmake
              pkgs.perl
              pkgs.protobuf
            ];
          };
        }
      );
    };
}
