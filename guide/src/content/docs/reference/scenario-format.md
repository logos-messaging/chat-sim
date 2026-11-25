---
title: Scenario format
description: Every field and step of a scenario file, what each defaults to, and what tools/sim refuses.
---

A scenario is a JSON object. `tools/sim` checks every field before anything starts, and refuses a
file by the field's name, such as `` `to` of steps[2] is not a whole number ``. A field the tables
below do not hold is refused too.

```json
{
  "members": 200,
  "timers": "default",
  "network": {"seed": 1, "loss": 0.05, "welcome_loss": 0.05, "delay_ms": {"saro": 518, "raya": 529}},
  "steps": [{"step": "grow", "epochs": 50, "adders": 3, "branches": 5}]
}
```

Members are named `saro`, `raya`, `pax`, `mira`, `kes`, `tomo`, then `peer7`, `peer8` and on.
`saro` creates the group.

| Field | Default | Holds |
|---|---|---|
| `members` | | how many members there are, 1 to 65534 |
| `steps` | | what they do, in order: a list of one step or more |
| `group` | `v2` | `v2` for GroupV2, or `v3` for GroupV3 |
| `timers` | `fast` | of a GroupV2 group: `fast`, `default` for the library's own, or an object of durations in milliseconds over the library's own |
| `budget` | `180` | the seconds of the virtual clock the waits of one batch, one post or one cut may take together |
| `seed` | `0` | what every random number of the run is drawn from |
| `network` | none | the model every member receives through |
| `expect` | a whole group | how the outcome differs from a whole group at the tip in which every post was read |

The durations `timers` can give are `voting_delay`, `consensus_timeout`, `commit_batch_window`,
`freeze_duration`, `retry_window`, `backup_takeover_window`, `proposal_expiration` and
`recovery_auto_commit_delay`. A GroupV3 group keeps timers of its own, whatever `timers` says.

## The network

| Field | Default | Holds |
|---|---|---|
| `seed` | | what the model draws from: with the member's name and the frames shown so far, it decides each frame |
| `loss` | `0` | the share of the frames each member loses, from 0 to 1 |
| `welcome_loss` | `0` | the share of the Welcomes each member loses |
| `delay_ms` | `0` | how long each frame is held: one number for every member, or an object by name, where a member it does not name has none |

A frame the model loses never comes later. A wait that a lost frame leaves open runs to the end of
the budget.

## The steps

| Step | Fields | Does |
|---|---|---|
| `seat` | `to`, `batch` (10) | saro adds members, `batch` at a time, until the group holds `to` |
| `post` | `from` (`last`) | a member posts, and the others wait for it: the last seated, a name, or `all` in turn |
| `hold` | `seconds` | the clock runs for a while, and nothing is waited for |
| `cut` | `between` | cuts two lists of members off from each other's frames |
| `heal` | | ends the cut in force |
| `withhold` | `from` | the member loses the first commit candidate of the next batch to reach it |
| `grow` | `epochs`, `branches`, and `adders` or `by`, and `size` | members add members in rounds ([A group that grows](/chat-sim/guide/understand/scenarios/#a-group-that-grows)) |

`grow` takes `epochs` (at least 1), `branches` (at least 2, the trunk being one), and either
`adders`, the most members drawn to add a round, or `by`, the one member who adds. `size`, at least
twice `adders`, holds the group at that many members.

## What is refused

- A `grow` step with any other step, or with `expect`: a grow expects no outcome.
- `cut`, `heal` and `withhold` without `network`. A `cut` while one is in force, and a `heal` with
  none.
- On a GroupV3 group, `withhold`, `cut` and a `grow` with `size`: it removes no member, has no
  commit candidate, and its frames name no sender for a cut to stop.
- A `withhold` that is not followed by a `seat` of one batch, a second one, one with a `cut`, one
  with loss above 0, and one whose member posts later.
- A name that is no member's, or a member that is not seated by the step that names it.

## What is expected

`expect` is held to the fork report of a run that is not a grow.

| Field | Holds |
|---|---|
| `verdict` | the verdict, such as `whole` or `split at epoch 4` |
| `positions` | by name, a member's position: `tip`, `behind`, `lineage unknown`, `removed`, `never joined` or `no data` |
| `posts` | by name, what becomes of each post another member sends while it is seated: `read`, `unreadable`, `not a member`, `no log` or `no record` |
| `applied` | with a `withhold` step, the outcome when the group applied the withheld candidate; the rest is then the outcome when it did not |

## Seeds in a sweep

Run `k` of a sweep, from 1, takes `seed + k - 1`, and the network's seed likewise. So a sweep from
run 1 of a scenario that sets no seed runs seeds 0, 1, 2 and on. `sweep --from N` runs the runs from
`N`, each with the seeds it has in a sweep from run 1.
