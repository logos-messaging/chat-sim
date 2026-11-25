---
title: chat-sim's own reports
description: What chat-sim's CI runs on its own master and pull requests, what the reports list holds, and how long a pull request's report is kept.
---

chat-sim's CI runs its own simulations on every push to master and on every pull request to
master, when a path the runs depend on changed. A pull request's runs are of its head commit, not of
its merge into master. Its publish job writes what they came to under
[reports](/chat-sim/reports/) of the site, beside this guide. Where no such path changed, the
simulations and the publish are skipped, and the last report stands.

| Job | Runs |
|---|---|
| Sim in one process | `50-epochs.json` and `50-epochs-waku.json`, once each, each held to what its scenario expects |
| Sim held to what is expected | for each file in `tools/sim/expected/`, a sweep of the scenario of that name, as many runs as the file holds, held by `tools/sim compare` to the file |
| Publish the report to GitHub Pages | the report of the two, from the text they upload, also when one failed |

The list has a row for master, then one for each open pull request, the highest number first: what
ran, its commit, when, how many sweeps came out as expected, and links to the report and the CI
run. A report page has a row a sweep, `as expected`, `not as expected` or `no outcome`, then each
sweep's `compare` output, its `sweep.txt` and each run's text, and the two runs of fifty epochs.
[CONTRIBUTING.md](https://github.com/logos-messaging/chat-sim/blob/master/CONTRIBUTING.md) says
what a change to the runs takes.

A row and its page are those of the newest CI run to publish one. A pull request's row and page go
when it closes. One closed with merge conflicts keeps them until another closes or a weekly sweep
runs. That sweep also removes any that a late run wrote again. A pull request from a fork gets
neither: its token cannot push to the site.

## logos-chat's runs

What chat-sim runs for logos-chat is under Track:
[Your pull request's result](/chat-sim/guide/track/pull-request/) for a pull request, and
[logos-chat main over time](/chat-sim/guide/track/main/) for its main.

## The pages of logos-chat's runs

[The report page](/chat-sim/guide/track/pull-request/#the-report-page) of a pull request,
[the page of a point](/chat-sim/guide/track/main/#the-page-of-a-point), and
[the long runs](/chat-sim/guide/track/main/#the-long-runs) say what each page of logos-chat's runs
shows.
