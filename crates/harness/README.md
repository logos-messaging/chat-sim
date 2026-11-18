# harness

A GroupV2 or GroupV3 group of libchat clients in one process, on a virtual clock, and
`harness-run`, which does a plan's steps on it. The clients are logos-chat's, at the revision
the workspace pins in [`Cargo.toml`](../../Cargo.toml); what stands between them is this
repository's [`network-model`](../network-model/src/lib.rs).

## A plan on a virtual clock

`harness-run` seats the members a plan names in one group and does the plan's steps, in
one process and on a virtual clock. Nothing is between the members but the model of a network a
plan may give each of them, so a run takes the time its work takes and not the time its timers
count:

```sh
cargo run --release -p harness --bin harness-run -- PLAN.json OUT_DIR
```

It writes `group.log` and `result.json` in `OUT_DIR`, which it makes when there is none, and exits
with 0. Steps that stopped are a run all the same, and the result says why they did. A plan that
cannot be read, a directory that cannot be written and anything but those two arguments end it
with 2 and one line on standard error, and nothing is run.

### The plan

A plan is a JSON object. A field that is not what the tables say is refused by its name
(`steps[2].to is not a whole number`), and so is a field they do not hold.

| Field | Holds |
|-------|-------|
| `members` | The members' names in the order they are seated, each of lowercase letters and digits, none twice. The first creates the group |
| `group` | Left out, `"v2"` or `"v3"`: the group the first member creates, GroupV2 when left out. A GroupV3 group removes no member and has no commit candidate, and the frames it sends outside SDS name no sender for a cut to stop, so with `"v3"` a `withhold` step, a `cut` step and a `grow` step of a `size` are refused. It commits an add once its finalize timeout of 120 s has passed, and `timers` has no part in it |
| `timers` | `"fast"` for the durations the tests of this crate run under, `"default"` for the library's own, or an object of durations in milliseconds over the library's own: `voting_delay`, `consensus_timeout`, `commit_batch_window`, `freeze_duration`, `retry_window`, `backup_takeover_window`, `proposal_expiration`, `recovery_auto_commit_delay`. A set the library refuses is refused with its reason |
| `budget_s` | The seconds of virtual time, whole or not, that the waits of one batch, of one post or of one cut may take together |
| `start` | The time the virtual clock starts at, in whole seconds past the Unix epoch. One within the first hour is refused, since a key package's lifetime opens an hour before the time of day, and so is one past the time the clock counts to, which is in the year 2554 |
| `seed` | Left out, or a whole number: what every random number of the run is drawn from, 0 when left out |
| `network` | Left out, or `seed`, `loss`, `welcome_loss` and `delay_ms`. Each member then receives through a model of a lossy network under that seed and its own name. `loss` is the rate at which it loses a frame that is no welcome and `welcome_loss` the rate at which it loses a welcome, each a number from 0 to 1. `delay_ms` gives, by a member's name, how long its model holds each frame, and a member it does not name has no delay |
| `unwaited` | Left out, or `rosters` and `posts`: the names of the members no roster is waited on for, and of those no read of a post is waited on for |
| `steps` | The steps, in the order they are done. Each is an object whose `step` is its kind |

| Step | Fields | Does |
|------|--------|------|
| `seat` | `to`, `batch` | The creator adds the next members, `batch` of them at a time, until the group holds `to`. After each batch every member waited on comes to hold the new roster |
| `post` | `from`, a list of names | Each of them posts once, in that order, and every other seated member waited on comes to read the post |
| `cut` | `sides`, two lists of names | Each side is cut off from the frames of the other, once every member it names that the steps seated holds the conversation |
| `heal` | | No member is cut off from another |
| `withhold` | `from`, a name | The next commit candidate to reach that member is withheld from it. The batch that follows waits for it to be |
| `hold` | `seconds`, above 0 | Virtual time passes, and nothing is waited for |
| `grow` | `epochs`, from 1; `adders`, from 1, or `by`, the creator's name; `branches`, from 2; `size`, from 2 and twice `adders` | Members add new ones in rounds until the trunk holds `epochs` or `branches` main branches run side by side: with `adders` any member on any branch, with `by` the creator alone. With `size`, an adder whose add would take the group above it first removes a member. See [A group that grows](#a-group-that-grows) |

`cut`, `heal` and `withhold` are steps of a plan with a `network`. Saro, Raya and Pax in one group,
and a post from Pax:

```json
{
  "members": ["saro", "raya", "pax"],
  "timers": "fast",
  "budget_s": 30,
  "start": 1791817445,
  "seed": 1,
  "steps": [
    {"step": "seat", "to": 3, "batch": 2},
    {"step": "post", "from": ["pax"]}
  ]
}
```

A wait moves the virtual clock 50 ms at a time and looks after each step. An add or a post that
is refused is tried again after each step. What a member holds is read from its trace records as
they are written: it holds a roster by an `epoch` record of that many members, and it has read a
post by its `received` record.

A frame a model holds is handed over in the first step to start once its delay has passed. The
delay counts against the group's timers: with `"fast"` and 100 ms on every member, batches of
one seat no third member. The second member's vote on the add falls due at the creator as its
consensus timeout of 250 ms fires, and the timeout is served first.

When the steps end with a frame still held, the clock goes on until no model holds one, for the
largest delay of the plan at most and no longer than the budget, so the log says what became of
that frame.

The steps stop when a wait or the tries run out of the budget, and when a batch leaves the group
split: two of the members waited on hold the epoch of the new roster in two states. One split
does not stop them, the one a lost commit candidate makes: the member
a candidate was withheld from holds that epoch in a state of its own, and every other member
waited on in one. That member is waited on no more. A `withhold` whose candidate does not arrive
within the batch that follows, or arrives before that batch's add is accepted, stops them as well.
So does a `withhold` whose rule cannot be armed: it is armed already, it has withheld its one
candidate, or the rule of another member is armed for the next batch. A `cut` stops them when a
member it names does not hold the conversation: at once for one the steps have not seated, and
for one they have seated when the budget has run out.

### A group that grows

A `grow` step is a plan's only step. Each round it draws from one to `adders` members, each of which
adds the next member of the plan at once, the creator's group then growing at whatever branch the
adder is on. A round ends when each member added holds the group and no member that was level with
its branch at the start is behind it, or when the budget runs out. A member is behind when it holds
a state another member has gone past. It is then waited on no more and drawn no more until it holds
a state no member has gone past. In a run of sixty at 5% loss, each of four members that fell behind
later held a state of its own, the one member of a branch; how it came to is not traced. An adder
refused all round is drawn no more until it holds another state. The draws come from the plan's
`seed`, on a stream apart from the one `getrandom` hands out: who is drawn follows from the seed and
from who can be drawn. With `by` in place of `adders` the creator is the one adder of every round
and adds one member, so that no member on a branch off its line is drawn to add. A GroupV3 group
applies one commit of an epoch: 120 s after the first is deferred, the one of the lowest
fingerprint of those deferred by then. Of the adds of a round one is applied, the joiners of the
others never join, and a round of more than one adder runs until the budget runs out.

With `size`, the group holds that many members at most. Each round still seats members of the plan
not yet seated, so a plan that is to reach `epochs` has a member for each add, and the group stays
at `size` by removing as many as it adds. An adder whose add would take the group above `size` first
removes the member seated longest ago that is not adding that round and is not the creator of `by`,
then adds. A removed member is drawn, waited on and counted no more: it is on no line, and neither
behind nor never joined. Once the round is over its client is dropped, with all it held of the
group. `size` is at least twice `adders`, so that a group at its size holds a member that is not
adding for each adder to remove.

The states the members hold, each after the one its members held before it, are lines. The
creator's first is on the trunk. Where a state has two or more after it, the one more members
hold or have gone past carries its line on, a tie going to the state the fork report ranks first,
and each other starts a branch off it, once a round is over: a line once given is kept. With `by`,
the one the creator holds or has gone past carries it on, so that the creator's line is the trunk.
A branch off the trunk is a main branch, and one off any other branch a sub-branch. The step ends
at the start of a round when the trunk holds `epochs`, when the trunk and the main branches are
`branches` or more, when every member of the plan is seated, or when no member can be drawn.

On Linux a plan and its seed give one run. `harness-run` stands in for libc's `getrandom`, which
every random draw of a run goes through, with a generator seeded by the plan's `seed`, and for its
`clock_gettime`, so that the time of day is the virtual clock. It runs openmls on one thread of
rayon's pool: on more than one, a run that removes members came out otherwise from run to run. Two
runs of one plan write the same log byte for byte, and the same result but for the wall time each
took, whenever they are run.
A run also depends on how many hash maps the process built before: std seeds the hasher of each new
`HashMap` and `HashSet` with keys drawn once per thread, one of them counted up by every map built
with a hasher of its own before it, while a clone keeps the keys of the map it copies. So a change
to the harness or to logos-chat that builds one map more or fewer can change how a run comes out.
Another seed draws other ids for the group and its members, and which commit the group applies
follows from them. The `seed` of `network` fixes what a member's model does with the n-th frame to
reach it, and nothing else. Elsewhere the ids come from the OS, and the `seated_s` of a batch, and
`virtual_s` with it, can come out a step apart from run to run.

### What a run leaves

`group.log` holds the trace records of every member, and of everything else the lines `LOG`
asks for. The stamp of a line is the virtual clock's time, which starts at `start`, so the fork
report reads the log as it reads one written on a wall clock:

```sh
python3 tools/forks report OUT_DIR/group.log
```

`result.json` is what the run came to. It is opened with the log and written when the steps are
done or have stopped, so a run that is ended from outside leaves it empty beside the log as far
as that got. The plan above left:

```json
{
  "batches": [
    {
      "joiners": 2,
      "members": 3,
      "seated_s": 0.5,
      "wall_s": 0.007026952
    }
  ],
  "conversation": "5bfb2f371a",
  "members": [
    {
      "account": "7361726f",
      "app_id": "62363065623863663830",
      "inbound_errors": {},
      "installation": "01469e2a",
      "name": "saro",
      "seated": true
    },
    {
      "account": "72617961",
      "app_id": "34373865336265666561",
      "inbound_errors": {},
      "installation": "4c454e6d",
      "name": "raya",
      "seated": true
    },
    {
      "account": "706178",
      "app_id": "61613863643635633266",
      "inbound_errors": {},
      "installation": "cc2e7d1e",
      "name": "pax",
      "seated": true
    }
  ],
  "posts": [
    {
      "poster": "pax",
      "read_s": [
        0.0,
        0.0
      ],
      "readers": 2,
      "unread": []
    }
  ],
  "steps_done": 2,
  "stopped": null,
  "virtual_s": 0.6,
  "wall_s": 0.010238333
}
```

| Field | Holds |
|-------|-------|
| `steps_done` | How many of the plan's steps were done to the end |
| `stopped` | `null`, or in one line why the steps stopped |
| `conversation` | The conversation's id, as the records write it |
| `members[]` | For each member of the plan its `name`, its `installation` and `account` as the records shorten them, whether the steps `seated` it, its `app_id` in hex, `null` until it has published in the conversation, and its `inbound_errors`: how many frames it rejected, by the reason. A member a grow step removed is as it was when the round that removed it was over, when its client was dropped |
| `members[].network` | In a plan with a `network`, the state of the member's model. The `seed`, `loss`, `welcome_loss` and `delay_ms` it was given. The app ids it is `cut` off from, and those it takes for its `own`: the app id on each frame it published, since a client of the harness is handed its own frames back. A GroupV3 member's frames sent outside SDS, its beacons, name no sender, so they come back to it as arrivals and can be lost as any other frame can. `withheld`, `null` until a candidate is withheld from it, then the `app_id` of the candidate's sender, the `seat` of the steward that minted it and `applied`: whether that steward has broadcast a welcome since, as it does for a commit of its own that the group applies. With no batch after the one the candidate was withheld in, that is whether the group applied the candidate. Whether the rule that withholds is `armed`. `frames`: how many `arrived`, and of those how many `passed` to the client, are `held` for the delay or were `dropped`, by the rule that dropped them |
| `members[].dropped[]` | In a plan with a `network`, each frame the member's model dropped: `n` numbers the frames that arrived, from 1, `why` is the rule (`loss`, `welcome_loss`, `cut`, `withheld`), `kind` what the frame is (`welcome`, `candidate`, `group`, `other`) and `bytes` its length. `app_id` is its sender's, left out for a frame that has none, and `seat` that of the steward that minted a commit candidate |
| `batches[]` | For each batch that was seated its `joiners`, the `members` the group then had, `seated_s`, the virtual seconds from the first try of the add to the last roster, and `wall_s`, the seconds it took on the machine |
| `posts[]` | For each post its `poster`, how many `readers` were waited on, the names of those that left it `unread` within the budget, and as `read_s` the virtual seconds from the send to each read |
| `grow` | Only in the result of a `grow` step: why it `ended`, the `trunk_epoch`, how many lines are `main`, the trunk among them, and the `lines`, the trunk first and then each branch in the order it forked: its `kind` (`trunk`, `main`, `sub`), the line it forked `from` and the epoch it `forked_at`, `null` for the trunk, the last `epoch` it holds and the `members` whose state is on it. `fell_behind` holds the `name` and `epoch` of each member that came out of a round behind, having gone into it level or before it held the group. `behind` names the members behind at the end, `never_joined` the members added that never held the group and are not removed, and `removed` the members a step of a `size` removed, in the order it removed them. Each of `rounds` names its `adders`, the `joiners` they added, the adders `refused` the whole round, and the virtual seconds it took as `settled_s` |
| `virtual_s`, `wall_s` | How far the run moved the virtual clock, and how long it took on the machine. The clock ends past the last stamp of the log when the step that wrote the last line moved it afterwards, or when a wait or a hold after that line wrote nothing |
