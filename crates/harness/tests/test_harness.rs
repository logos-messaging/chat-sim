//! The harness itself: how many clients it holds, the settings and the clock
//! it is built with, and the model of a network between a client and the
//! frames it receives.
//!
//! ```sh
//! cargo test -p harness --test test_harness
//! ```

use harness::{Harness, TestHarness};
use libchat::{GroupV2Config, MockClock};
use network_model::{Dropped, Model, Rules, read};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

/// How far the tests move the virtual clock between two looks at a client.
const STEP: Duration = Duration::from_millis(50);

/// How many members `client` lists in the group.
fn listed(harness: &mut Harness, convo: &str, client: usize) -> usize {
    harness
        .client(client)
        .group_signers(convo)
        .map_or(0, |m| m.len())
}

/// A harness has room for as many clients as it is asked for. Seating all 65
/// takes two minutes in a test build, so the last one is seated beside the
/// first.
#[test]
fn a_harness_of_65_clients_seats_its_last() {
    const N: usize = 65;
    let mut harness = TestHarness::<N>::new(|_, _| {});
    let last = harness.client(N - 1).signer_key();

    let convo = harness
        .client_mut(0)
        .create_group_convo_v2(&[], "", "")
        .expect("saro create");
    harness
        .client_mut(0)
        .group_add_signers(&convo, &[last])
        .expect("saro add");

    harness.process_until_label("the last client joins", |h| {
        [0, N - 1].into_iter().all(|i| listed(h, &convo, i) == 2)
    });
}

#[test]
#[should_panic(expected = "Harness requires at least one client")]
fn a_harness_of_no_clients_is_refused() {
    Harness::new(
        vec![],
        GroupV2Config::default(),
        MockClock::new(),
        |_, _| {},
    );
}

/// A retired client holds nothing and takes nothing: the group removes it
/// without it, and the others go on.
#[test]
fn the_others_go_on_without_a_retired_client() {
    let mut harness = TestHarness::<3>::new(|_, _| {});
    let invited = [harness.raya().signer_key(), harness.pax().signer_key()];
    let convo = harness
        .saro()
        .create_group_convo_v2(&[], "", "")
        .expect("saro create");
    harness
        .saro()
        .group_add_signers(&convo, &invited)
        .expect("saro add");
    harness.process_until_label("all three join", |h| {
        (0..3).all(|i| listed(h, &convo, i) == 3)
    });

    harness.retire(2);
    harness
        .saro()
        .group_remove_signers(&convo, &[&invited[1]])
        .expect("saro remove");

    harness.process_until_label("pax is removed", |h| {
        (0..2).all(|i| listed(h, &convo, i) == 2)
    });
}

#[test]
#[should_panic(expected = "client 2 is retired")]
fn a_retired_client_is_never_reached() {
    let mut harness = TestHarness::<3>::new(|_, _| {});
    harness.retire(2);
    harness.client(2);
}

/// Each client in turn creates a group and adds the next one to it. The
/// creator is the group's only steward, and a steward commits an add once the
/// commit batch window of its settings has passed on its clock.
#[test]
fn every_client_is_under_the_settings_and_on_the_clock_it_was_built_with() {
    let names = ["saro", "raya", "pax"].map(String::from);
    // The library's own settings, whose window the tests' fast settings cut
    // to half a second.
    let config = GroupV2Config::default();
    let window = config.commit_batch_window;
    let clock = MockClock::new();
    clock.advance(Duration::from_secs(1_791_817_445));
    let mut harness = Harness::new(names.to_vec(), config, clock.clone(), |_, _| {});

    for creator in 0..names.len() {
        let joiner = harness.client((creator + 1) % names.len()).signer_key();
        let convo = harness
            .client_mut(creator)
            .create_group_convo_v2(&[], "", "")
            .unwrap_or_else(|e| panic!("{} create: {e:?}", names[creator]));
        harness
            .client_mut(creator)
            .group_add_signers(&convo, &[joiner])
            .unwrap_or_else(|e| panic!("{} add: {e:?}", names[creator]));
        let added_at = clock.now();

        while listed(&mut harness, &convo, creator) < 2 {
            assert!(
                clock.now() - added_at < 10 * window,
                "{} seats no one",
                names[creator]
            );
            harness.process(STEP);
        }
        let took = clock.now() - added_at;
        assert!(took >= window, "{} took {took:?}", names[creator]);
    }
}

/// Raya's model loses frames at a rate, and rules of the same seed and name
/// are shown every frame the model is: the inbound filter sees each payload
/// on its way to the model. Raya is handed her own frames too, which the
/// rules take for her own as her model does.
#[test]
fn a_model_drops_what_its_rules_say_of_the_frames_it_is_handed() {
    const SEED: u64 = 7;
    let rate = "0.3".parse().expect("a rate");
    let mut harness = TestHarness::<3>::new(|_, _| {});
    // A member that misses frames rejects some of those that follow.
    harness.tolerate_inbound_errors();

    let mut model = Model::new(SEED, "raya");
    model.rules_mut().set_loss(rate);
    harness.raya().set_model(model);
    let handed = Rc::new(RefCell::new(Vec::<Vec<u8>>::new()));
    harness.raya().set_inbound_filter({
        let handed = handed.clone();
        move |payload| {
            handed.borrow_mut().push(payload.to_vec());
            true
        }
    });

    let convo = harness
        .saro()
        .create_group_convo_v2(&[], "", "")
        .expect("saro create");
    let invited = [harness.raya().signer_key(), harness.pax().signer_key()];
    harness
        .saro()
        .group_add_signers(&convo, &invited)
        .expect("saro add");
    for _ in 0..60 {
        harness.process(STEP);
        // A post is a frame for Raya whatever has become of the group, and
        // one that is refused is none.
        let _ = harness.saro().send_content(&convo, b"from saro");
        let _ = harness.pax().send_content(&convo, b"from pax");
    }

    let mut rules = Rules::new(SEED, "raya");
    rules.set_loss(rate);
    for app_id in harness.raya().app_ids() {
        rules.add_own(app_id);
    }
    let handed = handed.take();
    let dropped_beside: Vec<Dropped> = handed
        .iter()
        .filter_map(|payload| rules.decide(read(payload), payload.len()))
        .collect();

    let raya = harness.raya();
    assert!(!raya.dropped().is_empty(), "no frame was lost");
    assert_eq!(raya.dropped(), dropped_beside);
    let counts = raya.model().expect("a model").counts();
    assert_eq!(counts.loss, raya.dropped().len() as u64);
    assert_eq!(counts.arrived, counts.passed + counts.held + counts.loss);
}

/// A frame is held from the pass it arrives in: the first pass after it is
/// published, which starts at the time of the send. The pass that hands it
/// over is the first to start once the delay has passed.
#[test]
fn a_delay_holds_a_post_until_the_step_it_is_due_at() {
    for delay in [0, 100, 120].map(Duration::from_millis) {
        let mut harness = TestHarness::<2>::new(|_, _| {});
        let raya = harness.raya().signer_key();
        let convo = harness
            .saro()
            .create_group_convo_v2(&[], "", "")
            .expect("saro create");
        harness
            .saro()
            .group_add_signers(&convo, &[raya])
            .expect("saro add");
        harness.process_until_label("raya joins", |h| listed(h, &convo, 1) == 2);
        // What the join left on the wire is not held.
        harness.process(STEP);

        let mut model = Model::new(0, "raya");
        model.set_delay(delay);
        harness.raya().set_model(model);
        harness
            .saro()
            .send_content(&convo, b"held")
            .expect("saro send");

        // The passes that started before the one that handed the post over.
        let mut passes = 0;
        loop {
            harness.process(STEP);
            if harness.raya().check(&convo, b"held") {
                break;
            }
            passes += 1;
            assert!(passes < 100, "{delay:?}: the post is not handed over");
        }
        let held = STEP * passes;
        assert!(
            delay <= held && held < delay + STEP,
            "{delay:?}: held {held:?}"
        );
    }
}
