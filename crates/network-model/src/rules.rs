//! What becomes of each frame that arrives.

use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;
use std::str::FromStr;

use crate::frame::{AppId, Frame, Kind};
use crate::rng::SplitMix64;

/// The share of frames a rule drops: from 0, none, to 1, every one.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Rate(f64);

/// What is no rate: no number, or one outside 0 to 1.
#[derive(Debug, PartialEq, Eq)]
pub struct NotARate;

impl fmt::Display for NotARate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a rate is a number from 0 to 1")
    }
}

impl Error for NotARate {}

impl TryFrom<f64> for Rate {
    type Error = NotARate;

    fn try_from(rate: f64) -> Result<Self, NotARate> {
        if (0.0..=1.0).contains(&rate) {
            Ok(Self(rate))
        } else {
            Err(NotARate)
        }
    }
}

impl FromStr for Rate {
    type Err = NotARate;

    fn from_str(text: &str) -> Result<Self, NotARate> {
        text.parse::<f64>()
            .map_err(|_| NotARate)
            .and_then(Self::try_from)
    }
}

impl From<Rate> for f64 {
    fn from(rate: Rate) -> f64 {
        rate.0
    }
}

/// Why a frame was dropped.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Why {
    /// By the rate of a frame that is no welcome.
    Loss,
    /// By the rate of a welcome.
    WelcomeLoss,
    /// Its sender is cut off.
    Cut,
    /// It is the commit candidate the withhold rule waited for.
    Withheld,
}

/// A frame the rules dropped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dropped {
    /// Which arrival it was, from 1. Own frames are not numbered.
    pub n: u64,
    pub why: Why,
    pub frame: Frame,
    pub bytes: usize,
}

/// The rule that withholds one commit candidate.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum Withhold {
    #[default]
    Off,
    /// The next commit candidate to pass the other rules is withheld.
    Armed,
    Withheld {
        /// The app id of the steward that minted the candidate.
        sender: Option<AppId>,
        /// That steward's leaf.
        seat: Option<u32>,
        /// Whether that steward has broadcast a welcome since, which a
        /// steward does for a commit of its own that it merged. In the round
        /// of the withheld candidate that commit is the candidate, and the
        /// group applied it where its stewards merged one commit. A welcome
        /// of a later round sets it too.
        applied: bool,
    },
}

/// Why the withhold rule was not armed.
#[derive(Debug, PartialEq, Eq)]
pub enum NotArmed {
    /// It is armed and has yet to withhold a candidate.
    ArmedAlready,
    /// It has withheld its one candidate.
    Spent,
}

impl fmt::Display for NotArmed {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            NotArmed::ArmedAlready => "the rule is armed already",
            NotArmed::Spent => "the rule has withheld its one candidate",
        })
    }
}

impl Error for NotArmed {}

/// The rules one member's inbound frames are held to. Every decision follows
/// from the seed, the member's name and the frames shown so far.
#[derive(Debug)]
pub struct Rules {
    draws: SplitMix64,
    arrived: u64,
    loss: Rate,
    welcome_loss: Rate,
    cut: BTreeSet<AppId>,
    own: BTreeSet<AppId>,
    withhold: Withhold,
    lost: u64,
    welcomes_lost: u64,
    cut_off: u64,
    withheld: u64,
}

impl Rules {
    /// The rules of `member` under `seed`, with every one of them off.
    pub fn new(seed: u64, member: &str) -> Self {
        Self {
            draws: SplitMix64::of_member(seed, member),
            arrived: 0,
            loss: Rate::default(),
            welcome_loss: Rate::default(),
            cut: BTreeSet::new(),
            own: BTreeSet::new(),
            withhold: Withhold::Off,
            lost: 0,
            welcomes_lost: 0,
            cut_off: 0,
            withheld: 0,
        }
    }

    /// The rate at which a frame that is no welcome is lost.
    pub fn set_loss(&mut self, rate: Rate) {
        self.loss = rate;
    }

    /// The rate at which a welcome is lost. It has a rate of its own because
    /// a welcome is sent once, and a joiner that misses it stays out.
    pub fn set_welcome_loss(&mut self, rate: Rate) {
        self.welcome_loss = rate;
    }

    /// Cuts off `senders`, and no one else: their frames are dropped.
    pub fn set_cut(&mut self, senders: impl IntoIterator<Item = AppId>) {
        self.cut = senders.into_iter().collect();
    }

    /// Cuts off no one.
    pub fn heal(&mut self) {
        self.cut.clear();
    }

    /// The senders cut off.
    pub fn cut(&self) -> &BTreeSet<AppId> {
        &self.cut
    }

    /// Arms the rule that withholds the next commit candidate. It withholds
    /// one candidate and is not armed again.
    pub fn withhold_next_candidate(&mut self) -> Result<(), NotArmed> {
        match self.withhold {
            Withhold::Off => {
                self.withhold = Withhold::Armed;
                Ok(())
            }
            Withhold::Armed => Err(NotArmed::ArmedAlready),
            Withhold::Withheld { .. } => Err(NotArmed::Spent),
        }
    }

    pub fn withhold(&self) -> &Withhold {
        &self.withhold
    }

    /// Takes `app_id` for one of this member's own: a frame of it is the
    /// member's own coming back, which no rule applies to.
    pub fn add_own(&mut self, app_id: AppId) {
        self.own.insert(app_id);
    }

    /// The app ids taken for this member's own.
    pub fn own(&self) -> &BTreeSet<AppId> {
        &self.own
    }

    /// Whether `frame` is this member's own coming back.
    pub(crate) fn owns(&self, frame: &Frame) -> bool {
        frame
            .sender
            .as_ref()
            .is_some_and(|sender| self.own.contains(sender))
    }

    pub(crate) fn arrived(&self) -> u64 {
        self.arrived
    }

    /// How many frames were dropped for `why`.
    pub(crate) fn dropped(&self, why: Why) -> u64 {
        match why {
            Why::Loss => self.lost,
            Why::WelcomeLoss => self.welcomes_lost,
            Why::Cut => self.cut_off,
            Why::Withheld => self.withheld,
        }
    }

    /// What becomes of the next frame to arrive, of `bytes` bytes: `None`
    /// when it passes.
    pub fn decide(&mut self, frame: Frame, bytes: usize) -> Option<Dropped> {
        if self.owns(&frame) {
            return None;
        }
        // The n-th arrival takes the n-th number whatever becomes of it, so a
        // frame lost at one rate is lost at every higher one, and a cut moves
        // no draw.
        self.arrived += 1;
        let draw = self.draws.next_unit();

        let why = if self.cuts(&frame) {
            Some(Why::Cut)
        } else if frame.kind == Kind::Welcome {
            (draw < f64::from(self.welcome_loss)).then_some(Why::WelcomeLoss)
        } else {
            (draw < f64::from(self.loss)).then_some(Why::Loss)
        };
        let why = why.or_else(|| self.withholds(&frame).then_some(Why::Withheld))?;

        let count = match why {
            Why::Loss => &mut self.lost,
            Why::WelcomeLoss => &mut self.welcomes_lost,
            Why::Cut => &mut self.cut_off,
            Why::Withheld => &mut self.withheld,
        };
        *count += 1;
        Some(Dropped {
            n: self.arrived,
            why,
            frame,
            bytes,
        })
    }

    fn cuts(&self, frame: &Frame) -> bool {
        frame
            .sender
            .as_ref()
            .is_some_and(|sender| self.cut.contains(sender))
    }

    /// Whether `frame`, which passed the other rules, is the candidate to
    /// withhold. A welcome of the withheld candidate's sender is noted here.
    fn withholds(&mut self, frame: &Frame) -> bool {
        match (&mut self.withhold, frame.kind) {
            (Withhold::Armed, Kind::Candidate { seat }) => {
                self.withhold = Withhold::Withheld {
                    sender: frame.sender.clone(),
                    seat,
                    applied: false,
                };
                true
            }
            (
                Withhold::Withheld {
                    sender, applied, ..
                },
                Kind::Welcome,
            ) if sender.is_some() && *sender == frame.sender => {
                *applied = true;
                false
            }
            _ => false,
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) const SARO: &str = "5a904c11e7";
    pub(crate) const RAYA: &str = "c2118f3b0d";

    pub(crate) fn app_id(hex: &str) -> AppId {
        hex.parse().unwrap()
    }

    pub(crate) fn rate(rate: f64) -> Rate {
        Rate::try_from(rate).unwrap()
    }

    pub(crate) fn frame(kind: Kind, sender: Option<&str>) -> Frame {
        Frame {
            kind,
            sender: sender.map(app_id),
        }
    }

    /// A post, or any other frame of a group, from `sender`.
    pub(crate) fn post(sender: &str) -> Frame {
        frame(Kind::Group, Some(sender))
    }

    pub(crate) fn candidate(sender: &str, seat: u32) -> Frame {
        frame(Kind::Candidate { seat: Some(seat) }, Some(sender))
    }

    fn welcome(sender: &str) -> Frame {
        frame(Kind::Welcome, Some(sender))
    }

    /// The welcome sent to an inbox.
    fn invite() -> Frame {
        frame(Kind::Welcome, None)
    }

    /// The arrivals, from 1, that `rules` drops of `frames` posts from Saro.
    fn posts_lost(rules: &mut Rules, frames: u64) -> Vec<u64> {
        (0..frames)
            .filter_map(|_| rules.decide(post(SARO), 100))
            .map(|dropped| dropped.n)
            .collect()
    }

    fn lossy(seed: u64, member: &str, loss: f64) -> Rules {
        let mut rules = Rules::new(seed, member);
        rules.set_loss(rate(loss));
        rules
    }

    #[test]
    fn with_no_rule_set_every_frame_passes() {
        let mut rules = Rules::new(7, "pax");

        for frame in [
            post(SARO),
            candidate(SARO, 0),
            welcome(SARO),
            invite(),
            frame(Kind::Other, None),
        ] {
            assert_eq!(rules.decide(frame, 100), None);
        }
        assert_eq!(rules.withhold(), &Withhold::Off);
    }

    #[test]
    fn a_seed_and_a_name_give_the_same_drops_again() {
        let lost = posts_lost(&mut lossy(7, "pax", 0.5), 200);

        assert_eq!(posts_lost(&mut lossy(7, "pax", 0.5), 200), lost);
        assert!(!lost.is_empty() && lost.len() < 200, "{lost:?}");
    }

    #[test]
    fn another_seed_or_another_name_gives_other_drops() {
        let lost = posts_lost(&mut lossy(7, "pax", 0.5), 200);

        assert_ne!(posts_lost(&mut lossy(8, "pax", 0.5), 200), lost);
        assert_ne!(posts_lost(&mut lossy(7, "mira", 0.5), 200), lost);
    }

    #[test]
    fn the_drops_of_a_seed_and_a_name_are_those_of_its_stream() {
        // The first twenty numbers of seed 7 and "saro" that are below a half.
        assert_eq!(
            posts_lost(&mut lossy(7, "saro", 0.5), 20),
            [1, 2, 3, 4, 5, 8, 9, 10, 11, 13, 16, 17, 18, 20]
        );
    }

    #[test]
    fn a_frame_lost_at_a_rate_is_lost_at_every_higher_rate() {
        let mut lower: Vec<u64> = vec![];
        for loss in [0.0, 0.1, 0.3, 0.5, 0.9, 1.0] {
            let lost = posts_lost(&mut lossy(7, "pax", loss), 500);

            assert!(lower.iter().all(|n| lost.contains(n)), "at {loss}");
            assert!(lower.len() <= lost.len(), "at {loss}");
            lower = lost;
        }
    }

    #[test]
    fn a_rate_of_0_loses_no_frame_and_a_rate_of_1_every_frame() {
        assert_eq!(
            posts_lost(&mut lossy(7, "pax", 0.0), 500),
            Vec::<u64>::new()
        );
        assert_eq!(
            posts_lost(&mut lossy(7, "pax", 1.0), 500),
            (1..=500).collect::<Vec<u64>>()
        );
    }

    #[test]
    fn the_share_lost_over_10_000_frames_is_near_the_rate() {
        for (loss, near) in [(0.05, 400..600), (0.2, 1880..2120), (0.5, 4850..5150)] {
            let lost = posts_lost(&mut lossy(7, "pax", loss), 10_000).len();

            assert!(near.contains(&lost), "{lost} of 10 000 at {loss}");
        }
    }

    #[test]
    fn a_frame_is_lost_below_its_rate_and_not_at_it() {
        // The number drawn for the first frame of this seed and name.
        let number = SplitMix64::of_member(7, "pax").next_unit();
        let first = |frame: Frame, at: f64| {
            let mut rules = Rules::new(7, "pax");
            rules.set_loss(rate(at));
            rules.set_welcome_loss(rate(at));
            rules.decide(frame, 100).map(|dropped| dropped.why)
        };

        assert_eq!(first(post(SARO), number), None);
        assert_eq!(first(post(SARO), number.next_up()), Some(Why::Loss));
        assert_eq!(first(invite(), number), None);
        assert_eq!(first(invite(), number.next_up()), Some(Why::WelcomeLoss));
    }

    #[test]
    fn a_welcome_is_not_lost_at_the_loss_rate_and_is_at_its_own() {
        let mut rules = lossy(7, "pax", 1.0);
        assert_eq!(rules.decide(invite(), 100), None);
        assert_eq!(rules.decide(welcome(SARO), 100), None);
        assert_eq!(rules.decide(post(SARO), 100).unwrap().why, Why::Loss);
        assert_eq!(
            rules.decide(candidate(SARO, 0), 100).unwrap().why,
            Why::Loss
        );
        let other = frame(Kind::Other, None);
        assert_eq!(rules.decide(other, 100).unwrap().why, Why::Loss);

        let mut rules = Rules::new(7, "pax");
        rules.set_welcome_loss(rate(1.0));
        assert_eq!(rules.decide(invite(), 100).unwrap().why, Why::WelcomeLoss);
        let lost = rules.decide(welcome(SARO), 100).unwrap();
        assert_eq!(lost.why, Why::WelcomeLoss);
        assert_eq!(rules.decide(post(SARO), 100), None);
        assert_eq!(rules.decide(candidate(SARO, 0), 100), None);
    }

    #[test]
    fn a_cut_drops_its_senders_frames_and_no_other() {
        let mut rules = Rules::new(7, "pax");
        rules.set_cut([app_id(SARO)]);

        for frame in [post(SARO), candidate(SARO, 0), welcome(SARO)] {
            assert_eq!(rules.decide(frame, 100).unwrap().why, Why::Cut);
        }
        assert_eq!(rules.decide(post(RAYA), 100), None);
        // A frame with no sender passes any cut.
        assert_eq!(rules.decide(invite(), 100), None);
        assert_eq!(rules.decide(frame(Kind::Other, None), 100), None);
    }

    #[test]
    fn a_new_cut_replaces_the_one_before_and_healed_every_frame_passes() {
        let mut rules = Rules::new(7, "pax");
        rules.set_cut([app_id(SARO)]);
        rules.set_cut([app_id(RAYA)]);
        assert_eq!(rules.cut(), &BTreeSet::from([app_id(RAYA)]));
        assert_eq!(rules.decide(post(SARO), 100), None);
        assert_eq!(rules.decide(post(RAYA), 100).unwrap().why, Why::Cut);

        rules.heal();
        assert!(rules.cut().is_empty());
        assert_eq!(rules.decide(post(RAYA), 100), None);
    }

    #[test]
    fn a_cut_does_not_move_the_draws() {
        let uncut = posts_lost(&mut lossy(7, "pax", 0.5), 200);

        let mut rules = lossy(7, "pax", 0.5);
        rules.set_cut([app_id(RAYA)]);
        let mut lost = vec![];
        for n in 1..=200 {
            // Every third frame is of the sender cut off.
            let frame = if n % 3 == 0 { post(RAYA) } else { post(SARO) };
            match rules.decide(frame, 100) {
                Some(dropped) if dropped.why == Why::Loss => lost.push(dropped.n),
                Some(dropped) => assert_eq!((dropped.why, dropped.n % 3), (Why::Cut, 0)),
                None => {}
            }
        }

        let uncut: Vec<u64> = uncut.into_iter().filter(|n| n % 3 != 0).collect();
        assert_eq!(lost, uncut);
    }

    #[test]
    fn the_first_candidate_after_arming_is_withheld_and_the_second_passes() {
        let mut rules = Rules::new(7, "pax");
        // One before arming passes.
        assert_eq!(rules.decide(candidate(SARO, 0), 100), None);

        rules.withhold_next_candidate().unwrap();
        assert_eq!(rules.withhold(), &Withhold::Armed);
        assert_eq!(rules.decide(post(SARO), 100), None);
        let withheld = rules.decide(candidate(RAYA, 1), 100).unwrap();
        assert_eq!(withheld.why, Why::Withheld);
        assert_eq!(rules.decide(candidate(SARO, 0), 100), None);
        assert_eq!(rules.decide(candidate(RAYA, 1), 100), None);
    }

    #[test]
    fn the_rule_is_armed_once() {
        let mut rules = Rules::new(7, "pax");
        rules.withhold_next_candidate().unwrap();
        assert_eq!(rules.withhold_next_candidate(), Err(NotArmed::ArmedAlready));

        rules.decide(candidate(RAYA, 1), 100).unwrap();
        assert_eq!(rules.withhold_next_candidate(), Err(NotArmed::Spent));
        assert_eq!(rules.decide(candidate(SARO, 0), 100), None);
    }

    #[test]
    fn an_own_candidate_is_not_the_one_withheld() {
        let mut rules = Rules::new(7, "pax");
        rules.add_own(app_id(SARO));
        rules.withhold_next_candidate().unwrap();

        assert_eq!(rules.decide(candidate(SARO, 0), 100), None);
        assert_eq!(rules.withhold(), &Withhold::Armed);
        assert!(rules.decide(candidate(RAYA, 1), 100).is_some());
    }

    #[test]
    fn a_candidate_the_cut_or_the_loss_drops_is_not_the_one_withheld() {
        let mut rules = Rules::new(7, "pax");
        rules.set_cut([app_id(SARO)]);
        rules.withhold_next_candidate().unwrap();
        assert_eq!(rules.decide(candidate(SARO, 0), 100).unwrap().why, Why::Cut);
        assert_eq!(rules.withhold(), &Withhold::Armed);

        let mut rules = lossy(7, "pax", 1.0);
        rules.withhold_next_candidate().unwrap();
        assert_eq!(
            rules.decide(candidate(SARO, 0), 100).unwrap().why,
            Why::Loss
        );
        assert_eq!(rules.withhold(), &Withhold::Armed);
    }

    #[test]
    fn the_drop_names_its_arrival_its_seat_its_sender_and_its_bytes() {
        let mut rules = Rules::new(7, "pax");
        rules.withhold_next_candidate().unwrap();
        rules.decide(post(SARO), 100);

        assert_eq!(
            rules.decide(candidate(RAYA, 3), 2048),
            Some(Dropped {
                n: 2,
                why: Why::Withheld,
                frame: candidate(RAYA, 3),
                bytes: 2048,
            })
        );
        assert_eq!(
            rules.withhold(),
            &Withhold::Withheld {
                sender: Some(app_id(RAYA)),
                seat: Some(3),
                applied: false,
            }
        );
    }

    #[test]
    fn a_candidate_that_names_no_seat_is_withheld_all_the_same() {
        let mut rules = Rules::new(7, "pax");
        rules.withhold_next_candidate().unwrap();
        let unseated = frame(Kind::Candidate { seat: None }, Some(RAYA));

        assert_eq!(rules.decide(unseated, 100).unwrap().why, Why::Withheld);
        assert_eq!(
            rules.withhold(),
            &Withhold::Withheld {
                sender: Some(app_id(RAYA)),
                seat: None,
                applied: false,
            }
        );
    }

    #[test]
    fn a_welcome_from_the_withheld_candidates_sender_is_noted_once() {
        let applied = |rules: &Rules| match rules.withhold() {
            Withhold::Withheld { applied, .. } => *applied,
            other => panic!("nothing is withheld: {other:?}"),
        };
        let mut rules = Rules::new(7, "pax");
        rules.withhold_next_candidate().unwrap();
        // One before anything is withheld is not noted.
        assert_eq!(rules.decide(welcome(RAYA), 100), None);
        assert_eq!(rules.withhold(), &Withhold::Armed);
        rules.decide(candidate(RAYA, 1), 100).unwrap();

        // Neither is one from another sender, nor the one sent to an inbox.
        assert_eq!(rules.decide(welcome(SARO), 100), None);
        assert_eq!(rules.decide(invite(), 100), None);
        assert!(!applied(&rules));

        for _ in 0..2 {
            assert_eq!(rules.decide(welcome(RAYA), 100), None);
            assert!(applied(&rules));
        }
    }

    #[test]
    fn a_welcome_with_no_sender_is_not_that_of_a_candidate_with_none() {
        let mut rules = Rules::new(7, "pax");
        rules.withhold_next_candidate().unwrap();
        let unstamped = frame(Kind::Candidate { seat: Some(1) }, None);
        rules.decide(unstamped, 100).unwrap();

        assert_eq!(rules.decide(invite(), 100), None);
        assert_eq!(
            rules.withhold(),
            &Withhold::Withheld {
                sender: None,
                seat: Some(1),
                applied: false,
            }
        );
    }

    #[test]
    fn own_frames_draw_nothing_and_are_not_counted() {
        let plain = posts_lost(&mut lossy(7, "pax", 0.5), 200);

        let mut rules = lossy(7, "pax", 0.5);
        rules.add_own(app_id(RAYA));
        let mut lost = vec![];
        for _ in 0..200 {
            assert_eq!(rules.decide(post(RAYA), 100), None);
            lost.extend(rules.decide(post(SARO), 100).map(|dropped| dropped.n));
        }

        assert_eq!(lost, plain);
        assert_eq!(rules.arrived(), 200);
        assert_eq!(rules.own(), &BTreeSet::from([app_id(RAYA)]));
    }

    #[test]
    fn a_rate_is_a_number_from_0_to_1() {
        for (text, rate) in [("0", 0.0), ("1", 1.0), ("0.25", 0.25), ("1e-3", 0.001)] {
            assert_eq!(text.parse::<Rate>().map(f64::from), Ok(rate), "{text:?}");
            assert_eq!(Rate::try_from(rate).map(f64::from), Ok(rate));
        }
    }

    #[test]
    fn what_is_no_rate_is_refused() {
        for rate in [-0.1, 1.1, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert_eq!(Rate::try_from(rate), Err(NotARate), "{rate}");
        }
        for text in ["", "half", "1.5", "-1", "NaN", "inf", "0,5", "50%"] {
            assert_eq!(text.parse::<Rate>(), Err(NotARate), "{text:?}");
        }
    }
}
