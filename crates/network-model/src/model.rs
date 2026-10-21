//! The rules, and the frames that passed them on their way to the client.

use std::collections::VecDeque;
use std::time::Duration;

use crate::frame::Frame;
use crate::rules::{Dropped, Rules, Why};

/// How many frames arrived and what became of them. A member's own frames
/// coming back are in no count.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    pub arrived: u64,
    /// Handed on by [`Model::take_due`].
    pub passed: u64,
    /// Passed by the rules and not yet due.
    pub held: u64,
    pub loss: u64,
    pub welcome_loss: u64,
    pub cut: u64,
    pub withheld: u64,
}

#[derive(Debug)]
struct Held {
    due: Duration,
    payload: Vec<u8>,
    /// Whether the frame is one the counts are of.
    counted: bool,
}

/// A lossy network on the frames one member receives: [`Rules`] that drop
/// some, and a delay on the rest. It keeps no clock: the time of each call is
/// handed to it, as a duration from whatever start its caller counts from.
#[derive(Debug)]
pub struct Model {
    rules: Rules,
    delay: Duration,
    /// In the order they arrived, which is the order they leave in: a frame
    /// due before the one ahead of it waits for it.
    held: VecDeque<Held>,
    passed: u64,
}

impl Model {
    /// The model of `member` under `seed`, with every rule off and no delay.
    pub fn new(seed: u64, member: &str) -> Self {
        Self {
            rules: Rules::new(seed, member),
            delay: Duration::ZERO,
            held: VecDeque::new(),
            passed: 0,
        }
    }

    pub fn rules(&self) -> &Rules {
        &self.rules
    }

    pub fn rules_mut(&mut self) -> &mut Rules {
        &mut self.rules
    }

    /// How long a frame that arrives from now on is held.
    pub fn set_delay(&mut self, delay: Duration) {
        self.delay = delay;
    }

    /// Takes the frame `payload` was read as, arriving at `now`. A frame the
    /// rules drop is returned, and any other is held until it is due.
    pub fn arrive(&mut self, now: Duration, frame: Frame, payload: Vec<u8>) -> Option<Dropped> {
        let counted = !self.rules.owns(&frame);
        if let Some(dropped) = self.rules.decide(frame, payload.len()) {
            return Some(dropped);
        }
        self.held.push_back(Held {
            due: now.saturating_add(self.delay),
            payload,
            counted,
        });
        None
    }

    /// When the next frame to leave is due.
    pub fn next_due(&self) -> Option<Duration> {
        self.held.front().map(|held| held.due)
    }

    /// The payloads due by `now`, in the order they arrived.
    pub fn take_due(&mut self, now: Duration) -> Vec<Vec<u8>> {
        let mut due = vec![];
        while let Some(held) = self.held.pop_front_if(|held| held.due <= now) {
            self.passed += u64::from(held.counted);
            due.push(held.payload);
        }
        due
    }

    pub fn counts(&self) -> Counts {
        Counts {
            arrived: self.rules.arrived(),
            passed: self.passed,
            held: self.held.iter().filter(|held| held.counted).count() as u64,
            loss: self.rules.dropped(Why::Loss),
            welcome_loss: self.rules.dropped(Why::WelcomeLoss),
            cut: self.rules.dropped(Why::Cut),
            withheld: self.rules.dropped(Why::Withheld),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::Kind;
    use crate::rules::tests::{RAYA, SARO, app_id, candidate, post, rate};

    const MS: Duration = Duration::from_millis(1);

    /// A payload that tells which frame it is.
    fn payload(n: u8) -> Vec<u8> {
        vec![n; 3]
    }

    #[test]
    fn with_nothing_set_every_frame_passes_at_once_and_in_order() {
        let mut model = Model::new(7, "pax");

        for n in 0..5 {
            let now = MS * u32::from(n);
            assert_eq!(model.arrive(now, post(SARO), payload(n)), None);
            assert_eq!(model.next_due(), Some(now));
            assert_eq!(model.take_due(now), [payload(n)]);
            assert_eq!(model.next_due(), None);
        }
        for n in 5..8 {
            model.arrive(5 * MS, post(SARO), payload(n));
        }
        assert_eq!(model.take_due(5 * MS), [payload(5), payload(6), payload(7)]);
    }

    #[test]
    fn a_delay_holds_a_frame_until_its_time() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);

        model.arrive(10 * MS, post(SARO), payload(1));

        assert_eq!(model.take_due(10 * MS), Vec::<Vec<u8>>::new());
        assert_eq!(model.take_due(49 * MS), Vec::<Vec<u8>>::new());
        assert_eq!(model.counts().held, 1);
        assert_eq!(model.take_due(50 * MS), [payload(1)]);
        assert_eq!(model.counts().held, 0);
    }

    #[test]
    fn the_next_due_time_is_that_of_the_frame_that_arrived_first() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);
        assert_eq!(model.next_due(), None);

        model.arrive(10 * MS, post(SARO), payload(1));
        model.arrive(25 * MS, post(SARO), payload(2));
        assert_eq!(model.next_due(), Some(50 * MS));

        assert_eq!(model.take_due(60 * MS), [payload(1)]);
        assert_eq!(model.next_due(), Some(65 * MS));
        assert_eq!(model.take_due(70 * MS), [payload(2)]);
        assert_eq!(model.next_due(), None);
    }

    #[test]
    fn frames_leave_in_the_order_they_came_when_the_delay_is_cut_short() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);
        model.arrive(10 * MS, post(SARO), payload(1));
        model.set_delay(Duration::ZERO);
        model.arrive(20 * MS, post(SARO), payload(2));

        assert_eq!(model.take_due(20 * MS), Vec::<Vec<u8>>::new());
        assert_eq!(model.take_due(50 * MS), [payload(1), payload(2)]);
    }

    #[test]
    fn a_frame_the_rules_drop_is_never_held() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);
        model.rules_mut().set_cut([app_id(RAYA)]);

        let dropped = model.arrive(10 * MS, post(RAYA), payload(1)).unwrap();

        assert_eq!((dropped.n, dropped.why, dropped.bytes), (1, Why::Cut, 3));
        assert_eq!(model.next_due(), None);
        assert_eq!(model.take_due(Duration::MAX), Vec::<Vec<u8>>::new());
    }

    #[test]
    fn own_frames_are_held_in_their_place_and_are_in_no_count() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);
        model.rules_mut().add_own(app_id(RAYA));

        model.arrive(10 * MS, post(SARO), payload(1));
        model.arrive(11 * MS, post(RAYA), payload(2));
        model.arrive(12 * MS, post(SARO), payload(3));

        assert_eq!((model.counts().arrived, model.counts().held), (2, 2));
        assert_eq!(model.take_due(51 * MS), [payload(1), payload(2)]);
        assert_eq!((model.counts().passed, model.counts().held), (1, 1));
    }

    #[test]
    fn every_frame_that_arrived_has_passed_is_held_or_was_dropped() {
        let mut model = Model::new(7, "pax");
        model.set_delay(40 * MS);
        model.rules_mut().set_loss(rate(0.3));
        model.rules_mut().set_welcome_loss(rate(0.5));
        model.rules_mut().add_own(app_id("0b"));
        model.rules_mut().withhold_next_candidate().unwrap();

        for n in 0..400u32 {
            if n == 200 {
                model.rules_mut().set_cut([app_id(RAYA)]);
            }
            let frame = match n % 5 {
                0 => post(RAYA),
                1 => candidate(SARO, 1),
                2 => Frame {
                    kind: Kind::Welcome,
                    sender: None,
                },
                3 => post("0b"),
                _ => post(SARO),
            };
            let now = MS * n;
            model.arrive(now, frame, payload(0));
            model.take_due(now);

            let counts = model.counts();
            let dropped = counts.loss + counts.welcome_loss + counts.cut + counts.withheld;
            assert_eq!(counts.arrived, counts.passed + counts.held + dropped);
        }

        let counts = model.counts();
        assert_eq!(counts.arrived, 320, "own frames are not counted");
        assert_eq!(counts.withheld, 1);
        let each = [
            counts.passed,
            counts.held,
            counts.loss,
            counts.welcome_loss,
            counts.cut,
        ];
        assert!(each.iter().all(|count| *count != 0), "{counts:?}");
    }
}
