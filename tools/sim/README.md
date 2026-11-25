# sim

How does a group of logos-chat clients fare when the network delays and loses what its members
receive?

`sim` reads a scenario, a file that says how many members there are, what they do, what the
network does to them and what the group is expected to come to. It writes the scenario as a plan
for `harness-run`, the runner of this repository's [harness](../../crates/harness/README.md), which
seats every member in one process and does the steps on a virtual clock. A run of a `grow` step
says from its lineage how far the group got before it first forked. Any other run has
[`forks`](../forks/README.md) hold the report of the members' log against what the scenario
expects. `sweep` runs a scenario over seeds, `compare` holds a sweep to the outcomes expected of it,
and `matrix` pairs a revision's runs with its base's.

It needs Python 3.9 or newer and no package beyond the standard library, and the runner in its
place. The guide's [Scenario format](https://logos-messaging.github.io/chat-sim/guide/reference/scenario-format/)
gives every field and step of a scenario, and
[The scenarios](https://logos-messaging.github.io/chat-sim/guide/understand/scenarios/) what each
file in `scenarios/` does.

## Running it

From the repository root, with Nix, one command builds the runner from the checkout and runs `sim`
with it. What follows `--` is `sim`'s command line, less the flag the app gives:

```sh
nix run .#groupv2-simulation -- run tools/sim/scenarios/three.json --out tmp/three
```

Without the app:

```sh
nix develop -c cargo build --release -p harness --bin harness-run
python3 tools/sim run tools/sim/scenarios/three.json --one-process target/release/harness-run --out tmp/three
```

```console
3 members, fast group timers, one process, 0.600s on a virtual clock in 0.010s of wall time, seed 0
whole: 3 of 3 members at the tip
the report is as expected
load average 4.39 at the start, 4.39 at the end

member  installation  errors
saro        f95e254c       0
raya        671cc18a       0
pax         c1f311f2       0

batch  joiners  members  seated
1            2        3  0.500s

post  from  read by  median   worst
1      pax   2 of 2  0.000s  0.000s
```

| Status | Meaning |
|---|---|
| 0 | Every step was done, and any report of the log is as the scenario expects. |
| 1 | A step was not done, or the report differs from what the scenario expects. |
| 2 | The command line or the scenario is refused. No runner was started. |
| 128 + n | Signal n interrupted the run: SIGINT, SIGTERM or SIGHUP. It ended the runner and wrote what it had. |

| Flag | Sets |
|------|------|
| `--one-process PATH` | The runner. A path that is no file, or a file that cannot be run, is refused |
| `--out DIR` | The run's directory, which the run makes and which must not exist. One that cannot be made is refused |

A step that does not come about ends the steps and not the run. The steps stop where the runner
stops them, with its reason: a roster or a post a member waited on did not come to hold or to be
read within the budget, a batch left the group split, or no candidate was withheld in the batch
after a `withhold` step. The log is judged all the same, and the run says both what stopped the
steps and what the check found.

A run that made no conversation, or that a signal interrupted, is not checked. Neither is a run
whose check was not made: when `forks check` ends without an answer, with a status of its own or
by a signal, the run says `not checked:` and how the check ended, and exits 1. A run of a `grow`
step is not checked either, and says ``not checked: a `grow` step expects no outcome``: it goes
as expected when nothing stopped its step.

## The scenario

A scenario is checked whole before anything starts, and refused with the field's name and why. The
format is in the guide's
[Scenario format](https://logos-messaging.github.io/chat-sim/guide/reference/scenario-format/).
Two parts of it need more than a table.

### The network model

`network` puts every member's inbound frames through this repository's
[`network-model`](../../crates/network-model/src/lib.rs). The runner hands a member's model each
frame that arrives for it, and the model loses it at `loss`, or at `welcome_loss` for a Welcome,
or passes it on after the member's `delay_ms`. A decision follows from the seed, the member's name
and the frames shown to it so far, so one frame more or fewer shifts every decision after it.

### A withheld candidate

Every steward of a GroupV2 group makes a commit candidate in a round, and the group applies one of
them. A `withhold` step has a member lose the first candidate that reaches it in the round of the
next batch, and the run then comes to one of two outcomes. The group applied another candidate,
the member applied it as well, and the group is whole. Or the group applied the candidate the
member lost, and the member is alone in a state of its own. Which of the two a run comes to goes
by which candidate reached the member first, so a scenario says both:

```json
"expect": {
  "applied": {
    "verdict": "split at epoch 4",
    "posts": {"pax": "unreadable"}
  }
}
```

`applied` of `expect` is the outcome of a run whose group applied the candidate withheld. What
`expect` holds beside it is the outcome of a run whose group did not. At its end the run reads from
the member's model whether the group applied the candidate, and the report is held to the outcome
that answer names and to no other. A `withhold` step stands for these two outcomes and no third, so
the scenario is refused where it would make more of them: a second `withhold`, a `cut`, a loss
above 0, a step after it that is not one `seat` of one batch, a second `seat` after that, a post
from the member, and a position of the member in `expect` other than `tip`.

## A sweep

`sweep` does a scenario `--runs K` times, one after another, and prints a row as each ends. Run k
takes the seed `seed + k - 1`, and so does the seed of `network`. With `--from N` the runs are runs
N to N+K-1 of a sweep, each with the seeds it has in a sweep from run 1, so one run of a sweep can
be made alone:

```sh
nix run .#groupv2-simulation -- sweep tools/sim/scenarios/grow-waku-loss-5.json --runs 3 --out tmp/grow-waku-loss-5
```

```console
run  seconds  at the tip  verdict                 check (one process: seconds of its virtual clock)
  1   2712.5           -  -                       5 main branches ran side by side: trunk at epoch 11, 5 main, 6 sub, 15 fell behind, 6 behind at the end, 3 never joined; seed 0; network seed 1
  2   1603.0           -  -                       6 main branches ran side by side: trunk at epoch 11, 6 main, 2 sub, 4 fell behind, 2 behind at the end, 2 never joined; seed 1; network seed 2
  3   1626.6           -  -                       5 main branches ran side by side: trunk at epoch 11, 5 main, 0 sub, 6 fell behind, 4 behind at the end, 3 never joined; seed 2; network seed 3
3 of 3 runs went as expected
```

The `seconds` of a row are those of the run's virtual clock. A run of a `grow` step has no verdict
and nobody at the tip, and says how its step ended in place of a check. A run that went otherwise
names its first difference from the expectation and what stopped the steps. Each run keeps its own
directory under `--out`, and `sweep.txt` and `sweep.json` hold the rows, `sweep.json` with the wall
time each run took.

## A sweep held to what is expected

```sh
python3 tools/sim compare tools/sim/expected/grow-waku-loss-5.json tmp/grow-waku-loss-5
python3 tools/sim compare --write tools/sim/expected/grow-waku-loss-5.json tmp/grow-waku-loss-5
```

A run in one process is the same run every time from its seeds, on Linux, so a run that comes out
other than expected was moved by the revision its runner was built from. That can be what the
revision does to the group, or no more than a re-deal: one random number drawn or one frame
received more shifts the draws after it.

`compare` holds each run of a sweep in one process, in row order, to the outcome a file expects of
it, and prints a row a run, the outcome expected under a run that moved, as `was`:

```console
run  seed  change     verdict                 at the tip  seconds  came to
  1     0  same       split at epoch 3                 -   2712.5  5 main branches ran side by side: trunk at epoch 11, 5 main, 6 sub, 15 fell behind, 6 behind at the end, 3 never joined
  2     1  same       split at epoch 5                 -   1603.0  6 main branches ran side by side: trunk at epoch 11, 6 main, 2 sub, 4 fell behind, 2 behind at the end, 2 never joined
  3     2  same       split at epoch 5                 -   1626.6  5 main branches ran side by side: trunk at epoch 11, 5 main, 0 sub, 6 fell behind, 4 behind at the end, 3 never joined
3 of 3 runs came out as expected
```

An outcome is all a run's row says but its wall time, the ids it drew and how far a `grow` step got:

| Field | Holds |
|-------|-------|
| `seed`, `network_seed` | The run's seeds; `network_seed` is `null` without `network` |
| `conversation`, `members_digest` | The group's id, and the first 16 hex digits of the SHA-256 of the members' installation ids in member order, each followed by a newline; both `null` for a run that read no result |
| `verdict`, `at_tip` | The fork report's verdict and how many of the seated are at the tip; `null` for a run of a `grow` step |
| `seated`, `virtual_s` | How many the run seated, and the seconds of its clock |
| `trunk_epoch`, `first_fork` | Of a `grow` step, the trunk's epoch, and the lowest epoch a line forked at, `null` where none did |
| `ended` | Of a `grow` step, `split` where a line forked, else `stalled` where the trunk stopped short of `epochs`, else `behind` where members were behind at the end, else `whole` |
| `lines_off_trunk`, `members_off_trunk`, `never_joined` | Of a `grow` step, the lines whose kind is not `trunk`; the members behind at the end, on such a line or never joined, each once; and how many of those never joined |
| `came_to`, `stopped` | How the run came out in a few words, as the row says it, and what stopped its steps |

The six fields of a `grow` step are `null` for a run of other steps. A run is `same` where every
field is as expected, and `moved` where any differs. `compare` exits 0 when every run came out as
expected and the sweep has a run for each outcome expected, 1 when not, and 2 when the sweep or the
file cannot be read, the file holds outcomes without the ids and the fields of a `grow` step, or a
run was not in one process. `--write` writes the sweep's outcomes to the file instead, so a change
that moves how a run comes out has the file's diff show what moved. It runs with `python3`: the
flake's app adds `--one-process`, which `compare` does not take.

## A matrix of a revision's runs beside its base's

```sh
python3 tools/sim matrix tmp/head --base tmp/base --out tmp/matrix.json
```

`matrix` reads the runs in one process under a directory, at any depth: each run's
`<scenario>/<run>/run.json` as a sweep writes it, and `<scenario>/<run>/unbuilt` for each run of a
job that could not build chat-sim. With `--base` it reads those of the base revision too, pairs
the runs of the two by scenario and seed, and takes each run's outcome and the change of each pair
as `compare` does. `--asked` names a JSON list of the sweeps asked of each side, each with its
`scenario`, a file of `tools/sim/scenarios` without `.json`, the run it is `from` and how many
`runs`, and any other field besides. It writes the matrix as JSON, to standard output or to the
file `--out` names, and exits 0 when it wrote it and 2 when a run or `--asked` cannot be read, a
cell's scenario has no file in `tools/sim/scenarios` that can be read, or a run was not in one
process or is a second run of a scenario at a seed already read.

A cell is a scenario and a seed either side has a run of, or that `--asked` asks of each side. A
cell has no outcome at a side whose job could not build it, that has no run of it, or whose run
has no lineage of its `grow` step, as when its runner left no result.

| Field | Holds |
|-------|-------|
| `head`, `base` | Of each side, how many `runs` were read and how many runs its jobs could not build, as `unbuilt`; `base` is `null` without `--base` |
| `cells[]` | For each cell, in the order of its group, then loss, then scenario, then seed: the `scenario`, the `group` its scenario creates, `v2` or `v3`, the `seed`, the `run` of a sweep it is, the `loss` of its network model and that loss in percent as its `rate`; the `head`'s outcome and the `base`'s as `compare --write` writes one, each `null` where the side has none; whether each side's job could not build it, as `unbuilt`; the `change`, `same` or `moved`, `null` without both outcomes; of each side the `epochs` without a fork, the first fork's or else the trunk's, and how the run `ended`; and `d`, the head's epochs less the base's, `null` without a change |
| `counts` | How many `cells`, how many have a change as `compared`, how many of those are `same` and `moved`, and how many have no outcome at the head as `no-outcome` |
| `verdict` | The worst of the groups' verdicts, in the order `unbuilt`, `no-outcome`, `earlier`, `later`, `within`, `no-base`, `same`; `no-outcome` for a matrix of no cell |
| `groups[]` | For each group the cells are of, in its order: the `group`, the `counts` of its cells alone, its `verdict`, the worst of its scenarios' results, and its `results[]` |
| `groups[].results[]` | For each scenario of the group: its `scenario`, `rate` and the `seeds` of its cells; where a cell has a change, how many first forked `later` at the head, how many `earlier`, how many at the same epoch as `zero`, and the sign test's `p`, else each `null`; and its `result` |
| `summary[]` | Without a base, for each scenario: its `scenario`, `group`, `loss` and `rate`, how many `runs` and how many of them have no outcome as `no-outcome`, how many ended `whole`, `behind`, `split` and `stalled`, the `lowest`, `median` and `highest` of the `epochs` without a fork and, as `split_at`, of the epoch the runs that split first forked at, and the `median` of the `lines_off_trunk`, of the `members_off_trunk`, of those that `never_joined`, and of the members `seated`; `null` with `--base` |

A scenario's result is the first of these that holds:

| Result | When |
|---|---|
| `unbuilt` | No run at the head was read, and a job there could not build chat-sim. |
| `no-outcome` | A cell has no outcome at the head. |
| `no-base` | No cell has an outcome at the base. |
| `same` | Every cell with an outcome at both sides came out the same at both. |
| `earlier`, `later` | Of the cells with a change, those that first forked later at the head and those that forked earlier are apart beyond chance: an exact two-sided sign test on the two counts gives `ALPHA`, 1%, or less. More earlier is `earlier`. |
| `within` | The cells moved, within chance. |

A cell whose runs moved at the same first fork counts on neither side of the test. Without a base,
a result is `unbuilt`, `no-outcome` or else `null`. The order says what went wrong at the head
before what is missing at the base, and a group's result is the worst of its scenarios', so that
one group's runs forking later cannot hide the other's forking earlier.

## In one process

`--one-process` names `harness-run` (see
[A plan on a virtual clock](../../crates/harness/README.md#a-plan-on-a-virtual-clock)). The members
are clients in that one process, on a clock the runner moves. What that means for a run:

- Time is virtual. The runner moves its clock 50 ms at a time and waits for nothing, so a run
  takes the time its work takes and not the time its timers count. Every duration the run gives is
  of that clock but the wall time on the first line, and `budget` is seconds of that clock. A
  `hold` step moves the clock on by its seconds.
- No delivery node is between the members. A frame is lost or late where the network model makes
  it so and nowhere else.
- A member answers a frame in the step that hands it over. Of fifty members seated ten at a time
  on the library's own timers, the ten that the second batch seats never vote on the steward
  election that batch opens, which is decided at its 30 s timeout, so the third batch is seated
  after 90 s. Seated one at a time, as `50-epochs.json` seats them, no epoch takes longer than
  70.05 s.
- There is one log. `group.log` holds the trace records of every member, stamped by the virtual
  clock, which starts at 2026-01-01T00:00:00Z in every run. The report takes it for one run that
  holds every installation, and names each `group`, after the log; `run.txt` has each member's
  name beside its installation id.
- An add or a post that the group refuses is tried again after each step of the clock, until the
  budget has run out.
- A member's rejected frames are counted by the words of the core's error, such as
  `DeMlsError(Mls(ProcessMessage(ValidationError(UnableToDecrypt(AeadError)))))`.

A runner that ends with a status other than 0, or by a signal, stops the run with how it ended and
the last three lines it wrote to standard error, and what it left for a result is not read. A
result that lacks a field the run reads of it, or holds something else in its place, is not read
either, and the run stops with the field's name:
`the runner left a result that cannot be read: members is missing`. A run that read no result is
not checked. What a duration of `timers` may be is the library's to say:

```console
3 members, group timers freeze_duration 0 ms, one process on a virtual clock
stopped after 0 of 2 steps: the runner ended with status 2: harness-run: tmp/run/plan.json: timers: Invalid conversation config: freeze_duration must be non-zero
not checked: no result of the runner was read
```

On Linux a scenario and its seeds give one run, whenever it is made. The runner draws every random
number of the run from the scenario's `seed`, the ids of the group and of its members among them,
and which commit the group applies follows from those. The seed of `network` fixes what a member's
model does with the `n`-th frame to reach it. Two runs of one scenario write the same `group.log`,
and the same report but for the wall time and the load average. A change to logos-chat or to the
harness that changes a run shows as a difference between the logs of one scenario made before and
after it, and that difference can be a re-deal: the guide's
[The same run, and when it is not](https://logos-messaging.github.io/chat-sim/guide/understand/how-a-run-works/#the-same-run-and-when-it-is-not)
says what else a run depends on.

### What a run in one process writes

The run's directory holds `plan.json`, the plan the runner was told, and `group.log` and
`result.json`, which the runner leaves (see
[What a run leaves](../../crates/harness/README.md#what-a-run-leaves)). `runner.stderr` and
`runner.out` are what the runner wrote to standard error and to standard output. A run that is not
a grow also holds `expect.json`, what the report was held against, as
[`forks check`](../forks/README.md#checking-a-report) reads it, and `report.txt` and
`report.json`, the report `forks report` drew of the log. `run.txt` is what the run prints, and
`run.json` holds the same and more:

| Field | Holds |
|-------|-------|
| `rig` | `one process` |
| `members`, `group`, `timers`, `budget`, `seed` | What the run was given, the seed as this run had it |
| `network` | The `network` of the scenario with the seed this run had, or `null` |
| `ok` | Whether every step was done and the run is as expected |
| `stopped` | What stopped the steps, when something did |
| `steps`, `steps_done` | How many steps the scenario has, and how many were done |
| `virtual_s`, `wall_s` | How far the runner moved its clock, and how long it took on the machine; `null` when no result was read |
| `load` | The load average of the machine at the `start` and at the `end` of the run: over 1, 5 and 15 minutes |
| `conversation` | The id of the group, once it was created |
| `check` | Whether the report is `as_expected`, and the `differences` as `forks check` printed them; `null` when the run was not checked |
| `unchecked` | Why a run was not checked where that needs saying: how `forks check` ended when it gave no answer, or that a `grow` step expects no outcome |
| `withheld` | What a `withhold` step withheld: `from` whom, the `app_id` of the candidate's sender and the `seat` of the steward that made it, as `minter` the member that told that app id, and whether the group `applied` it, as the member's model answered at the end; `null` without the step |
| `held_to` | Which outcome the report was held to: `applied` or `not applied`, and `null` where `withheld` is |
| `verdict`, `at_tip`, `seated` | The report's verdict and how many of the seated it has at the tip, `null` for a grow; and how many the steps seated, of a grow those added and not removed |
| `nodes[]` | Each member as the runner's result has it: its `name`, `installation`, `account` and `app_id`, whether the steps `seated` it, and as `inbound_errors` how many frames it rejected, by the reason. With `network`, the state of its model as `network`, and as `network_dropped` how many frames the model dropped, by their `why` |
| `batches[]` | For each batch its `joiners`, the `members` the group then had, and `seated_s` on the virtual clock, from the first try of the add to the last roster |
| `posts[]` | For each post its `poster`, its `readers`, those that left it `unread`, and `read` on the virtual clock: `count`, `median` and `worst` |
| `holds[]` | The `seconds` of each `hold` step that was done |
| `grow` | What the runner's result says of a `grow` step: its lines, who fell behind, who was behind at the end, never joined or was removed, and its rounds (see `grow` in [What a run leaves](../../crates/harness/README.md#what-a-run-leaves)); `null` in a run of other steps |

The first line of `run.txt` says that the run was one process, how far the virtual clock went, how
long that took, the run's seeds and rates, and names a GroupV3 group in place of the group timers.
With `network`, the table of the members has how many frames `arrived` at each member's model, and
how many of them the model `passed`, `held` at the end and `lost`. A run of a `grow` step ends with
how the step ended, its rounds, a row a line and who fell behind, as the guide's
[A group that grows](https://logos-messaging.github.io/chat-sim/guide/understand/scenarios/#a-group-that-grows)
shows.

## What it does not do

- It does not build the runner, and runs whatever it is given. The flake's app builds it.
- It forms one group. Its steps seat members, send posts and set the rules of the network model:
  it removes nobody but in a `grow` step of a `size`.
- It checks a duration of `timers` for its name and for being a whole number of milliseconds
  below 2^64: what the library takes is the runner's to refuse.
- It does not look at what a report holds beyond the verdict, the positions and the posts (see
  [forks](../forks/README.md#checking-a-report)), so a run that went as expected may hold
  `unreadable` records of frames that are no posts.
- It makes a run again only on Linux. Elsewhere the ids come from the OS, and a batch's seconds
  can come out a step apart from run to run.

## Tests

```sh
python3 -B -m unittest discover -s tools/sim
```

They run the tool against `tests/standin_runner.py`, a stand-in for the runner that runs no group
and leaves the log and the result a runner leaves, and against `forks` as the repository has it.
`tests/fixtures/result.json` is a result the runner left, and `tests/fixtures/grow_result.json` one
of a `grow` step. What the stand-in leaves is held against their fields, and a run is given a
result whole and with each field the run reads taken out in turn. `tests/fixtures/grow_whole.json`,
`grow_split.json` and `grow_stalled.json` are the `run.json` of three runs of a `grow` step, cut to
what `compare` reads. `tests/fixtures/matrix/` is what six jobs uploaded of seeds 0 and 1 of
`50-epochs-by-saro-waku-loss-0.1.json`, `-1` and `-5`, each in the directory of its artifact, every
`run.json` cut to what `matrix` reads.
