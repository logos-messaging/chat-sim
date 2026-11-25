---
title: chat-sim's own reports
description: What chat-sim's CI runs on chat-sim's own master and pull requests, what the reports list holds, and how long such a report is kept.
---

chat-sim's CI runs its own simulations on every push to master and on every pull request to
master, when a path the runs depend on changed
([CONTRIBUTING.md](https://github.com/logos-messaging/chat-sim/blob/master/CONTRIBUTING.md#what-ci-runs)
lists the jobs). A pull request's runs are of its head commit, not of its merge into master. Their
report goes under [reports](/chat-sim/reports/) of the site, beside this guide, also when a
simulation failed. When no such path changed, the simulations are skipped, and the last report
stands.

The list has a row for master, then one for each open pull request, the highest number first. A
row says what ran, its commit, when, and how many sweeps were as expected. It links the report and
the CI run.

A report page has one row per sweep: `as expected`, `not as expected` or `no outcome`. Then come
each sweep's `compare` output, its `sweep.txt` and each run's text, and the two runs of fifty
epochs.

A pull request's row and page go when it closes. A pull request from a fork gets neither: its
token cannot push to the site.

## logos-chat's runs

The runs chat-sim makes for logos-chat are under Track:
[Your pull request's result](/chat-sim/guide/track/pull-request/) for a pull request, and
[logos-chat main over time](/chat-sim/guide/track/main/) for its `main`.

## The pages of logos-chat's runs

[The report page](/chat-sim/guide/track/pull-request/#the-report-page) of a pull request,
[the page of a point](/chat-sim/guide/track/main/#the-page-of-a-point), and
[the long runs](/chat-sim/guide/track/main/#the-long-runs) say what each page of logos-chat's runs
shows.
