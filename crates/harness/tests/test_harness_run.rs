//! `harness-run` as a command: what it leaves, and how it ends.
//!
//! ```sh
//! cargo test -p harness --test test_harness_run
//! ```

use libchat::GroupV2Config;
use serde_json::{Value, json};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::Duration;
use tempfile::TempDir;

/// Runs the runner with no `LOG`, so that its log holds the records alone.
fn harness_run(args: &[&PathBuf]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_harness-run"))
        .args(args)
        .env_remove("LOG")
        .output()
        .expect("the runner starts")
}

/// A directory with `plan` in a file, and the path of that file.
fn written(plan: &str) -> (TempDir, PathBuf) {
    let directory = tempfile::tempdir().expect("a directory");
    let file = directory.path().join("plan.json");
    fs::write(&file, plan).expect("the plan is written");
    (directory, file)
}

/// A plan that seats Raya beside Saro within `budget_s`.
fn plan(budget_s: u64) -> String {
    let steps = json!([{"step": "seat", "to": 2, "batch": 1}]);
    json!({
        "members": ["saro", "raya"],
        "timers": "fast",
        "budget_s": budget_s,
        "start": 1_791_817_445u64,
        "steps": steps,
    })
    .to_string()
}

/// What a run that ended with 0 and wrote nothing to standard error left in
/// `out`: its result, and how many lines its log holds.
fn left(ended: &Output, out: &Path) -> (Value, usize) {
    assert_eq!(ended.status.code(), Some(0), "{ended:?}");
    assert!(ended.stderr.is_empty(), "{ended:?}");
    let read = |name: &str| fs::read_to_string(out.join(name)).expect(name);
    let result = serde_json::from_str(&read("result.json")).expect("a result");
    (result, read("group.log").lines().count())
}

#[test]
fn a_plan_done_to_its_end_exits_with_0_and_leaves_a_log_and_a_result() {
    let (directory, file) = written(&plan(30));
    let out = directory.path().join("out");

    let ended = harness_run(&[&file, &out]);

    let (result, lines) = left(&ended, &out);
    assert_eq!(
        [&result["stopped"], &result["steps_done"]],
        [&Value::Null, &json!(1)]
    );
    // Each member writes that it runs and each roster it holds.
    assert_eq!(lines, 5);
}

#[test]
fn steps_that_stop_exit_with_0_and_a_result_that_says_why() {
    let (directory, file) = written(&plan(0));
    let out = directory.path().join("out");

    let ended = harness_run(&[&file, &out]);

    let (result, _) = left(&ended, &out);
    assert_eq!(
        result["stopped"],
        "the roster of 2 members was not held within the budget by: saro, raya"
    );
    assert_eq!(result["steps_done"], 0);
}

/// The log a run of eight members under `seed` left, and its result but for
/// the wall time it and each batch took.
#[cfg(target_os = "linux")]
fn run_under(seed: u64) -> (String, Value) {
    let names = [
        "saro", "raya", "pax", "mira", "kes", "tomo", "peer7", "peer8",
    ];
    let steps = json!([{"step": "seat", "to": 8, "batch": 4}, {"step": "post", "from": ["pax"]}]);
    let network = json!({"seed": 1, "loss": 0.02, "welcome_loss": 0, "delay_ms": {"pax": 40}});
    let plan = json!({
        "members": names, "timers": "fast", "budget_s": 30, "start": 1_791_817_445u64,
        "seed": seed, "network": network, "steps": steps,
    });
    let (directory, file) = written(&plan.to_string());
    let out = directory.path().join("out");
    let ended = harness_run(&[&file, &out]);
    assert_eq!(ended.status.code(), Some(0), "{ended:?}");
    let (mut result, _) = left(&ended, &out);
    result.as_object_mut().expect("a result").remove("wall_s");
    for batch in result["batches"].as_array_mut().expect("batches") {
        batch.as_object_mut().expect("a batch").remove("wall_s");
    }
    (
        fs::read_to_string(out.join("group.log")).expect("a log"),
        result,
    )
}

#[cfg(target_os = "linux")]
#[test]
fn a_plan_and_its_seed_give_one_run_whenever_it_is_run() {
    let first = run_under(1);
    // A key package's lifetime counts whole seconds of the time of day.
    std::thread::sleep(Duration::from_millis(1100));

    assert_eq!(run_under(1), first);
    assert_ne!(run_under(2).0, first.0);
}

/// What a run that ended with 2 said on standard error: one line.
fn said(ended: &Output) -> String {
    assert_eq!(ended.status.code(), Some(2), "{ended:?}");
    assert!(ended.stdout.is_empty(), "{ended:?}");
    let said = String::from_utf8(ended.stderr.clone()).expect("utf-8");
    assert_eq!(said.matches('\n').count(), 1, "{said}");
    said.trim_end().to_string()
}

#[test]
fn a_plan_that_cannot_be_read_exits_with_2_and_says_why() {
    let no_freeze = GroupV2Config {
        freeze_duration: Duration::ZERO,
        ..GroupV2Config::default()
    };
    let refusal = no_freeze.validate().expect_err("the library refuses it");
    let refused = [
        (
            plan(30).replace("\"to\":2", "\"to\":\"2\""),
            "steps[0].to is not a whole number".to_string(),
        ),
        (
            plan(30).replace("\"fast\"", "{\"freeze_duration\":0}"),
            format!("timers: {refusal}"),
        ),
    ];
    for (plan, why) in refused {
        let (directory, file) = written(&plan);
        let out = directory.path().join("out");

        let ended = harness_run(&[&file, &out]);

        assert_eq!(
            said(&ended),
            format!("harness-run: {}: {why}", file.display())
        );
        assert!(!out.exists(), "a plan that is not run leaves nothing");
    }

    let directory = tempfile::tempdir().expect("a directory");
    let (missing, out) = (
        directory.path().join("plan.json"),
        directory.path().join("out"),
    );
    let ended = harness_run(&[&missing, &out]);
    let prefix = format!("harness-run: {}: ", missing.display());
    assert!(said(&ended).starts_with(&prefix), "{ended:?}");
}

#[test]
fn a_directory_that_cannot_be_written_exits_with_2_and_says_why() {
    // The plan's own file is where the directory should be.
    let (_directory, file) = written(&plan(30));

    let ended = harness_run(&[&file, &file]);

    let prefix = format!("harness-run: {}: ", file.display());
    assert!(said(&ended).starts_with(&prefix), "{ended:?}");
}

#[test]
fn a_result_that_cannot_be_written_exits_with_2_before_a_step_is_done() {
    let (directory, file) = written(&plan(30));
    let out = directory.path().join("out");
    // A directory is where the result should be.
    let result = out.join("result.json");
    fs::create_dir_all(&result).expect("a directory");

    let ended = harness_run(&[&file, &out]);

    let prefix = format!("harness-run: {}: ", result.display());
    assert!(said(&ended).starts_with(&prefix), "{ended:?}");
    // A member writes that it runs as soon as it is made.
    let log = fs::read_to_string(out.join("group.log")).expect("the log");
    assert_eq!(log, "");
}

#[test]
fn anything_but_a_plan_and_a_directory_exits_with_2_and_the_usage() {
    let (directory, file) = written(&plan(30));
    let out = directory.path().join("out");

    for args in [&[][..], &[&file], &[&file, &out, &out]] {
        let ended = harness_run(args);
        assert_eq!(said(&ended), "usage: harness-run PLAN.json OUT_DIR");
    }
    assert!(!out.exists());
}
