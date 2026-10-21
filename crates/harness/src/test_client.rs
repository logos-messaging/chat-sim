use crate::mem_store::MemStore;
use crate::test_ident::{AcceptAllAuth, TestIdent};
use de_mls::WallClock;
use libchat::{ConversationId, Core, IdentityProvider, PayloadOutcome};
use libchat::{GroupV2Clock, GroupV2Config, MockClock};
use libchat::{ParticipantId, SignerKey};
use network_model::{AppId, Dropped, Model, read};
use std::collections::HashMap;
use std::fmt::Debug;
use std::ops::{Deref, DerefMut};
use std::time::Duration;
use tracing::{debug, info, warn};

use components::{EphemeralRegistry, LocalBroadcaster};

use crate::wakeup::{TestWakeupProvider, TestWakeupService, WakeupRecord};

type OnMessageCallback = dyn Fn(&TestClient, PayloadOutcome);
type InboundFilter = dyn FnMut(&[u8]) -> bool;

type WS = TestWakeupService;
type WP = TestWakeupProvider;

const SARO: usize = 0;
const RAYA: usize = 1;
const PAX: usize = 2;
const MIRA: usize = 3;
const KES: usize = 4;

/// How far one step of a wait moves the virtual clock.
pub(crate) const STEP: Duration = Duration::from_millis(50);

// type ClientType = CoreClient<TestIdent, LocalBroadcaster, EphemeralRegistry, WP, MemStore>;
type ClientType = Core<(
    TestIdent,
    AcceptAllAuth,
    LocalBroadcaster,
    EphemeralRegistry,
    WP,
    MemStore,
)>;

#[derive(Debug)]
pub struct ReceivedMessage<T> {
    pub convo_id: ConversationId,
    pub contents: T,
}

pub struct TestClient {
    inner: ClientType,
    account: ParticipantId,
    received_messages: Vec<ReceivedMessage<Vec<u8>>>,
    inbound_errors: Vec<String>,
    tolerate_inbound_errors: bool,
    inbound_filter: Option<Box<InboundFilter>>,
    model: Option<Model>,
    dropped: Vec<Dropped>,
}

impl TestClient {
    fn init(client: ClientType, account: ParticipantId) -> Self {
        Self {
            inner: client,
            account,
            received_messages: vec![],
            inbound_errors: vec![],
            tolerate_inbound_errors: false,
            inbound_filter: None,
            model: None,
            dropped: vec![],
        }
    }

    // Override the Deref to Inner, so that it gets cloned every time.
    pub fn signer_key(&self) -> SignerKey {
        self.inner.signer_key().clone()
    }

    /// The account group creation resolves to this client's signer.
    pub fn account(&self) -> ParticipantId {
        self.account.clone()
    }

    /// Inbound payloads this client rejected, in arrival order. Only recorded
    /// once the harness tolerates them.
    pub fn inbound_errors(&self) -> &[String] {
        &self.inbound_errors
    }

    /// Frames this client never receives: `filter` sees every payload the
    /// transport hands over, and the ones it rejects are discarded unread.
    pub fn set_inbound_filter(&mut self, filter: impl FnMut(&[u8]) -> bool + 'static) {
        self.inbound_filter = Some(Box::new(filter));
    }

    /// Puts a model of the network between the transport and this client:
    /// every payload the inbound filter lets through arrives at `model`, and
    /// the client is handed what the model lets through once it is due.
    pub fn set_model(&mut self, model: Model) {
        self.model = Some(model);
    }

    pub fn model(&self) -> Option<&Model> {
        self.model.as_ref()
    }

    pub fn model_mut(&mut self) -> Option<&mut Model> {
        self.model.as_mut()
    }

    /// The frames the model dropped, in arrival order.
    pub fn dropped(&self) -> &[Dropped] {
        &self.dropped
    }

    fn drain_outcomes(&mut self, now: Duration) -> Vec<PayloadOutcome> {
        let mut messages = vec![];
        while let Some(data) = self.inner.ds().poll() {
            if let Some(filter) = self.inbound_filter.as_mut()
                && !filter(&data)
            {
                continue;
            }
            messages.push(data);
        }
        if let Some(model) = self.model.as_mut() {
            // The transport hands a client its own frames back, and each is
            // of a conversation the client holds by the time it is.
            for app_id in own_app_ids(&self.inner) {
                model.rules_mut().add_own(app_id);
            }
            for data in messages {
                self.dropped.extend(model.arrive(now, read(&data), data));
            }
            messages = model.take_due(now);
        }

        let mut outcomes = vec![];
        for data in messages {
            let outcome = match self.inner.handle_payload(&data) {
                Ok(outcome) => outcome,
                Err(e) if self.tolerate_inbound_errors => {
                    warn!(id = ?self.signer_key(), error = ?e, "INBOUND ERROR");
                    self.inbound_errors.push(format!("{e:?}"));
                    continue;
                }
                Err(e) => panic!("{:?} rejected an inbound payload: {e:?}", self.signer_key()),
            };
            warn!(id= ?self.signer_key(),?outcome, "DRAIN CLIENT");
            // Copy Convo Messages to received buffer

            match &outcome {
                PayloadOutcome::Empty => continue,
                PayloadOutcome::Convo(convo_outcome) => {
                    if let Some(data) = &convo_outcome.content {
                        info!(
                            content = String::from_utf8_lossy(&data.bytes).to_string(),
                            "COT"
                        );
                        self.received_messages.push(ReceivedMessage {
                            convo_id: convo_outcome.convo_id.clone(),
                            contents: data.bytes.clone(),
                        });
                    }
                }
                PayloadOutcome::Inbox(_) => {}
            }

            if !matches!(outcome, PayloadOutcome::Empty) {
                outcomes.push(outcome);
            }
        }
        outcomes
    }

    /// Poll and discard every payload waiting for this client — simulates
    /// frames the transport never delivered.
    pub fn drop_pending_payloads(&mut self) {
        let ds = self.inner.ds();
        while ds.poll().is_some() {}
    }

    pub fn received_messages(&self) -> &[ReceivedMessage<Vec<u8>>] {
        &self.received_messages
    }

    pub fn check(&self, convo_id: &str, content: &[u8]) -> bool {
        for msg in &self.received_messages {
            if msg.convo_id == convo_id && msg.contents == content {
                return true;
            }
        }
        false
    }

    pub fn convo_count(&self) -> usize {
        self.list_all_conversations().map_or(0, |v| v.len())
    }
}

/// The app ids `client` stamps on the frames it publishes, one for each
/// conversation it holds that stamps one.
fn own_app_ids(client: &ClientType) -> Vec<AppId> {
    let convos = client
        .list_all_conversations()
        .expect("a client lists its conversations");
    // A conversation only the store holds is a GroupV1 one, which stamps none.
    let app_ids = convos
        .iter()
        .filter_map(|convo| client.app_id(convo).ok().flatten());
    app_ids
        .filter_map(|app_id| AppId::try_from(app_id).ok())
        .collect()
}

impl Deref for TestClient {
    type Target = ClientType;

    fn deref(&self) -> &Self::Target {
        &self.inner
    }
}

impl DerefMut for TestClient {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.inner
    }
}

#[allow(unused)]
pub struct Observation {
    ident: SignerKey,
    outcome: PayloadOutcome,
}

#[allow(unused)]
pub struct Harness {
    addresses: HashMap<usize, SignerKey>,
    /// Each client, `None` once it is retired.
    clients: Vec<Option<TestClient>>,
    auth: AcceptAllAuth,
    wakeup_service: WS,
    cb: Box<OnMessageCallback>,
    // List of outcomes that were detected across all clients.
    pub observed_outcomes: Vec<Observation>,
}

/// A [`Harness`] of `N` clients under the tests' names and fast settings, on
/// a clock at zero.
pub struct TestHarness<const N: usize>(Harness);

impl<const N: usize> Deref for TestHarness<N> {
    type Target = Harness;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl<const N: usize> DerefMut for TestHarness<N> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}

impl<const N: usize> TestHarness<N> {
    pub fn new(cb: impl Fn(&TestClient, PayloadOutcome) + 'static) -> Self {
        const { assert!(N > 0, "TestHarness requires at least one client") };
        let names = (0..N).map(Harness::names).collect();
        Self(Harness::new(
            names,
            fast_group_v2_config(),
            MockClock::new(),
            cb,
        ))
    }
}

impl Harness {
    /// One client for each name, in that order, each under `config` and on
    /// `clock`.
    pub fn new(
        names: Vec<String>,
        config: GroupV2Config,
        clock: MockClock,
        cb: impl Fn(&TestClient, PayloadOutcome) + 'static,
    ) -> Self {
        assert!(!names.is_empty(), "Harness requires at least one client");

        let mut clients = vec![];
        let mut addresses = HashMap::new();

        let ds = LocalBroadcaster::new();
        let rs = EphemeralRegistry::new();
        let ws = TestWakeupService::on(clock);
        let auth = AcceptAllAuth::default();

        for (i, name) in names.into_iter().enumerate() {
            let wp = ws.new_provider(i);
            let ident = TestIdent::new(name);

            addresses.insert(i, ident.signer_key().clone());
            auth.register(&ident);
            let account = ident.participant_id();
            let mut core_client = ClientType::new_with_name(
                ident,
                auth.clone(),
                ds.clone(),
                rs.clone(),
                wp,
                MemStore::new(),
            )
            .unwrap();
            core_client.set_group_v2_clock(GroupV2Clock::Mock(ws.clock()));
            core_client.set_group_v2_config(config.clone());

            let client = TestClient::init(core_client, account);

            clients.push(Some(client));
        }

        debug!(?rs, "registry");

        Self {
            addresses,
            clients,
            auth,
            wakeup_service: ws,
            cb: Box::new(cb),
            observed_outcomes: vec![],
        }
    }

    pub fn client(&mut self, i: usize) -> &TestClient {
        let client = self.clients[i].as_ref();
        client.unwrap_or_else(|| panic!("client {i} is retired"))
    }

    pub fn client_mut(&mut self, i: usize) -> &mut TestClient {
        let client = self.clients[i].as_mut();
        client.unwrap_or_else(|| panic!("client {i} is retired"))
    }

    /// Drops client `i` and all it holds. It takes no payload and no wakeup
    /// from then on, and reaching it panics.
    pub fn retire(&mut self, i: usize) {
        self.clients[i] = None;
    }

    /// The auth every client shares; what it resolves, all of them resolve.
    pub fn auth(&self) -> &AcceptAllAuth {
        &self.auth
    }

    /// Lets a client keep running when it rejects an inbound payload, the way
    /// a production client does with `Event::InboundError`, and records what
    /// it rejected. Without this a rejection fails the test where it happens.
    pub fn tolerate_inbound_errors(&mut self) {
        for client in self.clients.iter_mut().flatten() {
            client.tolerate_inbound_errors = true;
        }
    }

    fn names(i: usize) -> String {
        match i {
            SARO => "saro".into(),
            RAYA => "raya".into(),
            PAX => "pax".into(),
            MIRA => "mira".into(),
            KES => "kes".into(),
            n => format!("member{n:02}"),
        }
    }

    pub fn process(&mut self, duration: Duration) {
        self.process_payloads();

        let records = self.wakeup_service.advance_time(duration);
        self.process_records(records);
    }
}

impl<const N: usize> TestHarness<N> {
    pub fn process_until(&mut self, predicate: impl Fn(&mut TestHarness<N>) -> bool) {
        self.process_until_label("none", predicate);
    }

    pub fn process_until_label(
        &mut self,
        label: &str,
        predicate: impl Fn(&mut TestHarness<N>) -> bool,
    ) {
        info!(label, "Process Until");
        let timeout = Duration::from_mins(1);
        let step = STEP;
        let mut elapsed = Duration::ZERO;

        while !predicate(self) {
            if elapsed >= timeout {
                panic!(
                    "processing timed out after {:?} with label {:?}",
                    timeout, label
                );
            }
            self.process(step);
            elapsed += step;
        }
    }
}

impl Harness {
    fn process_payloads(&mut self) {
        // Process existing payloads for all clients.
        let now = self.wakeup_service.clock().now();
        for client in self.clients.iter_mut().flatten() {
            for outcome in client.drain_outcomes(now) {
                info!(id = ?client.signer_key(), ?outcome, "Process drain");
                self.observed_outcomes.push(Observation {
                    ident: client.signer_key().clone(),
                    outcome: outcome.clone(),
                });
                info!(id = ?client.signer_key(), ?outcome, "Process drain");
                (self.cb)(client, outcome)
            }
        }
    }

    fn process_records(&mut self, records: Vec<WakeupRecord>) {
        for record in records {
            if let Some(client) = self.clients[record.client_index].as_mut() {
                client
                    .wakeup(&record.convo_id)
                    .expect("Error During wakeup");
            }
        }
    }
}

// Avoid Developer confusion by gating access functions
// based on the number of clients in the harness

impl TestHarness<1> {
    pub fn saro(&mut self) -> &mut TestClient {
        self.client_mut(SARO)
    }
}

impl TestHarness<2> {
    pub fn saro(&mut self) -> &mut TestClient {
        self.client_mut(SARO)
    }

    pub fn raya(&mut self) -> &mut TestClient {
        self.client_mut(RAYA)
    }
}

impl TestHarness<3> {
    pub fn saro(&mut self) -> &mut TestClient {
        self.client_mut(SARO)
    }

    pub fn raya(&mut self) -> &mut TestClient {
        self.client_mut(RAYA)
    }

    pub fn pax(&mut self) -> &mut TestClient {
        self.client_mut(PAX)
    }
}

impl TestHarness<4> {
    pub fn saro(&mut self) -> &mut TestClient {
        self.client_mut(SARO)
    }

    pub fn raya(&mut self) -> &mut TestClient {
        self.client_mut(RAYA)
    }

    pub fn pax(&mut self) -> &mut TestClient {
        self.client_mut(PAX)
    }

    pub fn mira(&mut self) -> &mut TestClient {
        self.client_mut(MIRA)
    }
}

/// Millisecond GroupV2 timers for virtual-time tests — the production
/// defaults converge too slowly for the harness's step sizes.
pub(crate) fn fast_group_v2_config() -> GroupV2Config {
    GroupV2Config {
        voting_delay: Duration::from_millis(50),
        consensus_timeout: Duration::from_millis(250),
        commit_batch_window: Duration::from_millis(500),
        freeze_duration: Duration::from_millis(500),
        proposal_expiration: Duration::from_millis(4000),
        ..GroupV2Config::default()
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_() {
        let _ = tracing_subscriber::fmt()
            .with_max_level(tracing::Level::INFO)
            .with_test_writer()
            .try_init();

        let mut harness = TestHarness::<2>::new(|client, outcome| {
            info!( id=?&client.signer_key(), outcome = ?outcome, "Result");
        });

        //Create Convo
        let particpants = &[harness.raya().account()];
        let convo_id = harness
            .saro()
            .create_group_convo(particpants)
            .expect("saro create group");

        harness.process_until_label("Raya Join", |h| h.raya().convo_count() == 1);

        assert_eq!(harness.raya().convo_count(), 1, "raya did not join");

        harness
            .saro()
            .send_content(convo_id.as_str(), b"Hello")
            .expect("raya send");

        harness.process(Duration::from_millis(200));

        assert!(harness.raya().check(&convo_id, b"Hello"))
    }
}
