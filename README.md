# chat-sim

Simulations of [logos-chat](https://github.com/logos-messaging/logos-chat) groups: does a group of
logos-chat clients still hold one group when the network delays and loses what its members send?

A scenario says how many members there are, what they do, what the network does to them, and what
the group is expected to come to. [`tools/sim`](tools/sim/README.md) runs it with every member in
one process on a virtual clock, where on Linux a run is the same every time from its seed.
[`tools/forks`](tools/forks/README.md) then reads the members' logs and says whether they still
hold one group.

`tools/sim` can also run a `chat-cli` process for each member, with a delivery node of its own on a
local network, or each of those in a container on a lossy wire. chat-cli builds on logos-chat changes
that the revision run here does not hold, so it lives on the
[`local-network`](https://github.com/logos-messaging/chat-sim/tree/local-network) branch, pinned to
logos-chat's `feat/local-network`.

## Layout

| Path | What it is |
|------|------------|
| [`crates/harness`](crates/harness/README.md) | A group of logos-chat clients in one process on a virtual clock, and `harness-run`, which runs a plan on it |
| [`crates/network-model`](crates/network-model/src/lib.rs) | The model of a lossy network the runner puts between a client and what it receives |
| [`tools/sim`](tools/sim/README.md) | Runs a scenario, and sweeps one over seeds and rates |
| [`tools/forks`](tools/forks/README.md) | Reads the logs of a group's members and says whether the group forked |
| `.github/scripts/mock-registry.py` | The KeyPackage registry the peers of a run share |

## The logos-chat it runs

The simulation runs logos-chat at one revision, pinned twice: [`Cargo.toml`](Cargo.toml) pins
logos-chat's crates, and [`flake.nix`](flake.nix) takes the toolchain from logos-chat's flake, so it
is the store path logos-chat's CI pushes to the Logos cache. CI checks that the two name the same
revision. To move it, change the revision in both, then run
`nix flake lock`; cargo updates `Cargo.lock` on the next build.

`de-mls` and `chat-proto` are spelled in `Cargo.toml` as logos-chat spells them, and move with it.

## Running it

With Nix, one command builds what a run needs and runs a scenario:

```sh
nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs
```

The dev shell has the toolchain for cargo:

```sh
nix develop
cargo build --release --workspace
python3 tools/sim run tools/sim/scenarios/50-epochs.json --one-process target/release/harness-run --out tmp/50-epochs
```

[`tools/sim`](tools/sim/README.md#running-it) has the other ways to run a scenario and what a run
reports.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT)
at your option.
