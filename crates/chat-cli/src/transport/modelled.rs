//! A transport behind a model of a lossy network, and the app's end of that
//! model.

use std::collections::BTreeSet;
use std::io;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crossbeam_channel::{Receiver, RecvTimeoutError, Sender, bounded, unbounded};
use logos_chat::{AddressedEnvelope, DeliveryService, Transport};
use network_model::{AppId, Dropped, Model, NotArmed, Rate, Withhold, read};
use serde::Serialize;

/// How many frames the model has let through may wait for the client: as
/// many as wait in the channel of either transport, which this one stands in
/// for.
const INBOUND_BOUND: usize = 1024;

/// What a model is started with.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Settings {
    pub seed: u64,
    pub loss: Rate,
    pub welcome_loss: Rate,
    pub delay_ms: u64,
}

/// `inner`, with what it hands the client put through a model first.
#[derive(Clone, Debug)]
pub struct Modelled<T> {
    inner: T,
    model: Arc<Mutex<Model>>,
    inbound: Option<Receiver<Vec<u8>>>,
}

impl<T: Transport> Modelled<T> {
    /// Takes the inbound stream of `inner` and starts the thread that puts
    /// it through `model`. A frame the model drops is sent on `drops`.
    fn new(mut inner: T, model: Arc<Mutex<Model>>, drops: Sender<Dropped>) -> io::Result<Self> {
        let frames = inner.inbound();
        let (client, inbound) = bounded(INBOUND_BOUND);
        thread::Builder::new().name("network-model".into()).spawn({
            let model = model.clone();
            move || deliver(frames, &model, &drops, &client)
        })?;
        Ok(Self {
            inner,
            model,
            inbound: Some(inbound),
        })
    }
}

impl<T: DeliveryService> DeliveryService for Modelled<T> {
    type Error = T::Error;

    fn publish(&mut self, envelope: AddressedEnvelope) -> Result<(), T::Error> {
        // A transport hands a client its own frames back, and a frame can be
        // back before the client says which app id it publishes under. So
        // the model learns an app id of this client's own here, ahead of the
        // frame.
        if let Some(app_id) = read(&envelope.data).sender {
            self.model.lock().unwrap().rules_mut().add_own(app_id);
        }
        self.inner.publish(envelope)
    }

    fn subscribe(&mut self, delivery_address: &str) -> Result<(), T::Error> {
        self.inner.subscribe(delivery_address)
    }
}

impl<T: Transport> Transport for Modelled<T> {
    fn inbound(&mut self) -> Receiver<Vec<u8>> {
        self.inbound
            .take()
            .expect("Modelled::inbound called more than once")
    }
}

/// Lets each frame of `frames` arrive at `model`, and hands `client` what
/// the model lets through once it is due. Ends when the client has gone, or
/// when `frames` has closed and nothing is held.
fn deliver(
    frames: Receiver<Vec<u8>>,
    model: &Mutex<Model>,
    drops: &Sender<Dropped>,
    client: &Sender<Vec<u8>>,
) {
    let start = Instant::now();
    // `None` once the transport has closed its stream.
    let mut frames = Some(frames);
    loop {
        let next_due = model.lock().unwrap().next_due();
        let wait = next_due.map(|due| due.saturating_sub(start.elapsed()));
        let arrived = match (&frames, wait) {
            (Some(frames), Some(wait)) => frames.recv_timeout(wait),
            (Some(frames), None) => frames.recv().map_err(RecvTimeoutError::from),
            (None, Some(wait)) => {
                thread::sleep(wait);
                Err(RecvTimeoutError::Timeout)
            }
            (None, None) => return,
        };
        match arrived {
            Ok(payload) => {
                let frame = read(&payload);
                let mut model = model.lock().unwrap();
                if let Some(dropped) = model.arrive(start.elapsed(), frame, payload) {
                    // Sent with the lock, so a drop the counts hold is one
                    // the app can already read. The channel has no bound,
                    // and nobody is told of a drop once the app has gone.
                    let _ = drops.send(dropped);
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => frames = None,
        }
        // Taken with the lock and sent without it: a client slow to read
        // must not keep the app from the model.
        let due = model.lock().unwrap().take_due(start.elapsed());
        for payload in due {
            if client.send(payload).is_err() {
                return;
            }
        }
    }
}

/// The app's end of a model: its rules, what it counted and what it dropped.
pub struct NetworkModel {
    settings: Settings,
    model: Arc<Mutex<Model>>,
    drops: Receiver<Dropped>,
}

impl NetworkModel {
    /// Puts `transport` behind a model of the network of `member`.
    pub fn over<T: Transport>(
        transport: T,
        member: &str,
        settings: Settings,
    ) -> io::Result<(Modelled<T>, Self)> {
        let mut model = Model::new(settings.seed, member);
        model.rules_mut().set_loss(settings.loss);
        model.rules_mut().set_welcome_loss(settings.welcome_loss);
        model.set_delay(Duration::from_millis(settings.delay_ms));
        let model = Arc::new(Mutex::new(model));
        // Unbounded, as the client's events are: the app reads both.
        let (dropped, drops) = unbounded();
        let transport = Modelled::new(transport, model.clone(), dropped)?;
        let network = Self {
            settings,
            model,
            drops,
        };
        Ok((transport, network))
    }

    /// Each frame the model drops, as it arrives.
    pub fn drops(&self) -> &Receiver<Dropped> {
        &self.drops
    }

    /// Cuts this client off from `senders`, and from no one else.
    pub fn cut(&self, senders: Vec<AppId>) {
        self.model.lock().unwrap().rules_mut().set_cut(senders);
    }

    pub fn heal(&self) {
        self.model.lock().unwrap().rules_mut().heal();
    }

    pub fn withhold_next_candidate(&self) -> Result<(), NotArmed> {
        let mut model = self.model.lock().unwrap();
        model.rules_mut().withhold_next_candidate()
    }

    pub fn state(&self) -> NetworkState {
        let model = self.model.lock().unwrap();
        let (rules, counts) = (model.rules(), model.counts());
        let hex = |ids: &BTreeSet<AppId>| ids.iter().map(AppId::to_string).collect();
        NetworkState {
            seed: self.settings.seed,
            loss: self.settings.loss.into(),
            welcome_loss: self.settings.welcome_loss.into(),
            delay_ms: self.settings.delay_ms,
            cut: hex(rules.cut()),
            own: hex(rules.own()),
            withheld: match rules.withhold() {
                Withhold::Withheld {
                    sender,
                    seat,
                    applied,
                } => Some(Withheld {
                    app_id: sender.as_ref().map(AppId::to_string),
                    seat: *seat,
                    applied: *applied,
                }),
                Withhold::Off | Withhold::Armed => None,
            },
            armed: *rules.withhold() == Withhold::Armed,
            frames: Frames {
                arrived: counts.arrived,
                passed: counts.passed,
                held: counts.held,
                dropped: DroppedFrames {
                    loss: counts.loss,
                    welcome_loss: counts.welcome_loss,
                    cut: counts.cut,
                    withheld: counts.withheld,
                },
            },
        }
    }
}

/// What a model says of itself when asked.
#[derive(Debug, Serialize)]
pub struct NetworkState {
    seed: u64,
    loss: f64,
    welcome_loss: f64,
    delay_ms: u64,
    /// The app ids this client is cut off from.
    cut: Vec<String>,
    /// The app ids this client has published under, whose frames coming
    /// back the model lets through.
    own: Vec<String>,
    /// The commit candidate the model withheld.
    withheld: Option<Withheld>,
    /// Whether the next commit candidate is withheld.
    armed: bool,
    frames: Frames,
}

#[derive(Debug, Serialize)]
struct Withheld {
    /// The app id and the seat of the steward that minted it.
    app_id: Option<String>,
    seat: Option<u32>,
    /// Whether that steward has broadcast a welcome since.
    applied: bool,
}

/// What became of the frames that arrived. This client's own are in no count.
#[derive(Debug, Serialize)]
struct Frames {
    arrived: u64,
    /// Handed to the client.
    passed: u64,
    /// Let through and not yet due.
    held: u64,
    dropped: DroppedFrames,
}

#[derive(Debug, Serialize)]
struct DroppedFrames {
    loss: u64,
    welcome_loss: u64,
    cut: u64,
    withheld: u64,
}

#[cfg(test)]
mod tests {
    use super::*;
    use network_model::Rules;
    use std::convert::Infallible;

    /// How long a wait for the thread may last before it fails the test.
    const LIMIT: Duration = Duration::from_secs(10);

    /// A transport that hands the client what a test sends it.
    #[derive(Clone, Debug)]
    struct Fake(Option<Receiver<Vec<u8>>>);

    impl DeliveryService for Fake {
        type Error = Infallible;

        fn publish(&mut self, _: AddressedEnvelope) -> Result<(), Infallible> {
            Ok(())
        }

        fn subscribe(&mut self, _: &str) -> Result<(), Infallible> {
            Ok(())
        }
    }

    impl Transport for Fake {
        fn inbound(&mut self) -> Receiver<Vec<u8>> {
            self.0.take().expect("the inbound stream is taken once")
        }
    }

    /// A fake transport behind a model that holds each frame for `delay_ms`:
    /// where a test sends frames, where the client reads them, and the app's
    /// end.
    fn modelled(delay_ms: u64) -> (Sender<Vec<u8>>, Receiver<Vec<u8>>, NetworkModel) {
        let (frames, inner) = bounded(INBOUND_BOUND);
        let settings = Settings {
            seed: 7,
            loss: Rate::default(),
            welcome_loss: Rate::default(),
            delay_ms,
        };
        let (mut transport, network) =
            NetworkModel::over(Fake(Some(inner)), "saro", settings).expect("the thread starts");
        (frames, transport.inbound(), network)
    }

    /// A payload that tells which frame it is.
    fn payload(n: u8) -> Vec<u8> {
        vec![n; 3]
    }

    #[test]
    fn frames_reach_the_client_in_the_order_they_came_and_no_sooner_than_the_delay() {
        const DELAY: Duration = Duration::from_millis(200);
        let (frames, client, network) = modelled(DELAY.as_millis() as u64);

        let sent = Instant::now();
        for n in 0..50 {
            frames.send(payload(n)).expect("the thread reads");
        }
        for n in 0..50 {
            assert_eq!(client.recv_timeout(LIMIT), Ok(payload(n)));
            assert!(sent.elapsed() >= DELAY, "{n}: {:?}", sent.elapsed());
        }
        assert_eq!(network.state().frames.passed, 50);
    }

    #[test]
    fn a_frame_the_model_drops_is_told_and_does_not_reach_the_client() {
        let (frames, client, network) = modelled(0);
        // A payload that is no envelope has no sender to cut, so the rate
        // has to drop it.
        network
            .model
            .lock()
            .unwrap()
            .rules_mut()
            .set_loss("1".parse().unwrap());

        frames.send(payload(1)).expect("the thread reads");

        let dropped = network.drops().recv_timeout(LIMIT).expect("a drop");
        assert_eq!((dropped.n, dropped.bytes), (1, 3));
        drop(frames);
        assert_eq!(
            client.recv_timeout(LIMIT),
            Err(RecvTimeoutError::Disconnected)
        );
    }

    #[test]
    fn the_thread_ends_when_the_client_goes() {
        let (frames, client, _network) = modelled(0);
        drop(client);

        // It learns that the client has gone from the frame it has for it,
        // and lets go of the transport's stream as it ends.
        let deadline = Instant::now() + LIMIT;
        while frames.send(payload(1)).is_ok() {
            assert!(Instant::now() < deadline, "the thread still reads");
            thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn a_stream_that_closes_delivers_what_is_held_and_then_closes() {
        const DELAY: Duration = Duration::from_millis(300);
        let (frames, client, _network) = modelled(DELAY.as_millis() as u64);

        let sent = Instant::now();
        frames.send(payload(1)).expect("the thread reads");
        frames.send(payload(2)).expect("the thread reads");
        drop(frames);

        assert_eq!(client.recv_timeout(LIMIT), Ok(payload(1)));
        assert!(sent.elapsed() >= DELAY, "{:?}", sent.elapsed());
        assert_eq!(client.recv_timeout(LIMIT), Ok(payload(2)));
        assert_eq!(
            client.recv_timeout(LIMIT),
            Err(RecvTimeoutError::Disconnected)
        );
    }

    #[test]
    fn a_client_that_reads_nothing_backs_up_the_transport_and_not_the_app() {
        let (frames, client, network) = modelled(0);
        let numbered = |n: usize| n.to_be_bytes().to_vec();

        // The client's channel fills, the thread waits to hand it one frame
        // more, and the rest fill the transport's channel: the last of these
        // sends returns only then.
        let sent = 2 * INBOUND_BOUND + 1;
        for n in 0..sent {
            frames
                .send_timeout(numbered(n), LIMIT)
                .expect("the thread reads");
        }
        assert!(client.is_full(), "{} wait for the client", client.len());
        assert!(frames.is_full(), "{} wait in the transport", frames.len());

        // The thread does not wait with the model's lock.
        let model = network.model.clone();
        let (unlocked, counts) = bounded(1);
        thread::spawn(move || unlocked.send(model.lock().unwrap().counts()));
        counts.recv_timeout(LIMIT).expect("the model answers");

        for n in 0..sent {
            assert_eq!(client.recv_timeout(LIMIT), Ok(numbered(n)));
        }
    }

    #[test]
    fn the_frames_dropped_are_those_the_rules_of_the_seed_and_the_name_drop() {
        let (frames, inner) = bounded(INBOUND_BOUND);
        let settings = Settings {
            seed: 11,
            loss: "0.5".parse().unwrap(),
            welcome_loss: Rate::default(),
            delay_ms: 0,
        };
        let (mut transport, network) =
            NetworkModel::over(Fake(Some(inner)), "raya", settings).expect("the thread starts");
        let client = transport.inbound();
        let mut rules = Rules::new(settings.seed, "raya");
        rules.set_loss(settings.loss);

        let mut expected = vec![];
        for n in 0..100 {
            let dropped = rules.decide(read(&payload(n)), payload(n).len());
            expected.extend(dropped.map(|dropped| dropped.n));
            frames.send(payload(n)).expect("the thread reads");
        }
        drop(frames);
        // The stream closes once every frame has been through the model.
        while client.recv_timeout(LIMIT).is_ok() {}

        let dropped: Vec<u64> = network.drops().try_iter().map(|d| d.n).collect();
        assert_eq!(dropped, expected);
        assert!(!dropped.is_empty() && dropped.len() < 100, "{dropped:?}");
    }

    #[test]
    fn a_frame_is_held_for_the_delay_however_long_the_thread_has_run() {
        const DELAY: Duration = Duration::from_millis(100);
        const IDLE: Duration = Duration::from_secs(1);
        let (frames, client, _network) = modelled(DELAY.as_millis() as u64);
        thread::sleep(IDLE);

        let sent = Instant::now();
        frames.send(payload(1)).expect("the thread reads");

        assert_eq!(client.recv_timeout(LIMIT), Ok(payload(1)));
        let held = sent.elapsed();
        assert!(held >= DELAY && held < IDLE, "{held:?}");
    }
}
