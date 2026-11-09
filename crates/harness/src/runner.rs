//! Runs a plan: clients of the harness form one group and do the plan's steps
//! on a virtual clock, and the run leaves `group.log` and `result.json`.
//!
//! What a member holds is read from its trace records as they are written.
//! It holds a roster by an `epoch` record of that many members, an epoch in
//! the `state` of its `epoch` record, and it has read a post by its `received`
//! record of the post's frame.

mod group_log;
mod plan;

use crate::log::{Member, Record};
use crate::test_client::{Harness, STEP};
use group_log::Records;
use libchat::{ChatError, ConversationId, MockClock, SignerKey};
use network_model::{AppId, Dropped, Kind, Model, Rules, Why, Withhold};
use plan::{Adders, Network, Plan, Step};
use rand::Rng;
use rand::seq::index;
use rand_chacha::ChaCha20Rng;
use rand_chacha::rand_core::SeedableRng;
use serde_json::{Value, json};
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;
use std::fs::{self, File};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, Instant};

/// Why nothing was run: the plan could not be read, or the directory not
/// written.
#[derive(Debug)]
pub struct NotRun(String);

impl NotRun {
    fn at(path: &Path, why: impl fmt::Display) -> Self {
        Self(format!("{}: {why}", path.display()))
    }
}

impl fmt::Display for NotRun {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for NotRun {}

/// Runs the plan in the file `plan` and writes `group.log` and `result.json`
/// in the directory `out`. `host` is handed the plan's seed and the run's
/// clock before any member is made. Steps that stop are a run all the same,
/// and its result says why they did.
pub fn run(plan: &Path, out: &Path, host: impl FnOnce(u64, &MockClock)) -> Result<(), NotRun> {
    let text = fs::read_to_string(plan).map_err(|e| NotRun::at(plan, e))?;
    let plan = Plan::read(&text).map_err(|e| NotRun::at(plan, e))?;
    fs::create_dir_all(out).map_err(|e| NotRun::at(out, e))?;
    let (log, result) = (out.join("group.log"), out.join("result.json"));
    let file = File::create(&log).map_err(|e| NotRun::at(&log, e))?;
    // The result is opened with the log: one that cannot be written is found
    // before the steps, and none of an earlier run is left beside this log.
    let mut into = File::create(&result).map_err(|e| NotRun::at(&result, e))?;

    let began = Instant::now();
    let clock = MockClock::new();
    clock.advance(plan.start);
    host(plan.seed, &clock);
    let (subscriber, records) = group_log::on(file, clock.clone());
    let came_to = tracing::subscriber::with_default(subscriber, || {
        let mut run = Run::new(&plan, clock, records);
        let (steps_done, stopped) = run.steps();
        run.result(steps_done, stopped, began)
    });
    let whole = format!("{came_to:#}\n");
    into.write_all(whole.as_bytes())
        .map_err(|e| NotRun::at(&result, e))
}

/// Why the steps stopped, in one line.
struct Stopped(String);

/// An `epoch` record: when it was written, and what its installation then
/// held.
struct Epoch {
    at: Duration,
    epoch: u64,
    state: String,
    members: usize,
}

impl Epoch {
    fn of(at: Duration, record: &Record) -> Option<Self> {
        Some(Self {
            at,
            epoch: record.fields.get("epoch")?.parse().ok()?,
            state: record.fields.get("state")?.clone(),
            members: record.fields.get("members")?.parse().ok()?,
        })
    }
}

/// A state a member held: its epoch, and the state's id.
type Node = (u64, String);

/// The states the members held, each after the one its members held before
/// it, and the lines a grow step puts them on.
#[derive(Default)]
struct Lineage {
    parent: HashMap<Node, Node>,
    children: HashMap<Node, BTreeSet<Node>>,
    /// The trunk first, then each branch in the order it forked.
    lines: Vec<Line>,
    line_of: HashMap<Node, usize>,
}

/// A line of states: the trunk, or a branch that forked off a line.
struct Line {
    /// The line it forked off, and the epoch of its first state.
    from: Option<(usize, u64)>,
    /// 0 for the trunk, 1 for a main branch, more for a sub-branch.
    depth: usize,
}

impl Lineage {
    fn link(&mut self, from: Node, to: Node) {
        self.parent
            .entry(to.clone())
            .or_insert_with(|| from.clone());
        self.children.entry(from).or_default().insert(to);
    }

    /// Whether a member has held a state after `node`: one that holds
    /// `node` is behind it.
    fn followed(&self, node: &Node) -> bool {
        self.children
            .get(node)
            .is_some_and(|after| !after.is_empty())
    }

    /// Whether `node` is `top` or a state after it.
    fn under(&self, node: &Node, top: &Node) -> bool {
        let mut at = node;
        while at.0 > top.0 {
            match self.parent.get(at) {
                Some(parent) => at = parent,
                None => return false,
            }
        }
        at == top
    }

    /// Puts each state with a line under it on a line, from `root` on the
    /// trunk. Where a state has states after it on no line yet, the one
    /// `followed` is under carries its line on, unless one already does, or
    /// else the one more of the `newest` states are under, and each other
    /// starts a branch off it. A line once given is kept.
    fn place(&mut self, root: &Node, newest: &[Node], followed: Option<&Node>) {
        if self.lines.is_empty() {
            self.lines.push(Line {
                from: None,
                depth: 0,
            });
            self.line_of.insert(root.clone(), 0);
        }
        let mut unplaced: Vec<Node> = self.parent.keys().cloned().collect();
        unplaced.retain(|node| !self.line_of.contains_key(node));
        // By epoch, so that a state's parent is placed before it is.
        unplaced.sort();
        for node in unplaced {
            if self.line_of.contains_key(&node) {
                continue;
            }
            let Some(parent) = self.parent.get(&node) else {
                continue;
            };
            let Some(&line) = self.line_of.get(parent) else {
                continue;
            };
            let after = &self.children[parent];
            let carried = after
                .iter()
                .any(|side| self.line_of.get(side) == Some(&line));
            let mut sides: Vec<Node> = after.iter().cloned().collect();
            sides.retain(|side| !self.line_of.contains_key(side));
            // A tie goes to the state the fork report ranks first.
            sides.sort_by_cached_key(|side| {
                let follows = followed.is_some_and(|held| self.under(held, side));
                let on = newest.iter().filter(|held| self.under(held, side)).count();
                (!follows, Reverse(on), side.1.clone())
            });
            for (at, side) in sides.into_iter().enumerate() {
                let on = if at == 0 && !carried {
                    line
                } else {
                    self.lines.push(Line {
                        from: Some((line, side.0)),
                        depth: self.lines[line].depth + 1,
                    });
                    self.lines.len() - 1
                };
                self.line_of.insert(side, on);
            }
        }
    }

    /// The trunk and the main branches.
    fn main_lines(&self) -> usize {
        self.lines.iter().filter(|line| line.depth <= 1).count()
    }

    /// The last epoch a line holds.
    fn epoch_of(&self, line: usize) -> u64 {
        let on = self.line_of.iter().filter(|(_, on)| **on == line);
        on.map(|(node, _)| node.0).max().unwrap_or(0)
    }
}

/// What a grow step came to.
#[derive(Default)]
struct Grown {
    ended: String,
    rounds: Vec<Value>,
    /// Each time a member came out of a round behind its branch, having
    /// gone into it level or before it held the group: its name and the
    /// epoch it held.
    fell_behind: Vec<Value>,
    /// The adders refused a whole round, by the state each then held. None
    /// is picked again until it holds another.
    stuck: HashMap<usize, Node>,
}

/// What a member's records say of it.
#[derive(Default)]
struct Told {
    /// Its `epoch` records, in the order written.
    epochs: Vec<Epoch>,
    /// The frame of each post it sent, and when.
    sent: Vec<(String, Duration)>,
    /// When it read each post, by the post's frame.
    read: HashMap<String, Duration>,
}

/// A plan being run. A member is its place in the plan's `members`.
struct Run<'a> {
    plan: &'a Plan,
    clock: MockClock,
    harness: Harness,
    records: Records,
    /// The members as the records name them.
    members: Vec<Member>,
    by_installation: HashMap<String, usize>,
    told: Vec<Told>,
    convo: ConversationId,
    /// The conversation's id as the records write it.
    conversation: Option<String>,
    /// How many members the steps have seated, the creator among them.
    seated: usize,
    /// The member whose rule is armed for the next batch.
    armed: Option<usize>,
    /// The members a withheld candidate left in a state of their own.
    apart: Vec<usize>,
    batches: Vec<Value>,
    posts: Vec<Value>,
    lineage: Lineage,
    /// The member a grow step's adders are `by`, whose line is the trunk.
    adder: Option<usize>,
    /// The members a grow step removed, in the order it removed them.
    removed: Vec<usize>,
    /// What the result says of each member removed, taken as its client was
    /// retired.
    retired: HashMap<usize, Value>,
    grown: Option<Grown>,
}

impl<'a> Run<'a> {
    /// The plan's members on `clock`, the first of them alone in a group it
    /// has created.
    fn new(plan: &'a Plan, clock: MockClock, records: Records) -> Self {
        let names = plan.members.clone();
        let mut harness = Harness::new(names, plan.timers.clone(), clock.clone(), |_, _| {});
        // A member on the far side of a split rejects what the other side
        // sends, and the run has to reach its end all the same.
        harness.tolerate_inbound_errors();
        if let Some(network) = &plan.network {
            for (member, name) in plan.members.iter().enumerate() {
                let mut model = Model::new(network.seed, name);
                model.rules_mut().set_loss(network.loss);
                model.rules_mut().set_welcome_loss(network.welcome_loss);
                model.set_delay(Duration::from_millis(network.delay_ms[member]));
                harness.client_mut(member).set_model(model);
            }
        }
        let members: Vec<Member> = (0..plan.members.len())
            .map(|member| {
                let client = harness.client(member);
                Member::new(client.signer_key(), client.account())
            })
            .collect();
        let convo = harness
            .client_mut(0)
            .create_group_convo_v2(&[], "sim", "")
            .expect("a client creates a group of its own");

        let by_installation = members.iter().enumerate();
        let mut run = Self {
            plan,
            clock,
            harness,
            records,
            by_installation: by_installation
                .map(|(member, named)| (named.signer.clone(), member))
                .collect(),
            told: members.iter().map(|_| Told::default()).collect(),
            members,
            convo,
            conversation: None,
            seated: 1,
            armed: None,
            apart: vec![],
            batches: vec![],
            posts: vec![],
            lineage: Lineage::default(),
            adder: None,
            removed: vec![],
            retired: HashMap::new(),
            grown: None,
        };
        run.read();
        run
    }

    /// Does the plan's steps in order. Returns how many were done to the end
    /// and, when one stopped them, why.
    fn steps(&mut self) -> (usize, Option<String>) {
        let plan = self.plan;
        for (done, step) in plan.steps.iter().enumerate() {
            let stepped = match step {
                Step::Seat { to, batch } => self.seat(*to, *batch),
                Step::Post { from } => from.iter().try_for_each(|poster| self.post(*poster)),
                Step::Cut { sides } => self.cut(sides),
                Step::Heal => {
                    (0..plan.members.len()).for_each(|member| self.rules(member).heal());
                    Ok(())
                }
                Step::Withhold { from } => self.withhold(*from),
                Step::Hold { time } => {
                    let mut left = *time;
                    while !left.is_zero() {
                        self.step(&mut left);
                    }
                    Ok(())
                }
                Step::Grow {
                    epochs,
                    adders,
                    branches,
                    size,
                } => {
                    self.grow(*epochs, adders, *branches, *size);
                    Ok(())
                }
            };
            if let Err(Stopped(why)) = stepped {
                return (done, Some(why));
            }
        }
        (plan.steps.len(), None)
    }

    fn name(&self, member: usize) -> &'a str {
        &self.plan.members[member]
    }

    /// Takes in the records written since the last call.
    fn read(&mut self) {
        for (at, record) in self.records.take() {
            let field = |name: &str| record.fields.get(name);
            let member = field("signer").and_then(|id| self.by_installation.get(id));
            let Some(&member) = member else {
                continue;
            };
            let told = &mut self.told[member];
            match (record.name.as_str(), field("frame")) {
                ("epoch", _) => {
                    if let Some(held) = Epoch::of(at, &record) {
                        let before = told
                            .epochs
                            .last()
                            .filter(|before| before.epoch < held.epoch);
                        if let Some(before) = before {
                            let from = (before.epoch, before.state.clone());
                            self.lineage.link(from, (held.epoch, held.state.clone()));
                        }
                        told.epochs.push(held);
                    }
                    if self.conversation.is_none() {
                        self.conversation = field("convo").cloned();
                    }
                }
                ("sent", Some(frame)) => told.sent.push((frame.clone(), at)),
                ("received", Some(frame)) => {
                    told.read.entry(frame.clone()).or_insert(at);
                }
                _ => {}
            }
        }
    }

    /// The record by which `member` first held a roster of `members`.
    fn reached(&self, member: usize, members: usize) -> Option<&Epoch> {
        let mut epochs = self.told[member].epochs.iter();
        epochs.find(|held| held.members == members)
    }

    /// The state `member` holds `epoch` in.
    fn state_at(&self, member: usize, epoch: u64) -> Option<&str> {
        let mut epochs = self.told[member].epochs.iter();
        let held = epochs.find(|held| held.epoch == epoch)?;
        Some(&held.state)
    }

    /// Hands every client what is on the wire, moves the clock by `time` and
    /// fires the wakeups that are due.
    fn advance(&mut self, time: Duration) {
        self.harness.process(time);
        self.read();
    }

    /// Advances one step, or by `left` when that is less, and takes it off
    /// `left`.
    fn step(&mut self, left: &mut Duration) {
        let step = STEP.min(*left);
        self.advance(step);
        *left -= step;
    }

    /// Does `op` again after each step for as long as it is refused and
    /// `left` lasts. The last refusal, when it runs out.
    fn tried(
        &mut self,
        left: &mut Duration,
        op: impl Fn(&mut Harness) -> Result<(), ChatError>,
    ) -> Result<(), String> {
        loop {
            match op(&mut self.harness) {
                Ok(()) => {
                    self.read();
                    return Ok(());
                }
                Err(refusal) if left.is_zero() => return Err(refusal.to_string()),
                Err(_) => self.step(left),
            }
        }
    }

    /// Steps until `done` or until `left` runs out. The harness hands
    /// payloads over before it moves the clock, so what the last step's
    /// wakeups published is still on the wire then: it is handed over, and
    /// `done` is asked once more.
    fn wait(&mut self, left: &mut Duration, done: impl Fn(&mut Self) -> bool) -> bool {
        while !done(self) {
            if left.is_zero() {
                self.advance(Duration::ZERO);
                return done(self);
            }
            self.step(left);
        }
        true
    }

    /// Seats the members up to `to`, `batch` of them at a time.
    fn seat(&mut self, to: usize, batch: usize) -> Result<(), Stopped> {
        while self.seated < to {
            self.add(self.seated.saturating_add(batch).min(to))?;
        }
        Ok(())
    }

    /// The creator adds the next members, up to a group of `members`, and
    /// the members waited on come to hold that roster.
    fn add(&mut self, members: usize) -> Result<(), Stopped> {
        let (began, sent_at) = (Instant::now(), self.clock.now());
        let mut left = self.plan.budget;
        let joiners: Vec<SignerKey> = (self.seated..members)
            .map(|joiner| self.harness.client(joiner).signer_key())
            .collect();
        let convo = self.convo.clone();
        self.tried(&mut left, |harness| {
            harness.client_mut(0).group_add_signers(&convo, &joiners)
        })
        .map_err(|refusal| {
            let creator = self.name(0);
            Stopped(format!(
                "{creator}: the add was refused until the budget ran out: {refusal}"
            ))
        })?;
        // The rule withholds the first candidate to arrive once it is armed,
        // so one it has withheld by now was of an earlier batch.
        let withheld = self.armed.take();
        let early = withheld.is_some_and(|member| self.withheld_from(member));

        let unwaited = &self.plan.unwaited.rosters;
        let waited: Vec<usize> = (0..members)
            .filter(|member| !unwaited.contains(member) && !self.apart.contains(member))
            .collect();
        let held = self.wait(&mut left, |run| {
            let mut waited = waited.iter();
            waited.all(|member| run.reached(*member, members).is_some())
        });

        // A member that does not hold the roster may hold its epoch all the
        // same, by a commit the others did not take.
        let mut holders = std::iter::once(0).chain(waited.iter().copied());
        let epoch = holders.find_map(|member| Some(self.reached(member, members)?.epoch));
        if let Some(epoch) = epoch {
            let states = waited.iter().map(|member| self.state_at(*member, epoch));
            let states: Vec<Option<&str>> = states.collect();
            let distinct: BTreeSet<&str> = states.iter().flatten().copied().collect();
            if withheld.is_some_and(|member| alone(member, &waited, &states)) {
                self.apart.extend(withheld);
            } else if distinct.len() > 1 {
                return Err(Stopped(format!(
                    "the group split: {} of its {members} members hold epoch {epoch}, in {} states",
                    states.iter().flatten().count(),
                    distinct.len()
                )));
            }
        }
        if !held {
            let behind = waited
                .iter()
                .filter(|member| self.reached(**member, members).is_none());
            let behind: Vec<&str> = behind.map(|member| self.name(*member)).collect();
            return Err(Stopped(format!(
                "the roster of {members} members was not held within the budget by: {}",
                behind.join(", ")
            )));
        }
        if let Some(member) = withheld {
            let name = self.name(member);
            if early {
                return Err(Stopped(format!(
                    "{name}: a candidate was withheld before the add was accepted"
                )));
            }
            if !self.wait(&mut left, |run| run.withheld_from(member)) {
                return Err(Stopped(format!(
                    "{name}: no candidate was withheld within the budget"
                )));
            }
        }

        let reached = waited
            .iter()
            .filter_map(|member| self.reached(*member, members));
        let seated_at = reached.map(|held| held.at).max().unwrap_or(sent_at);
        self.batches.push(json!({
            "joiners": members - self.seated,
            "members": members,
            "seated_s": seated_at.saturating_sub(sent_at).as_secs_f64(),
            "wall_s": began.elapsed().as_secs_f64(),
        }));
        self.seated = members;
        Ok(())
    }

    /// Whether the model of `member` has withheld a candidate from it.
    fn withheld_from(&mut self, member: usize) -> bool {
        let mut dropped = self.harness.client(member).dropped().iter();
        dropped.any(|dropped| dropped.why == Why::Withheld)
    }

    /// `poster` posts once, and the members waited on come to read the post.
    fn post(&mut self, poster: usize) -> Result<(), Stopped> {
        let name = self.name(poster);
        let mut left = self.plan.budget;
        let (convo, text) = (self.convo.clone(), format!("post {}", self.posts.len() + 1));
        let posted = self.told[poster].sent.len();
        self.tried(&mut left, |harness| {
            let sent = harness
                .client_mut(poster)
                .send_content(&convo, text.as_bytes());
            sent.map(drop)
        })
        .map_err(|refusal| {
            Stopped(format!(
                "{name}: the post was refused until the budget ran out: {refusal}"
            ))
        })?;
        let Some((frame, sent_at)) = self.told[poster].sent.get(posted).cloned() else {
            return Err(Stopped(format!("{name}: the post left no record of it")));
        };

        let unwaited = &self.plan.unwaited.posts;
        let readers: Vec<usize> = (0..self.seated)
            .filter(|member| *member != poster)
            .filter(|member| !unwaited.contains(member) && !self.apart.contains(member))
            .collect();
        self.wait(&mut left, |run| {
            let mut readers = readers.iter();
            readers.all(|reader| run.told[*reader].read.contains_key(&frame))
        });

        let read_at = |reader: &usize| self.told[*reader].read.get(&frame);
        let unread = readers.iter().filter(|reader| read_at(reader).is_none());
        let unread: Vec<&str> = unread.map(|reader| self.name(*reader)).collect();
        let read_s = readers.iter().filter_map(read_at);
        let read_s: Vec<f64> = read_s
            .map(|at| at.saturating_sub(sent_at).as_secs_f64())
            .collect();
        self.posts.push(json!({
            "poster": name,
            "readers": readers.len(),
            "unread": unread,
            "read_s": read_s,
        }));
        if !unread.is_empty() {
            return Err(Stopped(format!(
                "the post of {name} was not read within the budget by: {}",
                unread.join(", ")
            )));
        }
        Ok(())
    }

    /// The app id `member` stamps its frames of the conversation with, once
    /// it has published one. A member of a run publishes in that conversation
    /// alone, a joiner first as it joins.
    fn app_id(&mut self, member: usize) -> Option<AppId> {
        let app_ids = self.harness.client(member).app_ids();
        assert!(
            app_ids.len() < 2,
            "{app_ids:?}: a member publishes in one conversation"
        );
        app_ids.into_iter().next()
    }

    /// The rules of the model of `member`. A plan has a step of the network
    /// only with a network.
    fn rules(&mut self, member: usize) -> &mut Rules {
        let model = self.harness.client_mut(member).model_mut();
        model.expect("a model for each member").rules_mut()
    }

    /// Cuts each side off from the frames of the other, once the members it
    /// names hold the conversation. One the steps have not seated stops them
    /// at once.
    fn cut(&mut self, sides: &[Vec<usize>; 2]) -> Result<(), Stopped> {
        let named: Vec<usize> = sides.iter().flatten().copied().collect();
        if let Some(unseated) = named.iter().find(|member| **member >= self.seated) {
            return Err(self.not_held(*unseated));
        }
        // A member no roster was waited on for may have yet to take its
        // welcome.
        let mut left = self.plan.budget;
        self.wait(&mut left, |run| {
            let mut named = named.iter();
            named.all(|member| run.app_id(*member).is_some())
        });
        let mut app_ids = [vec![], vec![]];
        for (side, app_ids) in sides.iter().zip(&mut app_ids) {
            for member in side {
                let app_id = self.app_id(*member).ok_or_else(|| self.not_held(*member))?;
                app_ids.push(app_id);
            }
        }
        for (side, others) in sides.iter().zip(app_ids.iter().rev()) {
            for member in side {
                self.rules(*member).set_cut(others.iter().cloned());
            }
        }
        Ok(())
    }

    /// Why a cut that names `member` stops the steps.
    fn not_held(&self, member: usize) -> Stopped {
        let name = self.name(member);
        Stopped(format!(
            "{name}: a cut names it, and it does not hold the conversation"
        ))
    }

    /// Arms the rule that withholds the next commit candidate from `from`.
    fn withhold(&mut self, from: usize) -> Result<(), Stopped> {
        // A rule once armed cannot be disarmed, and a batch waits for the
        // candidate of one member.
        if let Some(other) = self.armed.filter(|armed| *armed != from) {
            let (name, other) = (self.name(from), self.name(other));
            return Err(Stopped(format!(
                "{name}: no candidate is withheld: the next batch withholds one from {other}"
            )));
        }
        let armed = self.rules(from).withhold_next_candidate();
        armed.map_err(|refused| {
            let name = self.name(from);
            Stopped(format!("{name}: no candidate is withheld: {refused}"))
        })?;
        self.armed = Some(from);
        Ok(())
    }

    /// Members taking part add new ones in rounds, until the trunk holds
    /// `epochs`, `branches` main branches run side by side, every member of
    /// the plan is seated or none takes part. Each round the adders, a draw
    /// from the plan's seed of members on any branch or the one they are by,
    /// each add the next member at once.
    fn grow(&mut self, epochs: u64, adders: &Adders, branches: usize, size: Option<usize>) {
        // A stream of its own, apart from the one the host draws from the seed.
        let mut draw = ChaCha20Rng::seed_from_u64(self.plan.seed);
        draw.set_stream(1);
        if let Adders::By(adder) = adders {
            self.adder = Some(*adder);
        }
        let mut grown = Grown::default();
        grown.ended = loop {
            self.place();
            if self.lineage.epoch_of(0) >= epochs {
                break format!("the trunk reached epoch {epochs}");
            }
            let main = self.lineage.main_lines();
            if main >= branches {
                break format!("{main} main branches ran side by side");
            }
            let unseated = self.plan.members.len() - self.seated;
            if unseated == 0 {
                break "every member of the plan was seated".into();
            }
            let mut taking_part = self.level();
            taking_part.retain(|member| grown.stuck.get(member) != self.newest(*member).as_ref());
            if let Adders::By(adder) = adders {
                taking_part.retain(|member| member == adder);
            }
            if taking_part.is_empty() {
                break "no member took part".into();
            }
            let picked = match adders {
                Adders::Drawn(most) => {
                    let count = draw.random_range(1..=(*most).min(taking_part.len()).min(unseated));
                    let picked = index::sample(&mut draw, taking_part.len(), count);
                    picked.into_iter().map(|at| taking_part[at]).collect()
                }
                Adders::By(_) => taking_part,
            };
            let round = self.round(&picked, &mut grown, size);
            grown.rounds.push(round);
        };
        self.grown = Some(grown);
    }

    /// Each of `adders` adds the next member, tried again after each step
    /// while it is refused. The round ends when each member added holds the
    /// group and no member level with its branch at the start is behind it,
    /// or when the budget runs out. A member behind already is waited on no
    /// more until it holds a state no member has gone past, and a member
    /// whose Welcome comes after its round is never waited on.
    ///
    /// Where the adds would take the group above `size`, an adder first
    /// removes the member seated longest ago that neither adds this round
    /// nor is the adder a grow step's adders are `by`. Once the round is
    /// over, the client of each member removed is retired.
    fn round(&mut self, adders: &[usize], grown: &mut Grown, size: Option<usize>) -> Value {
        let (began, mut left) = (self.clock.now(), self.plan.budget);
        let convo = self.convo.clone();
        let mut waited = self.level();
        let mut already = self.joined();
        already.retain(|member| self.behind(*member));
        let present = self.seated - self.removed.len();
        let over = size.map_or(0, |size| (present + adders.len()).saturating_sub(size));
        let leaving = (0..self.seated).filter(|member| {
            !self.removed.contains(member)
                && !adders.contains(member)
                && self.adder != Some(*member)
        });
        let mut removing: HashMap<usize, usize> =
            adders.iter().copied().zip(leaving.take(over)).collect();
        let removed_before = self.removed.len();
        let (mut joiners, mut refused) = (vec![], adders.to_vec());
        loop {
            refused.retain(|adder| {
                if let Some(&leaving) = removing.get(adder) {
                    let key = self.harness.client(leaving).signer_key();
                    let removed = self
                        .harness
                        .client_mut(*adder)
                        .group_remove_signers(&convo, &[&key]);
                    if removed.is_err() {
                        return true;
                    }
                    removing.remove(adder);
                    self.removed.push(leaving);
                }
                let joiner = self.harness.client(self.seated).signer_key();
                let added = self
                    .harness
                    .client_mut(*adder)
                    .group_add_signers(&convo, &[joiner]);
                if added.is_ok() {
                    joiners.push(self.seated);
                    self.seated += 1;
                }
                added.is_err()
            });
            self.read();
            if refused.is_empty() || left.is_zero() {
                break;
            }
            self.step(&mut left);
        }
        waited.extend(&joiners);
        waited.retain(|member| !self.removed.contains(member));
        self.wait(&mut left, |run| run.settled(&waited));
        // A client keeps all it held of the group it was removed from.
        for at in removed_before..self.removed.len() {
            let member = self.removed[at];
            let said = self.member(member);
            self.retired.insert(member, said);
            self.harness.retire(member);
        }

        let mut fell = self.joined();
        fell.retain(|member| self.behind(*member) && !already.contains(member));
        for member in fell {
            let epoch = self.newest(member).map(|(epoch, _)| epoch);
            let name = self.name(member);
            grown
                .fell_behind
                .push(json!({"name": name, "epoch": epoch}));
        }
        for adder in &refused {
            grown
                .stuck
                .extend(self.newest(*adder).map(|held| (*adder, held)));
        }
        let names = |members: &[usize]| -> Vec<&str> {
            members.iter().map(|member| self.name(*member)).collect()
        };
        json!({
            "adders": names(adders),
            "joiners": names(&joiners),
            "refused": names(&refused),
            "settled_s": self.clock.now().saturating_sub(began).as_secs_f64(),
        })
    }

    /// The members seated and not removed that hold the group.
    fn joined(&self) -> Vec<usize> {
        let joined = 0..self.seated;
        joined
            .filter(|member| !self.told[*member].epochs.is_empty())
            .filter(|member| !self.removed.contains(member))
            .collect()
    }

    /// The members that hold the group in a state no member has gone past.
    fn level(&self) -> Vec<usize> {
        let mut level = self.joined();
        level.retain(|member| !self.behind(*member));
        level
    }

    /// The state `member` holds now.
    fn newest(&self, member: usize) -> Option<Node> {
        let held = self.told[member].epochs.last()?;
        Some((held.epoch, held.state.clone()))
    }

    /// Whether a member has held a state after the one `member` holds.
    fn behind(&self, member: usize) -> bool {
        let newest = self.newest(member);
        newest.is_some_and(|newest| self.lineage.followed(&newest))
    }

    /// Whether each of `waited` holds the group and none is behind.
    fn settled(&self, waited: &[usize]) -> bool {
        let level = |member: &usize| !self.told[*member].epochs.is_empty() && !self.behind(*member);
        waited.iter().all(level)
    }

    /// Puts the states the members held on lines, from the creator's first
    /// on the trunk, which follows the adder a grow step's adders are by.
    fn place(&mut self) {
        let Some(root) = self.told[0].epochs.first() else {
            return;
        };
        let root = (root.epoch, root.state.clone());
        let newest = self
            .joined()
            .into_iter()
            .filter_map(|member| self.newest(member));
        let newest: Vec<Node> = newest.collect();
        let followed = self.adder.and_then(|adder| self.newest(adder));
        self.lineage.place(&root, &newest, followed.as_ref());
    }

    /// What a grow step came to: each line and the members on it, who fell
    /// behind, and who is behind or never joined at the end.
    fn grown(&self) -> Option<Value> {
        let grown = self.grown.as_ref()?;
        let lineage = &self.lineage;
        let joined = self.joined();
        let line_of = |member: &usize| {
            let newest = self.newest(*member)?;
            lineage.line_of.get(&newest).copied()
        };
        let lines = lineage.lines.iter().enumerate().map(|(at, line)| {
            let on = joined.iter().filter(|member| line_of(member) == Some(at));
            json!({
                "kind": match line.depth {
                    0 => "trunk",
                    1 => "main",
                    _ => "sub",
                },
                "from": line.from.map(|(from, _)| from),
                "forked_at": line.from.map(|(_, epoch)| epoch),
                "epoch": lineage.epoch_of(at),
                "members": on.map(|member| self.name(*member)).collect::<Vec<_>>(),
            })
        });
        let names = |members: Vec<usize>| -> Vec<&str> {
            members
                .into_iter()
                .map(|member| self.name(member))
                .collect()
        };
        let behind = joined.iter().copied().filter(|member| self.behind(*member));
        let never_joined = (0..self.seated).filter(|member| {
            self.told[*member].epochs.is_empty() && !self.removed.contains(member)
        });
        Some(json!({
            "ended": grown.ended,
            "trunk_epoch": lineage.epoch_of(0),
            "main": lineage.main_lines(),
            "lines": lines.collect::<Vec<_>>(),
            "fell_behind": grown.fell_behind,
            "behind": names(behind.collect()),
            "never_joined": names(never_joined.collect()),
            "removed": names(self.removed.clone()),
            "rounds": grown.rounds,
        }))
    }

    /// Steps until no model holds a frame, for the largest delay of the plan
    /// at most and no longer than the budget. A frame its delay holds when
    /// the steps end then reaches its member, and the log says what became
    /// of it.
    fn rest(&mut self) {
        let delays = self
            .plan
            .network
            .iter()
            .flat_map(|network| &network.delay_ms);
        let delay = Duration::from_millis(delays.copied().max().unwrap_or(0));
        let mut left = delay.min(self.plan.budget);
        while !left.is_zero() {
            self.advance(Duration::ZERO);
            if !self.holds_a_frame() {
                return;
            }
            self.step(&mut left);
        }
    }

    fn holds_a_frame(&mut self) -> bool {
        let members = 0..self.plan.members.len();
        let retired = &self.retired;
        members
            .filter(|member| !retired.contains_key(member))
            .any(|member| {
                let model = self.harness.client(member).model();
                model.is_some_and(|model| model.next_due().is_some())
            })
    }

    /// What the run came to. `began` is when it began, by the wall clock.
    fn result(&mut self, steps_done: usize, stopped: Option<String>, began: Instant) -> Value {
        self.rest();
        // What the last step published is handed over, so the log and the
        // result hold all that the run made.
        self.advance(Duration::ZERO);
        let mut members = vec![];
        for member in 0..self.plan.members.len() {
            let retired = self.retired.remove(&member);
            members.push(retired.unwrap_or_else(|| self.member(member)));
        }
        if self.grown.is_some() {
            self.place();
        }
        let mut result = json!({
            "steps_done": steps_done,
            "stopped": stopped,
            "conversation": self.conversation,
            "members": members,
            "batches": self.batches,
            "posts": self.posts,
            "virtual_s": self.clock.now().saturating_sub(self.plan.start).as_secs_f64(),
            "wall_s": began.elapsed().as_secs_f64(),
        });
        if let Some(grown) = self.grown() {
            result["grow"] = grown;
        }
        result
    }

    /// What the result says of `member`.
    fn member(&mut self, member: usize) -> Value {
        let app_id = self.app_id(member).map(|app_id| app_id.to_string());
        let client = self.harness.client(member);
        let mut inbound_errors: BTreeMap<&str, u64> = BTreeMap::new();
        for message in client.inbound_errors() {
            *inbound_errors.entry(message).or_default() += 1;
        }
        let mut said = json!({
            "name": self.plan.members[member],
            "installation": self.members[member].signer,
            "account": self.members[member].participant,
            "seated": member < self.seated,
            "app_id": app_id,
            "inbound_errors": inbound_errors,
        });
        if let (Some(network), Some(model)) = (&self.plan.network, client.model()) {
            said["network"] = network_state(network, network.delay_ms[member], model);
            said["dropped"] = client.dropped().iter().map(drop_of).collect();
        }
        said
    }
}

/// Whether `member` holds an epoch in a state of its own and the others that
/// hold it in one: what a candidate withheld from `member` leaves when the
/// group applies it. `held` is the state of each of `members`.
fn alone(member: usize, members: &[usize], held: &[Option<&str>]) -> bool {
    let (mut own, mut others) = (None, BTreeSet::new());
    for (other, state) in members.iter().zip(held) {
        if *other == member {
            own = *state;
        } else {
            others.extend(*state);
        }
    }
    own.is_some_and(|own| others.len() == 1 && !others.contains(own))
}

/// The state of a member's model: the rules it was started with and holds
/// now, and what became of the frames that arrived.
fn network_state(network: &Network, delay_ms: u64, model: &Model) -> Value {
    let (rules, counts) = (model.rules(), model.counts());
    let hex = |ids: &BTreeSet<AppId>| ids.iter().map(AppId::to_string).collect::<Vec<_>>();
    let withheld = match rules.withhold() {
        Withhold::Withheld {
            sender,
            seat,
            applied,
        } => json!({
            "app_id": sender.as_ref().map(AppId::to_string),
            "seat": seat,
            "applied": applied,
        }),
        Withhold::Off | Withhold::Armed => Value::Null,
    };
    json!({
        "seed": network.seed,
        "loss": f64::from(network.loss),
        "welcome_loss": f64::from(network.welcome_loss),
        "delay_ms": delay_ms,
        "cut": hex(rules.cut()),
        "own": hex(rules.own()),
        "withheld": withheld,
        "armed": *rules.withhold() == Withhold::Armed,
        "frames": {
            "arrived": counts.arrived,
            "passed": counts.passed,
            "held": counts.held,
            "dropped": {
                "loss": counts.loss,
                "welcome_loss": counts.welcome_loss,
                "cut": counts.cut,
                "withheld": counts.withheld,
            },
        },
    })
}

/// A frame a model dropped.
fn drop_of(dropped: &Dropped) -> Value {
    let mut drop = json!({
        "n": dropped.n,
        "why": match dropped.why {
            Why::Loss => "loss",
            Why::WelcomeLoss => "welcome_loss",
            Why::Cut => "cut",
            Why::Withheld => "withheld",
        },
        "kind": match dropped.frame.kind {
            Kind::Welcome => "welcome",
            Kind::Candidate { .. } => "candidate",
            Kind::Group => "group",
            Kind::Other => "other",
        },
        "bytes": dropped.bytes,
    });
    if let Some(sender) = &dropped.frame.sender {
        drop["app_id"] = json!(sender.to_string());
    }
    if let Kind::Candidate { seat: Some(seat) } = dropped.frame.kind {
        drop["seat"] = json!(seat);
    }
    drop
}

#[cfg(test)]
mod tests {
    use super::*;
    use network_model::{Rate, read};

    /// What a run left.
    struct Left {
        result: Value,
        log: String,
    }

    impl Left {
        /// The trace records of the log, in the order written.
        fn records(&self) -> Vec<Record> {
            self.log.lines().filter_map(Record::read).collect()
        }

        fn member(&self, name: &str) -> &Value {
            let members = self.result["members"].as_array().expect("the members");
            let member = members.iter().find(|member| member["name"] == name);
            member.unwrap_or_else(|| panic!("no member {name}"))
        }

        /// The names of the records the installation of `name` wrote of
        /// `frame`.
        fn written_of(&self, name: &str, frame: &str) -> Vec<String> {
            let installation = self.member(name)["installation"].as_str().expect("an id");
            let records = self.records().into_iter().filter(|record| {
                record.fields["signer"] == installation
                    && record.fields.get("frame").is_some_and(|of| of == frame)
            });
            records.map(|record| record.name).collect()
        }
    }

    /// Runs `plan` in a directory of its own.
    fn run_of(plan: &Value) -> Left {
        let directory = tempfile::tempdir().expect("a directory");
        let (file, out) = (
            directory.path().join("plan.json"),
            directory.path().join("out"),
        );
        fs::write(&file, plan.to_string()).expect("the plan is written");
        run(&file, &out, |_, _| {}).expect("the plan is run");
        let left = |name: &str| fs::read_to_string(out.join(name)).expect(name);
        Left {
            result: serde_json::from_str(&left("result.json")).expect("a result"),
            log: left("group.log"),
        }
    }

    /// A plan of `steps` for `members` under the fast set, with a budget
    /// none of them comes near.
    fn plan(members: &[&str], steps: Value) -> Value {
        json!({"members": members, "timers": "fast", "budget_s": 30, "start": START, "steps": steps})
    }

    /// `plan` with a network that drops and holds nothing.
    fn with_network(mut plan: Value) -> Value {
        plan["network"] = json!({"seed": 1, "loss": 0, "welcome_loss": 0, "delay_ms": {}});
        plan
    }

    const CAST: [&str; 3] = ["saro", "raya", "pax"];
    /// The earliest second a plan may start at.
    const START: u64 = 60 * 60;

    #[test]
    fn three_members_are_seated_and_read_each_post() {
        let left = run_of(&plan(
            &CAST,
            json!([
                {"step": "seat", "to": 3, "batch": 1},
                {"step": "post", "from": ["pax", "saro"]},
            ]),
        ));
        let result = &left.result;

        assert_eq!(result["stopped"], Value::Null);
        assert_eq!(result["steps_done"], 2);
        let batches = result["batches"].as_array().expect("the batches");
        let seated = batches
            .iter()
            .map(|batch| [&batch["joiners"], &batch["members"]]);
        assert_eq!(seated.collect::<Vec<_>>(), [[1, 2], [1, 3]]);
        // Nothing is between a post and its readers: each reads it in the
        // pass that follows the send, before the clock moves.
        let read_at_once = |poster: &str| {
            let read_s = [0.0, 0.0];
            json!({"poster": poster, "readers": 2, "unread": [], "read_s": read_s})
        };
        assert_eq!(
            result["posts"],
            json!([read_at_once("pax"), read_at_once("saro")])
        );

        let records = left.records();
        for (name, account) in [
            ("saro", "7361726f"),
            ("raya", "72617961"),
            ("pax", "706178"),
        ] {
            let member = left.member(name);
            assert_eq!(member["account"], account);
            assert_eq!(member["seated"], true);
            assert_eq!(member["inbound_errors"], json!({}));
            assert!(member["app_id"].is_string(), "{member}");
            assert!(member.get("network").is_none() && member.get("dropped").is_none());
            // The roster it is said to hold is the one its records tell of.
            let holds_the_roster = records.iter().any(|record| {
                record.name == "epoch"
                    && record.fields["signer"] == member["installation"]
                    && record.fields["convo"] == result["conversation"]
                    && record.fields["members"] == "3"
            });
            assert!(holds_the_roster, "{name}");
        }
        let sent = records.iter().filter(|record| record.name == "sent");
        let frames: Vec<&str> = sent.map(|record| record.fields["frame"].as_str()).collect();
        let [from_pax, from_saro] = frames[..] else {
            panic!("{} posts were sent", frames.len());
        };
        assert_eq!(left.written_of("saro", from_pax), ["received"]);
        assert_eq!(left.written_of("raya", from_pax), ["received"]);
        assert_eq!(left.written_of("raya", from_saro), ["received"]);
        assert_eq!(left.written_of("pax", from_saro), ["received"]);
    }

    #[test]
    fn the_timers_of_a_plan_are_the_groups() {
        let seated_s = |timers: Value| -> Vec<f64> {
            let mut plan = plan(&CAST, json!([{"step": "seat", "to": 3, "batch": 1}]));
            plan["timers"] = timers;
            let left = run_of(&plan);
            let batches = left.result["batches"].as_array().expect("the batches");
            let seated = batches.iter().map(|batch| batch["seated_s"].as_f64());
            seated.map(|seated| seated.expect("seconds")).collect()
        };

        // A steward commits a batch once its window has passed.
        let fast = seated_s(json!("fast"));
        assert!(fast.len() == 2 && fast.iter().all(|s| *s < 3.0), "{fast:?}");
        let long_window = seated_s(json!({
            "voting_delay": 50,
            "consensus_timeout": 250,
            "commit_batch_window": 3000,
            "freeze_duration": 500,
            "proposal_expiration": 4000,
        }));
        assert!(
            long_window.len() == 2 && long_window.iter().all(|s| *s >= 3.0),
            "{long_window:?}"
        );
    }

    /// What the rules of `name` under `seed` drop of its first `arrived`
    /// frames, at `rate` whatever a frame is: a rule draws one number for
    /// each arrival.
    fn lost(seed: u64, name: &str, rate: f64, arrived: u64) -> Vec<u64> {
        let rate = Rate::try_from(rate).expect("a rate");
        let mut rules = Rules::new(seed, name);
        rules.set_loss(rate);
        rules.set_welcome_loss(rate);
        let dropped = (0..arrived).filter_map(|_| rules.decide(read(&[]), 0));
        dropped.map(|dropped| dropped.n).collect()
    }

    #[test]
    fn each_member_has_the_model_of_its_name_under_the_plans_network() {
        const RATE: f64 = 0.3;
        // A seed under which Raya's invite, the first frame to reach her, is
        // lost, and Pax's is not.
        let loses_its_first = |seed: u64, name: &str| lost(seed, name, RATE, 1) == [1];
        let seed = (0..)
            .find(|seed| loses_its_first(*seed, "raya") && !loses_its_first(*seed, "pax"))
            .expect("a seed");
        let mut plan = plan(&CAST, json!([{"step": "seat", "to": 3, "batch": 2}]));
        plan["budget_s"] = json!(5);
        plan["network"] = json!({
            "seed": seed,
            "loss": RATE,
            "welcome_loss": RATE,
            "delay_ms": {"pax": 100},
        });

        let left = run_of(&plan);

        let stopped = left.result["stopped"].as_str().expect("a stop");
        assert!(
            stopped.starts_with("the roster of 3 members was not held within the budget by: ")
                && stopped.contains("raya"),
            "{stopped}"
        );
        for (name, delay_ms) in [("saro", 0), ("raya", 0), ("pax", 100)] {
            let member = left.member(name);
            let network = &member["network"];
            assert_eq!(
                [&network["seed"], &network["delay_ms"]],
                [&json!(seed), &json!(delay_ms)]
            );
            assert_eq!([&network["loss"], &network["welcome_loss"]], [RATE, RATE]);
            // A member's own frames are those it published.
            let own = member["app_id"]
                .as_str()
                .map_or(json!([]), |app_id| json!([app_id]));
            assert_eq!(
                [&network["cut"], &network["own"], &network["withheld"]],
                [&json!([]), &own, &Value::Null]
            );
            assert_eq!(network["armed"], false);

            let frames = &network["frames"];
            let count = |of: &Value| of.as_u64().expect("a count");
            let arrived = count(&frames["arrived"]);
            let dropped = member["dropped"].as_array().expect("the drops");
            let numbers: Vec<u64> = dropped.iter().map(|drop| count(&drop["n"])).collect();
            assert_eq!(numbers, lost(seed, name, RATE, arrived), "{name}");
            let of_a_welcome = |drop: &&Value| drop["why"] == "welcome_loss";
            assert_eq!(
                [
                    &frames["dropped"]["welcome_loss"],
                    &frames["dropped"]["loss"]
                ],
                [
                    dropped.iter().filter(of_a_welcome).count(),
                    dropped.iter().filter(|drop| drop["why"] == "loss").count()
                ]
            );
            assert_eq!(
                arrived,
                count(&frames["passed"]) + count(&frames["held"]) + dropped.len() as u64
            );
        }

        let raya = left.member("raya");
        assert_eq!(
            [&raya["seated"], &raya["app_id"]],
            [&json!(false), &Value::Null]
        );
        let [invite] = &raya["dropped"].as_array().expect("the drops")[..] else {
            panic!("{raya}");
        };
        assert_eq!(
            [&invite["n"], &invite["why"], &invite["kind"]],
            [&json!(1), &json!("welcome_loss"), &json!("welcome")]
        );
        // An invite is sent to an inbox, by no conversation.
        assert!(invite.get("app_id").is_none() && invite.get("seat").is_none());
    }

    #[test]
    fn a_delay_of_the_plan_holds_what_its_member_receives() {
        let steps = json!([
            {"step": "seat", "to": 3, "batch": 2},
            {"step": "post", "from": ["saro"]},
        ]);
        let mut plan = with_network(plan(&CAST, steps));
        plan["network"]["delay_ms"] = json!({"pax": 120});

        let left = run_of(&plan);

        // Raya reads the post in the pass that follows the send, and Pax in
        // the first pass to start once the delay has passed.
        assert_eq!(left.result["posts"][0]["read_s"], json!([0.0, 0.15]));
    }

    #[test]
    fn a_frame_held_when_the_steps_end_reaches_its_member() {
        let steps = json!([
            {"step": "seat", "to": 3, "batch": 2},
            {"step": "post", "from": ["saro"]},
        ]);
        let mut plan = with_network(plan(&CAST, steps));
        plan["network"]["delay_ms"] = json!({"pax": 120});
        plan["unwaited"] = json!({"rosters": [], "posts": ["pax"]});

        let left = run_of(&plan);

        // The steps end as Raya reads the post, with Pax's copy still held.
        assert_eq!(left.result["posts"][0]["read_s"], json!([0.0]));
        let sent = left
            .records()
            .into_iter()
            .find(|record| record.name == "sent");
        let frame = sent.expect("a post").fields["frame"].clone();
        assert_eq!(left.written_of("pax", &frame), ["received"]);
        assert_eq!(left.member("pax")["network"]["frames"]["held"], 0);
    }

    /// The plan of a group of three in which Saro is cut off from the other
    /// two, and Raya posts.
    fn cut_plan(then: Value) -> Value {
        let mut steps = json!([
            {"step": "seat", "to": 3, "batch": 2},
            {"step": "cut", "sides": [["saro"], ["raya", "pax"]]},
            {"step": "post", "from": ["raya"]},
        ]);
        steps
            .as_array_mut()
            .unwrap()
            .extend(then.as_array().unwrap().iter().cloned());
        let mut plan = with_network(plan(&CAST, steps));
        plan["budget_s"] = json!(2);
        plan
    }

    #[test]
    fn a_cut_keeps_each_side_from_the_frames_of_the_other() {
        let left = run_of(&cut_plan(json!([])));
        let result = &left.result;
        let [saro, raya, pax] = CAST.map(|name| left.member(name));

        assert_eq!(
            result["stopped"],
            "the post of raya was not read within the budget by: saro"
        );
        assert_eq!(result["steps_done"], 2);
        assert_eq!(
            result["posts"],
            json!([{"poster": "raya", "readers": 2, "unread": ["saro"], "read_s": [0.0]}])
        );

        let mut others = [raya["app_id"].clone(), pax["app_id"].clone()];
        others.sort_by_key(|app_id| app_id.as_str().map(str::to_owned));
        assert_eq!(saro["network"]["cut"], json!(others));
        assert_eq!(raya["network"]["cut"], json!([saro["app_id"]]));
        assert_eq!(pax["network"]["cut"], json!([saro["app_id"]]));

        let dropped = saro["dropped"].as_array().expect("the drops");
        assert_eq!(saro["network"]["frames"]["dropped"]["cut"], dropped.len());
        assert!(dropped.iter().any(|drop| drop["app_id"] == raya["app_id"]));
        for drop in dropped {
            assert_eq!([&drop["why"], &drop["kind"]], ["cut", "group"]);
            assert!(others.contains(&drop["app_id"]), "{drop}");
        }
        assert_eq!(
            [&raya["dropped"], &pax["dropped"]],
            [&json!([]), &json!([])]
        );
    }

    #[test]
    fn a_heal_ends_the_cut() {
        let mut plan = cut_plan(json!([{"step": "heal"}, {"step": "post", "from": ["pax"]}]));
        plan["unwaited"] = json!({"rosters": [], "posts": ["saro"]});

        let left = run_of(&plan);

        assert_eq!(left.result["stopped"], Value::Null);
        assert_eq!(left.result["steps_done"], 5);
        for name in CAST {
            assert_eq!(left.member(name)["network"]["cut"], json!([]), "{name}");
        }
        let records = left.records();
        let sent = records.iter().filter(|record| record.name == "sent");
        let frames: Vec<&str> = sent.map(|record| record.fields["frame"].as_str()).collect();
        let [in_the_cut, after_it] = frames[..] else {
            panic!("{} posts were sent", frames.len());
        };
        assert!(left.written_of("saro", in_the_cut).is_empty());
        assert_eq!(left.written_of("saro", after_it), ["received"]);
    }

    #[test]
    fn a_cut_waits_within_the_budget_for_a_seated_member_to_hold_the_conversation() {
        let cut = json!({"step": "cut", "sides": [["saro"], ["pax"]]});
        // No roster is waited on for Pax, whose model holds its welcome past
        // the step that ends the batch.
        let steps = json!([{"step": "seat", "to": 3, "batch": 2}, cut]);
        let mut plan = with_network(plan(&CAST, steps));
        plan["network"]["delay_ms"] = json!({"pax": 100});
        plan["unwaited"] = json!({"rosters": ["pax"], "posts": []});

        let left = run_of(&plan);
        let [saro, _, pax] = CAST.map(|name| left.member(name));

        assert_eq!(left.result["stopped"], Value::Null);
        assert!(pax["app_id"].is_string(), "{pax}");
        assert_eq!(saro["network"]["cut"], json!([pax["app_id"]]));
        assert_eq!(pax["network"]["cut"], json!([saro["app_id"]]));

        // No invite arrives, and no roster is waited on for the two invited.
        let mut plan = no_invite_arrives();
        plan["steps"][1] = cut;
        plan["unwaited"] = json!({"rosters": ["raya", "pax"], "posts": []});

        let left = run_of(&plan);
        let result = &left.result;

        assert_eq!(
            result["stopped"],
            "pax: a cut names it, and it does not hold the conversation"
        );
        assert_eq!(result["steps_done"], 1);
        // The cut waited for Pax for the whole of the budget.
        let seated_s = result["batches"][0]["seated_s"].as_f64().expect("seconds");
        assert_eq!(result["virtual_s"], seated_s + 2.0);
    }

    /// The plan in which no invite arrives, so that Saro alone comes to hold
    /// the roster of three.
    fn no_invite_arrives() -> Value {
        let steps = json!([
            {"step": "seat", "to": 3, "batch": 2},
            {"step": "post", "from": ["saro"]},
        ]);
        let mut plan = plan(&CAST, steps);
        plan["budget_s"] = json!(2);
        plan["network"] = json!({"seed": 1, "loss": 0, "welcome_loss": 1, "delay_ms": {}});
        plan
    }

    #[test]
    fn a_wait_that_outlasts_the_budget_stops_the_steps() {
        let left = run_of(&no_invite_arrives());
        let result = &left.result;

        assert_eq!(
            result["stopped"],
            "the roster of 3 members was not held within the budget by: raya, pax"
        );
        assert_eq!(result["steps_done"], 0);
        assert_eq!(
            [&result["batches"], &result["posts"]],
            [&json!([]), &json!([])]
        );
        // The add was accepted at its first try, and the wait had the budget.
        assert_eq!(result["virtual_s"], 2.0);
        let seated: Vec<&Value> = CAST.map(|name| &left.member(name)["seated"]).to_vec();
        assert_eq!(seated, [true, false, false]);
    }

    /// The step that seats Raya beside Saro, and the virtual seconds it
    /// takes: up to the step whose wakeup publishes Raya's welcome, which she
    /// takes in the pass after it.
    fn seat_of_raya() -> (Value, Value) {
        let seat = json!({"step": "seat", "to": 2, "batch": 1});
        let left = run_of(&plan(&CAST, json!([seat])));
        (seat, left.result["batches"][0]["seated_s"].clone())
    }

    #[test]
    fn a_wait_takes_in_what_the_last_step_of_its_budget_published() {
        let (seat, seated_s) = seat_of_raya();
        let mut plan = plan(&CAST, json!([seat]));
        plan["budget_s"] = seated_s.clone();

        let left = run_of(&plan);

        assert_eq!(left.result["stopped"], Value::Null);
        assert_eq!(left.result["virtual_s"], seated_s);
    }

    #[test]
    fn the_result_holds_what_the_last_step_published() {
        let (seat, seated_s) = seat_of_raya();
        // No roster is waited on, and the hold ends where the wait would.
        let mut plan = plan(&CAST, json!([seat, {"step": "hold", "seconds": seated_s}]));
        plan["unwaited"] = json!({"rosters": ["saro", "raya"], "posts": []});

        let left = run_of(&plan);

        assert_eq!(left.result["stopped"], Value::Null);
        assert!(left.member("raya")["app_id"].is_string());
    }

    #[test]
    fn the_members_left_unwaited_are_not_waited_on() {
        let mut plan = no_invite_arrives();
        plan["unwaited"] = json!({"rosters": ["raya", "pax"], "posts": ["raya", "pax"]});

        let left = run_of(&plan);
        let result = &left.result;

        assert_eq!(result["stopped"], Value::Null);
        assert_eq!(result["steps_done"], 2);
        let batch = &result["batches"][0];
        assert_eq!([&batch["joiners"], &batch["members"]], [2, 3]);
        assert_eq!(
            result["posts"],
            json!([{"poster": "saro", "readers": 0, "unread": [], "read_s": []}])
        );
        // The steps seated the two, and neither holds the conversation.
        for name in ["raya", "pax"] {
            let member = left.member(name);
            assert_eq!(
                [&member["seated"], &member["app_id"]],
                [&json!(true), &Value::Null]
            );
        }
    }

    #[test]
    fn an_op_refused_until_the_budget_runs_out_stops_the_steps() {
        // Pax holds no conversation to post to.
        let mut plan = plan(&CAST, json!([{"step": "post", "from": ["pax"]}]));
        plan["budget_s"] = json!(0.12);

        let left = run_of(&plan);
        let result = &left.result;

        let stopped = result["stopped"].as_str().expect("a stop");
        let refused = "pax: the post was refused until the budget ran out: convo with id: ";
        assert!(stopped.starts_with(refused), "{stopped}");
        assert_eq!(result["steps_done"], 0);
        assert_eq!(result["posts"], json!([]));
        // The post was tried for the whole of the budget.
        assert_eq!(result["virtual_s"], 0.12);
    }

    #[test]
    fn a_step_the_network_cannot_take_stops_the_steps() {
        let stopped = |steps: Value| {
            let left = run_of(&with_network(plan(&CAST, steps)));
            (
                left.result["steps_done"].clone(),
                left.result["stopped"].clone(),
            )
        };

        let not_seated = stopped(json!([
            {"step": "seat", "to": 2, "batch": 1},
            {"step": "cut", "sides": [["saro"], ["pax"]]},
        ]));
        assert_eq!(
            not_seated,
            (
                json!(1),
                json!("pax: a cut names it, and it does not hold the conversation")
            )
        );
        // Before the first seat the creator, who holds the conversation, has
        // published nothing.
        let before_any_seat = stopped(json!([{"step": "cut", "sides": [["saro"], ["pax"]]}]));
        assert_eq!(
            before_any_seat,
            (
                json!(0),
                json!("pax: a cut names it, and it does not hold the conversation")
            )
        );

        let armed_twice = stopped(json!([
            {"step": "withhold", "from": "raya"},
            {"step": "withhold", "from": "raya"},
        ]));
        assert_eq!(
            armed_twice,
            (
                json!(1),
                json!("raya: no candidate is withheld: the rule is armed already")
            )
        );

        let armed_for_another = stopped(json!([
            {"step": "withhold", "from": "raya"},
            {"step": "withhold", "from": "pax"},
        ]));
        assert_eq!(
            armed_for_another,
            (
                json!(1),
                json!("pax: no candidate is withheld: the next batch withholds one from raya")
            )
        );
    }

    #[test]
    fn a_hold_passes_its_time() {
        let virtual_s = |seconds: f64| {
            let left = run_of(&plan(
                &["saro"],
                json!([{"step": "hold", "seconds": seconds}]),
            ));
            assert_eq!(left.result["steps_done"], 1);
            left.result["virtual_s"].clone()
        };

        assert_eq!(virtual_s(1.25), 1.25);
        // The last step of a hold is as short as what is left of it.
        assert_eq!(virtual_s(0.12), 0.12);
    }

    /// Fresh groups a member's turn may take, as many as
    /// `test_group_v2_lost_candidate` gives one.
    const GROUPS_PER_TURN: usize = 32;

    const FIVE: [&str; 5] = ["saro", "raya", "pax", "mira", "kes"];

    /// Withholds a candidate from `member` in the batch that seats Kes, holds
    /// the run to what the candidate calls for, and says whether the group
    /// applied it.
    fn withhold_from(member: &str) -> bool {
        let left = run_of(&with_network(plan(
            &FIVE,
            json!([
                {"step": "seat", "to": 4, "batch": 1},
                {"step": "withhold", "from": member},
                {"step": "seat", "to": 5, "batch": 1},
                {"step": "post", "from": ["kes"]},
            ]),
        )));
        let result = &left.result;

        let told = left.member(member);
        let [candidate] = &told["dropped"].as_array().expect("the drops")[..] else {
            panic!("{told}");
        };
        assert_eq!(
            [&candidate["why"], &candidate["kind"]],
            ["withheld", "candidate"]
        );
        let withheld = &told["network"]["withheld"];
        assert_eq!(
            [&withheld["app_id"], &withheld["seat"]],
            [&candidate["app_id"], &candidate["seat"]]
        );
        assert_eq!(told["network"]["frames"]["dropped"]["withheld"], 1);
        let applied = withheld["applied"]
            .as_bool()
            .expect("whether it was applied");

        // The log's report: the state each member holds the last epoch in.
        let records = left.records();
        let epochs = records.iter().filter(|record| record.name == "epoch");
        let last = epochs.clone().map(Record::epoch).max().expect("an epoch");
        let state = |name: &str| {
            let installation = &left.member(name)["installation"];
            let mut epochs = epochs.clone();
            let held = epochs
                .find(|record| record.fields["signer"] == *installation && record.epoch() == last);
            held.unwrap_or_else(|| panic!("{name} holds no epoch {last}"))
                .fields["state"]
                .as_str()
        };
        // Kes joins by the welcome of the commit the group applied.
        let group = state("kes");
        for other in FIVE.iter().filter(|other| **other != member) {
            assert_eq!(state(other), group, "{other} is with the group");
        }
        let alone = state(member) != group;
        assert_eq!(
            alone, applied,
            "{member} is alone exactly when the group applied the candidate withheld from it"
        );
        // A member alone cannot decrypt what the group posts, and a frame
        // that does not decrypt is one the member rejected.
        let undecrypted = records.iter().filter(|record| {
            record.name == "unreadable"
                && record.fields["signer"] == told["installation"]
                && record.fields["why"] == "decrypt"
        });
        let undecrypted = undecrypted.count() as u64;
        let rejected = told["inbound_errors"].as_object().expect("the errors");
        let rejected: u64 = rejected.values().filter_map(Value::as_u64).sum();
        assert!(rejected >= undecrypted, "{member}: {told}");
        assert!(!alone || undecrypted > 0, "{member}: {told}");

        // A member alone is set apart, and the steps go on without it.
        assert_eq!(result["stopped"], Value::Null);
        assert_eq!(result["steps_done"], 4);
        let post = &result["posts"][0];
        assert_eq!(post["readers"], if alone { 3 } else { 4 });
        assert_eq!(post["unread"], json!([]));
        alone
    }

    #[test]
    fn a_withheld_candidate_sets_its_member_apart_when_the_group_applies_it() {
        for member in &FIVE[..4] {
            let (mut alone, mut whole) = (false, false);
            for _ in 0..GROUPS_PER_TURN {
                if withhold_from(member) {
                    alone = true;
                } else {
                    whole = true;
                }
                if alone && whole {
                    break;
                }
            }
            assert!(
                alone && whole,
                "{member} in {GROUPS_PER_TURN} groups: alone {alone}, with the group {whole}"
            );
        }
    }

    #[test]
    fn a_member_set_apart_is_not_waited_on_for_the_rosters_that_follow() {
        let six = ["saro", "raya", "pax", "mira", "kes", "tomo"];
        let steps = json!([
            {"step": "seat", "to": 4, "batch": 1},
            {"step": "withhold", "from": "pax"},
            {"step": "seat", "to": 6, "batch": 1},
        ]);
        // The state each installation holds a roster of `members` in.
        let held = |left: &Left, members: &str| -> BTreeMap<String, String> {
            let records = left.records().into_iter();
            let epochs = records
                .filter(|record| record.name == "epoch" && record.fields["members"] == members);
            epochs
                .map(|record| {
                    let field = |name: &str| record.fields[name].clone();
                    (field("signer"), field("state"))
                })
                .collect()
        };
        let installation = |left: &Left, name: &str| {
            let id = left.member(name)["installation"].as_str();
            id.expect("an id").to_string()
        };
        // A group that applies the candidate withheld from Pax leaves Pax in
        // a state of its own, which no later commit of the group reaches.
        let alone = |left: &Left| {
            let five = held(left, "5");
            let state = |name: &str| five.get(&installation(left, name));
            state("pax").is_some() && state("pax") != state("kes")
        };
        let left = (0..GROUPS_PER_TURN)
            .map(|_| run_of(&with_network(plan(&six, steps.clone()))))
            .find(alone)
            .expect("a group that left Pax alone");

        // A stop names the members that were waited on and did not come to
        // hold the roster. Pax does not hold it, and no stop names Pax.
        assert!(!held(&left, "6").contains_key(&installation(&left, "pax")));
        let stopped = left.result["stopped"].as_str().unwrap_or_default();
        assert!(!stopped.contains("pax"), "{stopped}");
    }

    /// The members of `plan` in their group, for `steps` to do with them what
    /// no step of a plan does.
    fn by_hand<T>(plan: &Value, steps: impl FnOnce(&mut Run) -> T) -> T {
        let plan = Plan::read(&plan.to_string()).expect("a plan");
        let log = tempfile::tempfile().expect("a file");
        let clock = MockClock::new();
        clock.advance(plan.start);
        let (subscriber, records) = group_log::on(log, clock.clone());
        tracing::subscriber::with_default(subscriber, || {
            steps(&mut Run::new(&plan, clock, records))
        })
    }

    #[test]
    fn a_group_in_two_states_stops_the_steps_ahead_of_a_roster_that_is_not_held() {
        const NOT_HELD: &str = "the roster of 5 members was not held within the budget by: kes";
        let (raya, kes) = (1, 4);
        // A candidate is withheld from Raya with no word to the run, as loss
        // would lose it, and Kes loses its welcome: the wait for the roster of
        // five runs out whichever commit the group applies.
        let stopped = |_| {
            by_hand(&with_network(plan(&FIVE, json!([]))), |run| {
                if let Err(Stopped(why)) = run.seat(4, 1) {
                    panic!("{why}");
                }
                let armed = run.rules(raya).withhold_next_candidate();
                armed.expect("the rule is armed");
                let every = Rate::try_from(1.0).expect("a rate");
                run.rules(kes).set_welcome_loss(every);
                let Err(Stopped(why)) = run.add(5) else {
                    panic!("Kes is seated");
                };
                why
            })
        };
        let mut stops = (0..GROUPS_PER_TURN).map(stopped);
        let split = stops.find(|why| why != NOT_HELD);

        // A group that applies the candidate leaves Raya in a state of her
        // own.
        assert_eq!(
            split.as_deref(),
            Some("the group split: 4 of its 5 members hold epoch 4, in 2 states")
        );
    }

    #[test]
    fn a_batch_with_no_candidate_to_withhold_stops_the_steps() {
        // Saro alone mints the commit that seats Raya, and is sent none.
        let steps = json!([
            {"step": "withhold", "from": "saro"},
            {"step": "seat", "to": 2, "batch": 1},
        ]);
        let mut plan = with_network(plan(&CAST, steps));
        plan["budget_s"] = json!(3);

        let left = run_of(&plan);

        assert_eq!(
            left.result["stopped"],
            "saro: no candidate was withheld within the budget"
        );
        assert_eq!(left.result["steps_done"], 1);
        assert_eq!(left.result["virtual_s"], 3.0);
        assert_eq!(left.member("saro")["network"]["armed"], true);
    }

    #[test]
    fn a_candidate_withheld_ahead_of_its_batch_stops_the_steps() {
        // No roster is waited on, so the rule is armed while the round that
        // seats Mira is under way, and it withholds a candidate of that round.
        let steps = json!([
            {"step": "seat", "to": 3, "batch": 2},
            {"step": "hold", "seconds": 5},
            {"step": "seat", "to": 4, "batch": 1},
            {"step": "withhold", "from": "raya"},
            {"step": "hold", "seconds": 5},
            {"step": "seat", "to": 5, "batch": 1},
        ]);
        let mut plan = with_network(plan(&FIVE, steps));
        plan["unwaited"] = json!({"rosters": FIVE, "posts": []});

        let left = run_of(&plan);

        assert_eq!(
            left.result["stopped"],
            "raya: a candidate was withheld before the add was accepted"
        );
        assert_eq!(left.result["steps_done"], 5);
    }

    fn node(epoch: u64, state: &str) -> Node {
        (epoch, state.to_string())
    }

    /// The lineage of `links` with its states placed, `newest` the states
    /// the members hold.
    fn placed(links: &[(Node, Node)], newest: &[Node]) -> Lineage {
        let mut lineage = Lineage::default();
        for (from, to) in links {
            lineage.link(from.clone(), to.clone());
        }
        lineage.place(&node(0, "r"), newest, None);
        lineage
    }

    #[test]
    fn at_a_fork_the_side_more_members_hold_carries_the_line_on() {
        // Three go on from "b" and two from "a", which then forks.
        let links = [
            (node(0, "r"), node(1, "s")),
            (node(1, "s"), node(2, "a")),
            (node(1, "s"), node(2, "b")),
            (node(2, "b"), node(3, "c")),
            (node(2, "a"), node(3, "d")),
            (node(2, "a"), node(3, "e")),
        ];
        let newest = [
            node(3, "c"),
            node(3, "c"),
            node(3, "c"),
            node(3, "d"),
            node(3, "e"),
        ];

        let lineage = placed(&links, &newest);

        let line = |state: &str, epoch: u64| lineage.line_of[&node(epoch, state)];
        assert_eq!([line("s", 1), line("b", 2), line("c", 3)], [0, 0, 0]);
        let main = line("a", 2);
        assert_eq!(lineage.lines[main].from, Some((0, 2)));
        assert_eq!(lineage.lines[main].depth, 1);
        // One member each: the tie goes to the state the report ranks first.
        assert_eq!(line("d", 3), main);
        let sub = line("e", 3);
        assert_eq!(
            (lineage.lines[sub].from, lineage.lines[sub].depth),
            (Some((main, 3)), 2)
        );
        assert_eq!(lineage.main_lines(), 2);
        assert_eq!([lineage.epoch_of(0), lineage.epoch_of(main)], [3, 3]);
    }

    #[test]
    fn at_a_fork_the_side_the_followed_state_is_under_carries_the_line_on() {
        let mut lineage = Lineage::default();
        let links = [
            (node(0, "r"), node(1, "a")),
            (node(0, "r"), node(1, "b")),
            (node(1, "a"), node(2, "c")),
            (node(1, "b"), node(2, "d")),
            (node(1, "b"), node(2, "e")),
        ];
        for (from, to) in links {
            lineage.link(from, to);
        }
        // The one followed is past "a", and four are past "b".
        let newest = [
            node(2, "c"),
            node(2, "d"),
            node(2, "d"),
            node(2, "d"),
            node(2, "e"),
        ];

        lineage.place(&node(0, "r"), &newest, Some(&node(2, "c")));

        let line = |state: &str, epoch: u64| lineage.line_of[&node(epoch, state)];
        assert_eq!([line("a", 1), line("c", 2)], [0, 0]);
        let main = line("b", 1);
        assert_eq!(lineage.lines[main].from, Some((0, 1)));
        // Off the line followed, the side more members hold carries it on.
        assert_eq!(line("d", 2), main);
        assert_eq!(lineage.lines[line("e", 2)].from, Some((main, 2)));
    }

    #[test]
    fn a_line_once_given_is_kept_as_members_move() {
        let mut lineage = placed(
            &[(node(0, "r"), node(1, "a")), (node(0, "r"), node(1, "b"))],
            &[node(1, "a"), node(1, "a"), node(1, "b")],
        );
        assert_eq!(lineage.line_of[&node(1, "a")], 0);

        // The branch grows past the trunk in members and in epochs.
        lineage.link(node(1, "b"), node(2, "c"));
        lineage.place(
            &node(0, "r"),
            &[node(1, "a"), node(2, "c"), node(2, "c"), node(2, "c")],
            None,
        );

        assert_eq!(lineage.line_of[&node(1, "a")], 0);
        assert_eq!(lineage.line_of[&node(2, "c")], 1);
        assert_eq!([lineage.epoch_of(0), lineage.epoch_of(1)], [1, 2]);
    }

    #[test]
    fn a_state_with_one_after_it_is_behind() {
        let lineage = placed(
            &[(node(0, "r"), node(1, "a")), (node(1, "a"), node(2, "b"))],
            &[],
        );

        assert!(lineage.followed(&node(1, "a")));
        assert!(!lineage.followed(&node(2, "b")));
        assert!(lineage.under(&node(2, "b"), &node(1, "a")));
        assert!(lineage.under(&node(1, "a"), &node(1, "a")));
        assert!(!lineage.under(&node(1, "a"), &node(2, "b")));
        assert!(!lineage.under(&node(2, "x"), &node(1, "a")));
    }

    /// A plan whose one step grows the group of `members`.
    fn grow_plan(members: &[&str], epochs: u64, adders: u64) -> Value {
        let step = json!({"step": "grow", "epochs": epochs, "adders": adders, "branches": 5});
        plan(members, json!([step]))
    }

    const EIGHT: [&str; 8] = [
        "saro", "raya", "pax", "mira", "kes", "tomo", "peer7", "peer8",
    ];

    #[test]
    fn a_grow_step_ends_where_the_trunk_holds_its_epochs() {
        let left = run_of(&grow_plan(&EIGHT, 3, 2));
        let (result, grown) = (&left.result, &left.result["grow"]);

        assert_eq!(
            [&result["stopped"], &result["steps_done"]],
            [&Value::Null, &json!(1)]
        );
        assert_eq!(grown["ended"], "the trunk reached epoch 3");
        assert_eq!([&grown["trunk_epoch"], &grown["main"]], [3, 1]);
        let [trunk] = &grown["lines"].as_array().expect("the lines")[..] else {
            panic!("{grown}");
        };
        assert_eq!(
            [&trunk["kind"], &trunk["from"]],
            [&json!("trunk"), &Value::Null]
        );
        let joiners: Vec<&str> = grown["rounds"]
            .as_array()
            .expect("the rounds")
            .iter()
            .flat_map(|round| {
                let adders = round["adders"].as_array().expect("the adders").len();
                assert!((1..=2).contains(&adders), "{round}");
                assert_eq!(round["refused"], json!([]));
                assert_eq!(
                    round["joiners"].as_array().expect("the joiners").len(),
                    adders
                );
                round["joiners"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(Value::as_str)
            })
            .collect();
        // The members are added in the plan's order, and all are on the trunk.
        assert_eq!(joiners[..], EIGHT[1..=joiners.len()]);
        let mut on = trunk["members"].as_array().expect("the members").clone();
        on.sort_by_key(|name| name.to_string());
        let mut seated: Vec<Value> = EIGHT[..=joiners.len()]
            .iter()
            .map(|name| json!(name))
            .collect();
        seated.sort_by_key(|name| name.to_string());
        assert_eq!(on, seated);
        assert_eq!(
            [
                &grown["fell_behind"],
                &grown["behind"],
                &grown["never_joined"]
            ],
            [&json!([]), &json!([]), &json!([])]
        );
    }

    #[test]
    fn a_grow_step_by_the_creator_has_it_add_each_member_alone() {
        let step = json!({"step": "grow", "epochs": 4, "by": "saro", "branches": 5});
        let left = run_of(&plan(&EIGHT, json!([step])));
        let grown = &left.result["grow"];

        assert_eq!(grown["ended"], "the trunk reached epoch 4");
        let rounds = grown["rounds"].as_array().expect("the rounds");
        let joiners: Value = rounds
            .iter()
            .map(|round| round["joiners"].clone())
            .collect();
        assert_eq!(joiners, json!([["raya"], ["pax"], ["mira"], ["kes"]]));
        for round in rounds {
            assert_eq!(round["adders"], json!(["saro"]), "{round}");
        }
    }

    #[test]
    fn a_grow_step_of_a_size_has_its_adder_remove_the_member_seated_longest_ago() {
        let step = json!({"step": "grow", "epochs": 5, "by": "saro", "branches": 5, "size": 3});
        let left = run_of(&plan(&EIGHT, json!([step])));
        let grown = &left.result["grow"];

        assert_eq!(grown["ended"], "the trunk reached epoch 5");
        assert_eq!(grown["removed"], json!(["raya", "pax", "mira"]));
        assert_eq!(grown["lines"][0]["members"], json!(["saro", "kes", "tomo"]));
        assert_eq!(grown["never_joined"], json!([]));
    }

    #[test]
    fn a_grow_step_of_a_size_holds_no_line_above_it_and_no_member_removed() {
        let mut step = json!({"step": "grow", "epochs": 5, "adders": 2, "branches": 5});
        step["size"] = json!(4);
        let left = run_of(&plan(&EIGHT, json!([step])));
        let grown = &left.result["grow"];

        assert_eq!(grown["ended"], "the trunk reached epoch 5");
        let removed = grown["removed"].as_array().expect("the removed");
        assert!(!removed.is_empty(), "{grown}");
        for line in grown["lines"].as_array().expect("the lines") {
            let members = line["members"].as_array().expect("the members");
            assert!(members.len() <= 4, "{line}");
            assert!(
                members.iter().all(|member| !removed.contains(member)),
                "{grown}"
            );
        }
    }

    #[test]
    fn the_adders_of_each_round_are_drawn_from_the_plans_seed() {
        let adders = |seed: u64| {
            let mut plan = grow_plan(&EIGHT, 50, 3);
            plan["seed"] = json!(seed);
            let left = run_of(&plan);
            let rounds = left.result["grow"]["rounds"]
                .as_array()
                .expect("the rounds")
                .clone();
            rounds
                .into_iter()
                .map(|round| round["adders"].clone())
                .collect::<Vec<_>>()
        };

        assert_eq!(adders(3), adders(3));
        assert_ne!(adders(3), adders(4));
    }

    #[test]
    fn a_grow_step_ends_once_every_member_of_the_plan_is_seated() {
        let left = run_of(&grow_plan(&CAST, 50, 3));

        assert_eq!(
            left.result["grow"]["ended"],
            "every member of the plan was seated"
        );
        assert_eq!(left.member("pax")["seated"], true);
    }

    #[test]
    fn a_member_whose_welcome_is_lost_never_joins() {
        let mut plan = grow_plan(&CAST, 50, 1);
        plan["budget_s"] = json!(3);
        plan["network"] = json!({"seed": 1, "loss": 0, "welcome_loss": 1, "delay_ms": {}});

        let left = run_of(&plan);
        let grown = &left.result["grow"];

        assert_eq!(grown["ended"], "every member of the plan was seated");
        assert_eq!(grown["never_joined"], json!(["raya", "pax"]));
        // Saro waits the whole budget out for each.
        let settled = grown["rounds"].as_array().expect("the rounds");
        let settled: Vec<&Value> = settled.iter().map(|round| &round["settled_s"]).collect();
        assert_eq!(settled, [3.0, 3.0]);
    }

    #[test]
    fn the_stamps_of_the_log_are_the_virtual_clocks_from_the_plans_start() {
        let stamps = |start: u64| {
            let steps = json!([
                {"step": "seat", "to": 2, "batch": 1},
                {"step": "hold", "seconds": 1},
            ]);
            let mut plan = plan(&CAST, steps);
            plan["start"] = json!(start);
            let left = run_of(&plan);
            let records = left.log.lines().filter(|line| Record::read(line).is_some());
            let stamps = records.map(|line| line.split(' ').next());
            let stamps: Vec<String> = stamps.map(|stamp| stamp.expect("a stamp").into()).collect();
            // The roster Raya is the last to hold is the last record written.
            let seated_s = left.result["batches"][0]["seated_s"]
                .as_f64()
                .expect("seconds");
            let seated = Duration::from_secs(start) + Duration::from_secs_f64(seated_s);
            assert_eq!(stamps.last(), Some(&group_log::Stamp(seated).to_string()));
            let virtual_s = left.result["virtual_s"].clone();
            (stamps[0].clone(), seated_s, virtual_s)
        };

        // The members are made and the group created before the clock moves.
        let (first, seated_s, virtual_s) = stamps(START);
        assert_eq!(first, "1970-01-01T01:00:00.000000Z");
        assert!(seated_s > 0.0);
        // The time a run took is counted from where its clock started.
        let (first, _, from_the_start) = stamps(1_791_817_445);
        assert_eq!(first, "2026-10-12T15:04:05.000000Z");
        assert_eq!(from_the_start, virtual_s);
    }
}
