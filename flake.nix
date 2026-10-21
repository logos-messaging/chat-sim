{
  description = "chat-sim - simulations of logos-chat groups";

  nixConfig = {
    extra-substituters = [ "https://cache.nix.logos.co/public" ];
    extra-trusted-public-keys = [ "public:l4HrXgL4nw246+LBh2SOJyhz64BoGegOYLheT/iIAPU=" ];
  };

  inputs = {
    # The revision the simulation runs, which Cargo.toml pins logos-chat's crates at too.
    logos-chat.url = "github:logos-messaging/logos-chat/00f396a83c99da1a6159a31add9befef8f680ac1";
    # logos-chat's own, so the delivery library and the toolchain are the store paths its CI
    # pushes to the Logos cache.
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
      packages = forAllSystems ({ pkgs, system, ... }:
        let
          logosDeliveryLib = self.packages.${system}.logos-delivery;
          rustToolchain = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
          rustPlatform = pkgs.makeRustPlatform { cargo = rustToolchain; rustc = rustToolchain; };
          chat-cli = rustPlatform.buildRustPackage {
            pname = "chat-cli";
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
            cargoBuildFlags = [ "-p" "chat-cli" ];
            nativeBuildInputs = [ pkgs.pkg-config pkgs.cmake pkgs.perl pkgs.protobuf pkgs.patchelf ];
            buildInputs = [ pkgs.openssl ];
            env.LOGOS_DELIVERY_LIB_DIR = "${logosDeliveryLib}/lib";
            # Vendored OpenSSL keeps the compiler's path, which brings gcc into the closure.
            env.OPENSSL_NO_VENDOR = "1";
            # The tests start a registry and drive the binary; CI runs them in the dev shell.
            doCheck = false;
            meta.mainProgram = "chat-cli";
          };
          harness-run = chat-cli.overrideAttrs (old: {
            pname = "harness-run";
            cargoBuildFlags = [ "-p" "harness" "--bin" "harness-run" ];
            meta = old.meta // { mainProgram = "harness-run"; };
          });
        in
        {
          inherit chat-cli harness-run;
          logos-delivery = logos-chat.packages.${system}.logos-delivery;
        }
        // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          # What tools/sim runs each member and the registry in, given --image:
          # `nix build .#sim-image && ./result | docker load`.
          sim-image = pkgs.dockerTools.streamLayeredImage {
            name = "logos-chat-sim";
            tag = "latest";
            contents = [ chat-cli pkgs.python3 pkgs.iproute2 ];
          };
        }
      );

      # `nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs`:
      # tools/sim with the binaries of one rig built and passed to it.
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
          groupv2-simulation-processes = app "groupv2-simulation-processes"
            "tools/sim with each member a chat-cli process of its own"
            ''exec python3 ${sim}/tools/sim "$@" --chat-cli ${pkgs.lib.getExe packages.chat-cli}'';
        }
        // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          groupv2-simulation-containers = app "groupv2-simulation-containers"
            "tools/sim with each member in a container of sim-image"
            ''
              engine=docker
              previous=
              for argument in "$@"; do
                case $previous in --engine) engine=$argument ;; esac
                case $argument in --engine=*) engine=''${argument#--engine=} ;; esac
                previous=$argument
              done
              ${packages.sim-image} | "$engine" load >&2
              exec python3 ${sim}/tools/sim "$@" --image logos-chat-sim:latest
            '';
        }
      );

      devShells = forAllSystems ({ pkgs, system, ... }:
        let
          rustToolchain = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
          logosDeliveryLib = self.packages.${system}.logos-delivery;
        in
        {
          default = pkgs.mkShell {
            nativeBuildInputs = [
              rustToolchain
              pkgs.pkg-config
              pkgs.cmake
              pkgs.perl
              pkgs.protobuf
            ]
            # logos-chat's components/build.rs rewrites the dylib soname via patchelf
            # on Linux. macOS uses install_name_tool, which ships with the toolchain.
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.patchelf ];
            buildInputs = [ logosDeliveryLib ];
            shellHook = ''
              export LOGOS_DELIVERY_LIB_DIR="${logosDeliveryLib}/lib"
            '';
          };
        }
      );
    };
}
