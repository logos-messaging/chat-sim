//! A plan: what a run is told to do, read from JSON a field at a time so that
//! a refusal names the field and what it should be.

use crate::test_client::fast_group_v2_config;
use libchat::{GroupV2Config, MockClock};
use network_model::{NotARate, Rate};
use serde_json::{Map, Value};
use std::fmt;
use std::time::Duration;

/// How long before the time of day OpenMLS opens a key package's lifetime.
const KEY_PACKAGE_MARGIN: Duration = Duration::from_secs(60 * 60);

/// Why a text is no plan a run can do.
#[derive(Debug)]
pub(crate) struct Refused(String);

impl fmt::Display for Refused {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// What a run is told. A member is its place in `members`.
pub(crate) struct Plan {
    /// The names, in the order the members are seated. The first creates the
    /// group.
    pub members: Vec<String>,
    pub timers: GroupV2Config,
    /// The virtual time the waits of one batch, of one post or of one cut
    /// may take together.
    pub budget: Duration,
    /// Where the virtual clock starts, past the Unix epoch.
    pub start: Duration,
    /// What harness-run draws every random number of the run from.
    pub seed: u64,
    pub network: Option<Network>,
    pub unwaited: Unwaited,
    pub steps: Vec<Step>,
}

/// The model of a lossy network that what each member receives goes through.
pub(crate) struct Network {
    pub seed: u64,
    pub loss: Rate,
    pub welcome_loss: Rate,
    /// How long each member's model holds a frame, in milliseconds, by
    /// member.
    pub delay_ms: Vec<u64>,
}

/// The members that waits leave out.
#[derive(Default)]
pub(crate) struct Unwaited {
    /// No roster is waited on for these.
    pub rosters: Vec<usize>,
    /// No read of a post is waited on for these.
    pub posts: Vec<usize>,
}

pub(crate) enum Step {
    /// The creator adds the next members, `batch` at a time, until the group
    /// holds `to`.
    Seat { to: usize, batch: usize },
    /// Each of these members posts once, in this order.
    Post { from: Vec<usize> },
    /// Each side is cut off from the frames of the other.
    Cut { sides: [Vec<usize>; 2] },
    /// No member is cut off from any other.
    Heal,
    /// The next commit candidate to reach the member is withheld from it.
    Withhold { from: usize },
    /// Virtual time passes, and nothing is waited for.
    Hold { time: Duration },
    /// Members add new ones in rounds, `adders` saying which, until the
    /// trunk holds `epochs` or `branches` main branches run side by side.
    /// With `size`, an adder whose add would take the group above it first
    /// removes the member seated longest ago.
    Grow {
        epochs: u64,
        adders: Adders,
        branches: usize,
        size: Option<usize>,
    },
}

/// Who adds the members of a grow step's rounds.
pub(crate) enum Adders {
    /// From one to this many a round, drawn from the members on any branch.
    Drawn(usize),
    /// This member alone, one a round. The line it holds is the trunk.
    By(usize),
}

impl Plan {
    pub(crate) fn read(text: &str) -> Result<Self, Refused> {
        let value: Value =
            serde_json::from_str(text).map_err(|e| Refused(format!("it is not JSON: {e}")))?;
        let plan = Fields::of(&value, "")?;
        plan.only(
            "a plan",
            &[
                "members", "timers", "budget_s", "start", "seed", "network", "unwaited", "steps",
            ],
        )?;

        let members = members(plan.get("members")?)?;
        let timers = timers(plan.get("timers")?)?;
        let budget = seconds(plan.get("budget_s")?)
            .ok_or_else(|| Refused("budget_s is not a number of seconds, 0 or more".into()))?;
        let start = Duration::from_secs(whole(plan.get("start")?, "start")?);
        let clock = MockClock::new();
        clock.advance(start);
        if clock.now() != start {
            return Err(Refused("start is past the time the clock counts to".into()));
        }
        if start < KEY_PACKAGE_MARGIN {
            return Err(Refused(
                "start is within an hour of the Unix epoch, and a key package's lifetime opens an hour before the time of day".into(),
            ));
        }
        let seed = match plan.fields.get("seed") {
            Some(seed) => whole(seed, "seed")?,
            None => 0,
        };
        let network = plan
            .fields
            .get("network")
            .map(|network| Network::read(network, &members))
            .transpose()?;
        let unwaited = match plan.fields.get("unwaited") {
            Some(unwaited) => Unwaited::read(unwaited, &members)?,
            None => Unwaited::default(),
        };
        let steps = list(plan.get("steps")?, "steps")?
            .iter()
            .enumerate()
            .map(|(at, step)| Step::read(step, &format!("steps[{at}]"), &members))
            .collect::<Result<Vec<Step>, Refused>>()?;
        let grown = steps
            .iter()
            .position(|step| matches!(step, Step::Grow { .. }));
        if let Some(at) = grown.filter(|_| steps.len() > 1) {
            return Err(Refused(format!(
                "steps[{at}] is a grow step, and a grow step is a plan's only step"
            )));
        }
        if network.is_none() {
            for (at, step) in steps.iter().enumerate() {
                let kind = match step {
                    Step::Cut { .. } => "cut",
                    Step::Heal => "heal",
                    Step::Withhold { .. } => "withhold",
                    Step::Seat { .. }
                    | Step::Post { .. }
                    | Step::Hold { .. }
                    | Step::Grow { .. } => {
                        continue;
                    }
                };
                return Err(Refused(format!(
                    "steps[{at}] is a {kind} step, and the plan has no network"
                )));
            }
        }

        Ok(Self {
            members,
            timers,
            budget,
            start,
            seed,
            network,
            unwaited,
            steps,
        })
    }
}

impl Network {
    fn read(value: &Value, members: &[String]) -> Result<Self, Refused> {
        let network = Fields::of(value, "network")?;
        network.only("network", &["seed", "loss", "welcome_loss", "delay_ms"])?;
        let rate = |name: &str| {
            let rate = network.get(name)?.as_f64().ok_or(NotARate);
            rate.and_then(Rate::try_from)
                .map_err(|e| Refused(format!("{}: {e}", network.path(name))))
        };

        let delays = Fields::of(network.get("delay_ms")?, "network.delay_ms")?;
        let mut delay_ms = vec![0; members.len()];
        for (name, held) in delays.fields {
            let member = member(&Value::from(name.as_str()), "network.delay_ms", members)?;
            delay_ms[member] = whole(held, &delays.path(name))?;
        }
        Ok(Self {
            seed: whole(network.get("seed")?, "network.seed")?,
            loss: rate("loss")?,
            welcome_loss: rate("welcome_loss")?,
            delay_ms,
        })
    }
}

impl Unwaited {
    fn read(value: &Value, members: &[String]) -> Result<Self, Refused> {
        let unwaited = Fields::of(value, "unwaited")?;
        unwaited.only("unwaited", &["rosters", "posts"])?;
        let named = |name: &str| names(unwaited.get(name)?, &unwaited.path(name), members);
        Ok(Self {
            rosters: named("rosters")?,
            posts: named("posts")?,
        })
    }
}

impl Step {
    fn read(value: &Value, path: &str, members: &[String]) -> Result<Self, Refused> {
        let step = Fields::of(value, path)?;
        let kind = step.get("step")?.as_str().unwrap_or_default();
        let fields: &[&str] = match kind {
            "seat" => &["step", "to", "batch"],
            "post" | "withhold" => &["step", "from"],
            "cut" => &["step", "sides"],
            "heal" => &["step"],
            "hold" => &["step", "seconds"],
            "grow" => &["step", "epochs", "adders", "by", "branches", "size"],
            _ => {
                return Err(Refused(format!(
                    "{path}.step is not seat, post, cut, heal, withhold, hold or grow"
                )));
            }
        };
        step.only(&format!("a {kind} step"), fields)?;

        Ok(match kind {
            "seat" => {
                let to = whole(step.get("to")?, &step.path("to"))?;
                let to = usize::try_from(to).unwrap_or(usize::MAX);
                if to > members.len() {
                    return Err(Refused(format!(
                        "{path}.to is above the {} members",
                        members.len()
                    )));
                }
                let batch = step.get("batch")?.as_u64();
                let batch = batch
                    .filter(|batch| *batch > 0)
                    .map(|batch| usize::try_from(batch).unwrap_or(usize::MAX))
                    .ok_or_else(|| {
                        Refused(format!("{path}.batch is not a whole number above 0"))
                    })?;
                Step::Seat { to, batch }
            }
            "post" => Step::Post {
                from: names(step.get("from")?, &step.path("from"), members)?,
            },
            "cut" => {
                let path = step.path("sides");
                let sides = step.get("sides")?.as_array().map(Vec::as_slice);
                let Some([one, other]) = sides else {
                    return Err(Refused(format!("{path} is not two lists of names")));
                };
                Step::Cut {
                    sides: [
                        names(one, &format!("{path}[0]"), members)?,
                        names(other, &format!("{path}[1]"), members)?,
                    ],
                }
            }
            "heal" => Step::Heal,
            "withhold" => Step::Withhold {
                from: member(step.get("from")?, &step.path("from"), members)?,
            },
            "grow" => {
                let above = |name: &str, least: u64| {
                    let number = step.get(name)?.as_u64().filter(|number| *number >= least);
                    number.ok_or_else(|| {
                        Refused(format!(
                            "{} is not a whole number of {least} or more",
                            step.path(name)
                        ))
                    })
                };
                let count = |number: u64| usize::try_from(number).unwrap_or(usize::MAX);
                let adders = match step.fields.get("by") {
                    Some(_) if step.fields.contains_key("adders") => {
                        return Err(Refused(format!(
                            "{path} holds adders and by, and a grow step holds one or the other"
                        )));
                    }
                    Some(by) => {
                        let by = member(by, &step.path("by"), members)?;
                        // A grow step is a plan's only step.
                        if by != 0 {
                            return Err(Refused(format!(
                                "{path}.by names {}, and a grow step begins with {} alone seated",
                                members[by], members[0]
                            )));
                        }
                        Adders::By(by)
                    }
                    None => Adders::Drawn(count(above("adders", 1)?)),
                };
                let size = match step.fields.get("size") {
                    // An adder removes a member other than itself.
                    Some(_) => Some(count(above("size", 2)?)),
                    None => None,
                };
                if let (Some(size), Adders::Drawn(most)) = (size, &adders)
                    && size < most.saturating_mul(2)
                {
                    return Err(Refused(format!(
                        "{path}.size is below twice adders, and each adder of a group at its \
                         size removes a member that adds none"
                    )));
                }
                Step::Grow {
                    epochs: above("epochs", 1)?,
                    adders,
                    // The trunk alone is one main branch.
                    branches: count(above("branches", 2)?),
                    size,
                }
            }
            _ => {
                let time = seconds(step.get("seconds")?).filter(|time| !time.is_zero());
                let time = time.ok_or_else(|| {
                    Refused(format!("{path}.seconds is not a number of seconds above 0"))
                })?;
                Step::Hold { time }
            }
        })
    }
}

/// An object of the plan and the path to it, which is empty for the plan
/// itself.
struct Fields<'a> {
    path: &'a str,
    fields: &'a Map<String, Value>,
}

impl<'a> Fields<'a> {
    fn of(value: &'a Value, path: &'a str) -> Result<Self, Refused> {
        let fields = value.as_object().ok_or_else(|| {
            let named = if path.is_empty() { "the plan" } else { path };
            Refused(format!("{named} is not an object"))
        })?;
        Ok(Self { path, fields })
    }

    /// The path to the field `name`.
    fn path(&self, name: &str) -> String {
        if self.path.is_empty() {
            name.to_string()
        } else {
            format!("{}.{name}", self.path)
        }
    }

    /// Refuses a field that is none of `names`, the fields of `what`.
    fn only(&self, what: &str, names: &[&str]) -> Result<(), Refused> {
        match self
            .fields
            .keys()
            .find(|name| !names.contains(&name.as_str()))
        {
            Some(name) => Err(Refused(format!(
                "{} is no field of {what}",
                self.path(name)
            ))),
            None => Ok(()),
        }
    }

    fn get(&self, name: &str) -> Result<&'a Value, Refused> {
        self.fields
            .get(name)
            .ok_or_else(|| Refused(format!("{} is missing", self.path(name))))
    }
}

fn whole(value: &Value, path: &str) -> Result<u64, Refused> {
    value
        .as_u64()
        .ok_or_else(|| Refused(format!("{path} is not a whole number")))
}

fn list<'a>(value: &'a Value, path: &str) -> Result<&'a Vec<Value>, Refused> {
    value
        .as_array()
        .ok_or_else(|| Refused(format!("{path} is not a list")))
}

/// A number of seconds, whole or not, as a duration.
fn seconds(value: &Value) -> Option<Duration> {
    Duration::try_from_secs_f64(value.as_f64()?).ok()
}

/// The names of `members`: one or more, each of lowercase letters and digits,
/// none twice.
fn members(value: &Value) -> Result<Vec<String>, Refused> {
    let mut members: Vec<String> = vec![];
    for (at, name) in list(value, "members")?.iter().enumerate() {
        let name = name.as_str().unwrap_or_default();
        let lowercase = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit();
        if name.is_empty() || !name.chars().all(lowercase) {
            return Err(Refused(format!(
                "members[{at}] is not a name of lowercase letters and digits"
            )));
        }
        if members.iter().any(|member| member == name) {
            return Err(Refused(format!("members[{at}] names {name} a second time")));
        }
        members.push(name.to_string());
    }
    if members.is_empty() {
        return Err(Refused("members is empty".into()));
    }
    Ok(members)
}

/// The member a name stands for.
fn member(value: &Value, path: &str, members: &[String]) -> Result<usize, Refused> {
    let name = value
        .as_str()
        .ok_or_else(|| Refused(format!("{path} is not a name")))?;
    members
        .iter()
        .position(|member| member == name)
        .ok_or_else(|| Refused(format!("{path} names {name}, who is not among members")))
}

fn names(value: &Value, path: &str, members: &[String]) -> Result<Vec<usize>, Refused> {
    list(value, path)?
        .iter()
        .enumerate()
        .map(|(at, name)| member(name, &format!("{path}[{at}]"), members))
        .collect()
}

/// A set of group timers by its name, or the library's own with each duration
/// the plan gives, in milliseconds, in place of the library's.
fn timers(value: &Value) -> Result<GroupV2Config, Refused> {
    let mut config = GroupV2Config::default();
    match value {
        Value::String(set) if set == "fast" => config = fast_group_v2_config(),
        Value::String(set) if set == "default" => {}
        Value::Object(durations) => {
            for (name, milliseconds) in durations {
                let path = format!("timers.{name}");
                let given = || whole(milliseconds, &path).map(Duration::from_millis);
                match name.as_str() {
                    "voting_delay" => config.voting_delay = given()?,
                    "consensus_timeout" => config.consensus_timeout = given()?,
                    "commit_batch_window" => config.commit_batch_window = given()?,
                    "freeze_duration" => config.freeze_duration = given()?,
                    "retry_window" => config.retry_window = given()?,
                    "backup_takeover_window" => config.backup_takeover_window = given()?,
                    "proposal_expiration" => config.proposal_expiration = given()?,
                    "recovery_auto_commit_delay" => {
                        config.recovery_auto_commit_delay = Some(given()?)
                    }
                    _ => {
                        return Err(Refused(format!(
                            "{path} is no duration of the group's settings"
                        )));
                    }
                }
            }
        }
        _ => {
            return Err(Refused(
                "timers is not \"fast\", \"default\" or an object of durations".into(),
            ));
        }
    }
    // What a set of durations may be is the library's to say.
    config
        .validate()
        .map_err(|e| Refused(format!("timers: {e}")))?;
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A plan that is read, for a test to break in one place.
    fn plan() -> Value {
        json!({
            "members": ["saro", "raya", "pax"],
            "timers": "fast",
            "budget_s": 30,
            "start": 1_791_817_445u64,
            "seed": 3,
            "network": {"seed": 7, "loss": 0.05, "welcome_loss": 0, "delay_ms": {"pax": 40}},
            "unwaited": {"rosters": ["pax"], "posts": ["raya", "pax"]},
            "steps": [
                {"step": "seat", "to": 3, "batch": 2},
                {"step": "post", "from": ["pax", "saro"]},
                {"step": "cut", "sides": [["saro"], ["raya", "pax"]]},
                {"step": "heal"},
                {"step": "withhold", "from": "raya"},
                {"step": "hold", "seconds": 0.25},
            ],
        })
    }

    /// The plan with `value` at `path`, a JSON pointer, or without the field
    /// there when `value` is `None`.
    fn with(path: &str, value: Option<Value>) -> Value {
        let mut plan = plan();
        let (parent, name) = path.rsplit_once('/').expect("a pointer");
        let parent = plan.pointer_mut(parent).expect("the parent is there");
        match (parent, value) {
            (Value::Object(fields), Some(value)) => {
                fields.insert(name.to_string(), value);
            }
            (Value::Object(fields), None) => {
                fields.remove(name);
            }
            (Value::Array(items), Some(value)) => items[name.parse::<usize>().unwrap()] = value,
            (parent, _) => panic!("{parent} holds no {name}"),
        }
        plan
    }

    fn refusal(plan: Value) -> String {
        match Plan::read(&plan.to_string()) {
            Ok(_) => panic!("the plan is read: {plan}"),
            Err(refused) => refused.to_string(),
        }
    }

    #[test]
    fn a_plan_is_read_with_every_name_resolved() {
        let plan = Plan::read(&plan().to_string()).expect("the plan is read");

        assert_eq!(plan.members, ["saro", "raya", "pax"]);
        assert_eq!(plan.timers.consensus_timeout, Duration::from_millis(250));
        assert_eq!(plan.budget, Duration::from_secs(30));
        assert_eq!(plan.start, Duration::from_secs(1_791_817_445));
        assert_eq!(plan.seed, 3);
        let network = plan.network.expect("a network");
        assert_eq!(network.seed, 7);
        assert_eq!(
            (f64::from(network.loss), f64::from(network.welcome_loss)),
            (0.05, 0.0)
        );
        assert_eq!(network.delay_ms, [0, 0, 40]);
        assert_eq!(plan.unwaited.rosters, [2]);
        assert_eq!(plan.unwaited.posts, [1, 2]);
        let [seat, post, cut, heal, withhold, hold] = &plan.steps[..] else {
            panic!("{} steps", plan.steps.len());
        };
        assert!(matches!(seat, Step::Seat { to: 3, batch: 2 }));
        assert!(matches!(post, Step::Post { from } if from[..] == [2, 0]));
        assert!(matches!(cut, Step::Cut { sides } if sides[0] == [0] && sides[1] == [1, 2]));
        assert!(matches!(heal, Step::Heal));
        assert!(matches!(withhold, Step::Withhold { from: 1 }));
        assert!(matches!(hold, Step::Hold { time } if *time == Duration::from_millis(250)));
    }

    #[test]
    fn a_plan_needs_neither_a_seed_a_network_nor_members_left_unwaited() {
        let mut bare = plan();
        let fields = bare.as_object_mut().unwrap();
        fields.remove("seed");
        fields.remove("network");
        fields.remove("unwaited");
        fields.insert("steps".into(), json!([]));
        fields.insert("budget_s".into(), json!(0.5));

        let plan = Plan::read(&bare.to_string()).expect("the plan is read");

        assert_eq!(plan.seed, 0);
        assert!(plan.network.is_none());
        assert!(plan.unwaited.rosters.is_empty() && plan.unwaited.posts.is_empty());
        assert!(plan.steps.is_empty());
        assert_eq!(plan.budget, Duration::from_millis(500));
    }

    #[test]
    fn timers_are_a_set_or_durations_over_the_librarys_own() {
        let timers = |timers: Value| {
            let plan = with("/timers", Some(timers)).to_string();
            Plan::read(&plan).expect("the plan is read").timers
        };
        let library = GroupV2Config::default();

        let fast = timers(json!("fast"));
        assert_eq!(fast.voting_delay, Duration::from_millis(50));
        assert_eq!(fast.proposal_expiration, Duration::from_millis(4000));
        assert_eq!(fast.retry_window, library.retry_window);

        let own = timers(json!("default"));
        assert_eq!(own.voting_delay, library.voting_delay);
        assert_eq!(own.commit_batch_window, library.commit_batch_window);

        let given = timers(json!({
            "voting_delay": 1,
            "consensus_timeout": 2,
            "commit_batch_window": 3,
            "freeze_duration": 4,
            "retry_window": 5,
            "backup_takeover_window": 6,
            "proposal_expiration": 7,
            "recovery_auto_commit_delay": 8,
        }));
        let milliseconds = [
            given.voting_delay,
            given.consensus_timeout,
            given.commit_batch_window,
            given.freeze_duration,
            given.retry_window,
            given.backup_takeover_window,
            given.proposal_expiration,
            given.recovery_auto_commit_delay.expect("a delay"),
        ];
        assert_eq!(
            milliseconds.map(|given| given.as_millis()),
            [1, 2, 3, 4, 5, 6, 7, 8]
        );

        let one = timers(json!({"freeze_duration": 700}));
        assert_eq!(one.freeze_duration, Duration::from_millis(700));
        assert_eq!(one.voting_delay, library.voting_delay);
    }

    #[test]
    fn what_is_no_plan_is_refused_by_the_name_of_its_field() {
        let refused = [
            ("/colour", json!(1), "colour is no field of a plan"),
            ("/members", json!("saro"), "members is not a list"),
            ("/members", json!([]), "members is empty"),
            (
                "/members/1",
                json!("Raya"),
                "members[1] is not a name of lowercase letters and digits",
            ),
            (
                "/members/1",
                json!(""),
                "members[1] is not a name of lowercase letters and digits",
            ),
            (
                "/members/2",
                json!(7),
                "members[2] is not a name of lowercase letters and digits",
            ),
            (
                "/members/2",
                json!("saro"),
                "members[2] names saro a second time",
            ),
            (
                "/timers",
                json!("slow"),
                "timers is not \"fast\", \"default\" or an object of durations",
            ),
            (
                "/timers",
                json!({"patience": 5}),
                "timers.patience is no duration of the group's settings",
            ),
            (
                "/timers",
                json!({"freeze_duration": 0.5}),
                "timers.freeze_duration is not a whole number",
            ),
            (
                "/budget_s",
                json!("30"),
                "budget_s is not a number of seconds, 0 or more",
            ),
            (
                "/budget_s",
                json!(-1),
                "budget_s is not a number of seconds, 0 or more",
            ),
            ("/start", json!(1.5), "start is not a whole number"),
            ("/start", json!(-1), "start is not a whole number"),
            (
                "/start",
                json!(18_446_744_074u64),
                "start is past the time the clock counts to",
            ),
            (
                "/start",
                json!(3599),
                "start is within an hour of the Unix epoch, and a key package's lifetime opens an hour before the time of day",
            ),
            ("/seed", json!(-1), "seed is not a whole number"),
            ("/network", json!(7), "network is not an object"),
            (
                "/network/colour",
                json!(1),
                "network.colour is no field of network",
            ),
            (
                "/network/seed",
                json!("7"),
                "network.seed is not a whole number",
            ),
            (
                "/network/loss",
                json!(1.5),
                "network.loss: a rate is a number from 0 to 1",
            ),
            (
                "/network/welcome_loss",
                json!("0.1"),
                "network.welcome_loss: a rate is a number from 0 to 1",
            ),
            (
                "/network/delay_ms",
                json!(40),
                "network.delay_ms is not an object",
            ),
            (
                "/network/delay_ms",
                json!({"tomo": 40}),
                "network.delay_ms names tomo, who is not among members",
            ),
            (
                "/network/delay_ms",
                json!({"pax": -40}),
                "network.delay_ms.pax is not a whole number",
            ),
            ("/unwaited", json!(["pax"]), "unwaited is not an object"),
            (
                "/unwaited/colour",
                json!(1),
                "unwaited.colour is no field of unwaited",
            ),
            (
                "/unwaited/rosters",
                json!("pax"),
                "unwaited.rosters is not a list",
            ),
            (
                "/unwaited/posts/1",
                json!("tomo"),
                "unwaited.posts[1] names tomo, who is not among members",
            ),
            (
                "/unwaited/posts/0",
                json!(1),
                "unwaited.posts[0] is not a name",
            ),
            ("/steps", json!({}), "steps is not a list"),
            ("/steps/0", json!("seat"), "steps[0] is not an object"),
            (
                "/steps/0/step",
                json!("remove"),
                "steps[0].step is not seat, post, cut, heal, withhold, hold or grow",
            ),
            (
                "/steps/3/step",
                json!(4),
                "steps[3].step is not seat, post, cut, heal, withhold, hold or grow",
            ),
            (
                "/steps/0/colour",
                json!(1),
                "steps[0].colour is no field of a seat step",
            ),
            (
                "/steps/3/seconds",
                json!(1),
                "steps[3].seconds is no field of a heal step",
            ),
            (
                "/steps/1/colour",
                json!(1),
                "steps[1].colour is no field of a post step",
            ),
            (
                "/steps/2/colour",
                json!(1),
                "steps[2].colour is no field of a cut step",
            ),
            (
                "/steps/4/colour",
                json!(1),
                "steps[4].colour is no field of a withhold step",
            ),
            (
                "/steps/5/colour",
                json!(1),
                "steps[5].colour is no field of a hold step",
            ),
            (
                "/steps/0/to",
                json!("3"),
                "steps[0].to is not a whole number",
            ),
            (
                "/steps/0/to",
                json!(4),
                "steps[0].to is above the 3 members",
            ),
            (
                "/steps/0/batch",
                json!(0),
                "steps[0].batch is not a whole number above 0",
            ),
            (
                "/steps/0/batch",
                json!(2.5),
                "steps[0].batch is not a whole number above 0",
            ),
            ("/steps/1/from", json!("pax"), "steps[1].from is not a list"),
            (
                "/steps/1/from/1",
                json!("tomo"),
                "steps[1].from[1] names tomo, who is not among members",
            ),
            (
                "/steps/2/sides",
                json!([["saro"]]),
                "steps[2].sides is not two lists of names",
            ),
            (
                "/steps/2/sides",
                json!("saro"),
                "steps[2].sides is not two lists of names",
            ),
            (
                "/steps/2/sides/1",
                json!("raya"),
                "steps[2].sides[1] is not a list",
            ),
            (
                "/steps/2/sides/0/0",
                json!("tomo"),
                "steps[2].sides[0][0] names tomo, who is not among members",
            ),
            (
                "/steps/4/from",
                json!(["raya"]),
                "steps[4].from is not a name",
            ),
            (
                "/steps/4/from",
                json!("tomo"),
                "steps[4].from names tomo, who is not among members",
            ),
            (
                "/steps/5/seconds",
                json!(0),
                "steps[5].seconds is not a number of seconds above 0",
            ),
            (
                "/steps/5/seconds",
                json!("1"),
                "steps[5].seconds is not a number of seconds above 0",
            ),
        ];
        for (path, value, why) in refused {
            assert_eq!(refusal(with(path, Some(value))), why, "{path}");
        }

        let missing = [
            ("/members", "members is missing"),
            ("/timers", "timers is missing"),
            ("/budget_s", "budget_s is missing"),
            ("/start", "start is missing"),
            ("/steps", "steps is missing"),
            ("/network/seed", "network.seed is missing"),
            ("/network/loss", "network.loss is missing"),
            ("/network/welcome_loss", "network.welcome_loss is missing"),
            ("/network/delay_ms", "network.delay_ms is missing"),
            ("/unwaited/rosters", "unwaited.rosters is missing"),
            ("/unwaited/posts", "unwaited.posts is missing"),
            ("/steps/0/step", "steps[0].step is missing"),
            ("/steps/0/to", "steps[0].to is missing"),
            ("/steps/0/batch", "steps[0].batch is missing"),
            ("/steps/1/from", "steps[1].from is missing"),
            ("/steps/2/sides", "steps[2].sides is missing"),
            ("/steps/4/from", "steps[4].from is missing"),
            ("/steps/5/seconds", "steps[5].seconds is missing"),
        ];
        for (path, why) in missing {
            assert_eq!(refusal(with(path, None)), why, "{path}");
        }

        assert_eq!(refusal(json!(["saro"])), "the plan is not an object");
        let not_json = Plan::read("{\"members\": ").err().expect("a refusal");
        assert!(
            not_json.to_string().starts_with("it is not JSON: "),
            "{not_json}"
        );
    }

    /// The plan with a grow step in place of its steps.
    fn grown(step: Value) -> Value {
        let mut plan = plan();
        plan["steps"] = json!([step]);
        plan
    }

    #[test]
    fn a_grow_step_is_read_as_a_plans_only_step() {
        let step = json!({"step": "grow", "epochs": 50, "adders": 3, "branches": 5});
        let mut bare = grown(step.clone());
        bare.as_object_mut().unwrap().remove("network");

        let read = Plan::read(&bare.to_string()).expect("the plan is read");

        let [grow] = &read.steps[..] else {
            panic!("{} steps", read.steps.len());
        };
        assert!(matches!(
            grow,
            Step::Grow {
                epochs: 50,
                adders: Adders::Drawn(3),
                branches: 5,
                size: None
            }
        ));
        let mut sized = step.clone();
        sized["size"] = json!(6);
        let read = Plan::read(&grown(sized).to_string()).expect("the plan is read");
        assert!(matches!(read.steps[..], [Step::Grow { size: Some(6), .. }]));
        let by = json!({"step": "grow", "epochs": 50, "by": "saro", "branches": 5});
        let read = Plan::read(&grown(by).to_string()).expect("the plan is read");
        assert!(matches!(
            read.steps[..],
            [Step::Grow {
                adders: Adders::By(0),
                ..
            }]
        ));
        let mut two = plan();
        two["steps"] = json!([{"step": "hold", "seconds": 1}, step]);
        assert_eq!(
            refusal(two),
            "steps[1] is a grow step, and a grow step is a plan's only step"
        );
    }

    #[test]
    fn what_is_no_grow_step_is_refused_by_the_name_of_its_field() {
        let step = || json!({"step": "grow", "epochs": 50, "adders": 3, "branches": 5});
        let refused = [
            (
                "epochs",
                json!(0),
                "steps[0].epochs is not a whole number of 1 or more",
            ),
            (
                "epochs",
                json!(2.5),
                "steps[0].epochs is not a whole number of 1 or more",
            ),
            (
                "adders",
                json!(0),
                "steps[0].adders is not a whole number of 1 or more",
            ),
            (
                "adders",
                json!("3"),
                "steps[0].adders is not a whole number of 1 or more",
            ),
            (
                "branches",
                json!(1),
                "steps[0].branches is not a whole number of 2 or more",
            ),
            (
                "by",
                json!("saro"),
                "steps[0] holds adders and by, and a grow step holds one or the other",
            ),
            (
                "size",
                json!(1),
                "steps[0].size is not a whole number of 2 or more",
            ),
            (
                "size",
                json!(5),
                "steps[0].size is below twice adders, and each adder of a group at its size \
                 removes a member that adds none",
            ),
            (
                "colour",
                json!(1),
                "steps[0].colour is no field of a grow step",
            ),
        ];
        for (name, value, why) in refused {
            let mut step = step();
            step[name] = value;
            assert_eq!(refusal(grown(step)), why, "{name}");
        }
        for name in ["epochs", "adders", "branches"] {
            let mut step = step();
            step.as_object_mut().unwrap().remove(name);
            assert_eq!(refusal(grown(step)), format!("steps[0].{name} is missing"));
        }
        let by = |by: Value| json!({"step": "grow", "epochs": 50, "by": by, "branches": 5});
        let refused = [
            (
                json!("raya"),
                "steps[0].by names raya, and a grow step begins with saro alone seated",
            ),
            (
                json!("kes"),
                "steps[0].by names kes, who is not among members",
            ),
            (json!(0), "steps[0].by is not a name"),
        ];
        for (named, why) in refused {
            assert_eq!(refusal(grown(by(named.clone()))), why, "{named}");
        }
    }

    #[test]
    fn a_step_of_the_network_is_refused_in_a_plan_without_one() {
        for (at, kind) in [(2, "cut"), (3, "heal"), (4, "withhold")] {
            let mut plan = plan();
            let fields = plan.as_object_mut().unwrap();
            fields.remove("network");
            let steps = fields["steps"].as_array_mut().unwrap();
            let step = steps.remove(at);
            *steps = vec![json!({"step": "hold", "seconds": 1}), step];

            assert_eq!(
                refusal(plan),
                format!("steps[1] is a {kind} step, and the plan has no network")
            );
        }
    }

    #[test]
    fn timers_the_library_refuses_are_refused_with_its_reason() {
        let library = GroupV2Config::default;
        let refused = [
            (
                json!({"voting_delay": 30000}),
                GroupV2Config {
                    voting_delay: Duration::from_secs(30),
                    ..library()
                },
            ),
            (
                json!({"proposal_expiration": 30000}),
                GroupV2Config {
                    proposal_expiration: Duration::from_secs(30),
                    ..library()
                },
            ),
            (
                json!({"freeze_duration": 0}),
                GroupV2Config {
                    freeze_duration: Duration::ZERO,
                    ..library()
                },
            ),
        ];
        for (timers, set) in refused {
            let why = set.validate().expect_err("the library refuses the set");
            assert_eq!(
                refusal(with("/timers", Some(timers))),
                format!("timers: {why}")
            );
        }
    }
}
