mod app;
mod headless;
mod transport;
mod utils;

use std::fs::{File, TryLockError};
use std::net::Ipv4Addr;
use std::num::NonZeroU32;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::{Parser, ValueEnum};
use components::HttpAuthClient;
use components::basic_file_key_vault::BasicFileKeyVault;
use crossbeam_channel::Receiver;
use logos_chat::{
    AuthService, ChatClient, ClusterId, ConversationStore, DbKey, EmbeddedLogosDelivery, Event,
    GroupV2Config, Installation, LocalNetwork, LogosConfig, Network, NodeAddress, P2pConfig,
    REGISTRY_ENDPOINT, RegistrationService, RegistryPublishMode, Transport,
};
use network_model::Rate;

use app::ChatApp;
use transport::modelled::{NetworkModel, Settings};

#[derive(Copy, Clone, Debug, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum TransportKind {
    File,
    LogosDelivery,
}

#[derive(Copy, Clone, Debug, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum GroupCommit {
    /// Fast on `file`, library default on the network.
    Auto,
    /// Always use fast commit timers.
    Fast,
    /// Always use the de-mls library default timing.
    Default,
}

/// Fast GroupV2 timing so a membership change (`/add`, `/remove`) commits in
/// ~1s instead of ~60s — for local
/// demos and tests. These are the vetted values from the library's group
/// tests; they are deliberately aggressive and not appropriate for a
/// high-latency network (hence `--group-commit auto` keeps defaults there).
fn fast_group_v2_config() -> GroupV2Config {
    GroupV2Config {
        voting_delay: Duration::from_millis(50),
        consensus_timeout: Duration::from_millis(250),
        commit_batch_window: Duration::from_millis(500),
        freeze_duration: Duration::from_millis(500),
        proposal_expiration: Duration::from_millis(4000),
        ..GroupV2Config::default()
    }
}

/// Decide whether to override GroupV2 timing with fast commits: always for
/// `Fast`, never for `Default`, and — for `Auto` — only on the local file
/// transport. Each duration a flag gives stands over the set, and a
/// combination the library refuses is refused with its reason.
fn group_v2_override(cli: &Cli) -> Result<Option<GroupV2Config>> {
    let fast = match cli.group_commit {
        GroupCommit::Fast => true,
        GroupCommit::Default => false,
        GroupCommit::Auto => matches!(cli.transport, TransportKind::File),
    };
    if !fast && cli.group_timers == GroupTimers::default() {
        return Ok(None);
    }
    let mut config = if fast {
        fast_group_v2_config()
    } else {
        GroupV2Config::default()
    };
    cli.group_timers.apply(&mut config);
    config.validate()?;
    Ok(Some(config))
}

/// The durations of the group's settings, each by the name the library gives
/// it and in milliseconds. One that is given stands over that of the set
/// `--group-commit` picks.
#[derive(clap::Args, Debug, Default, PartialEq)]
struct GroupTimers {
    /// How long a member has to vote on a proposal before its vote is cast
    /// for it, in milliseconds. Below the consensus timeout.
    #[arg(long = "group-voting-delay-ms", value_name = "MS")]
    voting_delay: Option<u64>,

    /// How long a vote waits for two thirds of the members before the silent
    /// ones are counted, in milliseconds.
    #[arg(long = "group-consensus-timeout-ms", value_name = "MS")]
    consensus_timeout: Option<u64>,

    /// How long approved proposals are gathered before every steward makes a
    /// commit of them, in milliseconds.
    #[arg(long = "group-commit-batch-window-ms", value_name = "MS")]
    commit_batch_window: Option<u64>,

    /// How long the stewards' commits are gathered before one is chosen, in
    /// milliseconds. Above 0.
    #[arg(long = "group-freeze-duration-ms", value_name = "MS")]
    freeze_duration: Option<u64>,

    /// How long a round of reelection or of recovery lasts, in milliseconds.
    #[arg(long = "group-retry-window-ms", value_name = "MS")]
    retry_window: Option<u64>,

    /// How long a backup steward waits for a silent steward before it stands
    /// in for it, in milliseconds.
    #[arg(long = "group-backup-takeover-window-ms", value_name = "MS")]
    backup_takeover_window: Option<u64>,

    /// How long a proposal stays open before it expires unvoted, in
    /// milliseconds. Above the consensus timeout.
    #[arg(long = "group-proposal-expiration-ms", value_name = "MS")]
    proposal_expiration: Option<u64>,

    /// How long recovery waits for a commit made by hand before every member
    /// makes one, in milliseconds.
    #[arg(long = "group-recovery-auto-commit-delay-ms", value_name = "MS")]
    recovery_auto_commit_delay: Option<u64>,
}

impl GroupTimers {
    /// Puts each duration that was given in place of the one `config` holds.
    fn apply(&self, config: &mut GroupV2Config) {
        let given = [
            (self.voting_delay, &mut config.voting_delay),
            (self.consensus_timeout, &mut config.consensus_timeout),
            (self.commit_batch_window, &mut config.commit_batch_window),
            (self.freeze_duration, &mut config.freeze_duration),
            (self.retry_window, &mut config.retry_window),
            (
                self.backup_takeover_window,
                &mut config.backup_takeover_window,
            ),
            (self.proposal_expiration, &mut config.proposal_expiration),
        ];
        for (milliseconds, duration) in given {
            if let Some(milliseconds) = milliseconds {
                *duration = Duration::from_millis(milliseconds);
            }
        }
        if let Some(milliseconds) = self.recovery_auto_commit_delay {
            config.recovery_auto_commit_delay = Some(Duration::from_millis(milliseconds));
        }
    }
}

/// A model of a lossy network on what this client receives, off unless
/// `--network-seed` is given.
#[derive(clap::Args, Debug)]
struct NetworkFlags {
    /// Put what this client receives through a model of a lossy network. The
    /// frames it drops follow from this seed and `--name`.
    #[arg(long = "network-seed", value_name = "N")]
    seed: Option<u64>,

    /// The share of the frames received that the model loses, from 0 to 1. A
    /// welcome is lost at `--network-welcome-loss` instead.
    #[arg(
        long = "network-loss",
        value_name = "RATE",
        default_value = "0",
        requires = "seed"
    )]
    loss: Rate,

    /// The share of the welcomes received that the model loses, from 0 to 1.
    #[arg(
        long = "network-welcome-loss",
        value_name = "RATE",
        default_value = "0",
        requires = "seed"
    )]
    welcome_loss: Rate,

    /// How long the model holds each frame it lets through, in milliseconds.
    #[arg(
        long = "network-delay-ms",
        value_name = "MS",
        default_value = "0",
        requires = "seed"
    )]
    delay_ms: u64,
}

impl NetworkFlags {
    /// What the model is started with, or `None` when it is off.
    fn settings(&self) -> Option<Settings> {
        Some(Settings {
            seed: self.seed?,
            loss: self.loss,
            welcome_loss: self.welcome_loss,
            delay_ms: self.delay_ms,
        })
    }
}

#[derive(Parser, Debug)]
#[command(
    name = "chat-cli",
    about = "A chat client without a terminal: inputs on standard input, a JSON line for each \
             result and event on standard output"
)]
struct Cli {
    /// Your identity name.
    #[arg(long, short)]
    name: String,

    /// Which delivery transport to use.
    #[arg(long, value_enum, default_value_t = TransportKind::LogosDelivery)]
    transport: TransportKind,

    /// How quickly group membership changes commit. `fast` makes `/add` and
    /// `/remove` commit in ~1s (great for local demos); `default` uses
    /// production de-mls timing.
    /// `auto` (the default) picks `fast` for `--transport file` and `default`
    /// otherwise, since fast timers are too aggressive for a high-latency network.
    #[arg(long, value_enum, default_value_t = GroupCommit::Auto)]
    group_commit: GroupCommit,

    #[command(flatten)]
    group_timers: GroupTimers,

    #[command(flatten)]
    network: NetworkFlags,

    /// Data directory (used for UI state and the default SQLite path).
    #[arg(long, default_value = "tmp/chat-cli-data")]
    data: PathBuf,

    /// Where account and installation keys are kept, unencrypted. Separate from
    /// `--data`, so clearing chat state keeps your identities.
    #[arg(long, default_value = "chat-cli-keys")]
    keys: PathBuf,

    /// Override the SQLite database path (defaults to `<data>/<name>.db`).
    #[arg(long)]
    db: Option<PathBuf>,

    // ── logos-delivery transport options ──────────────────────────────────────
    /// logos-delivery network preset (e.g. `logos.test`). When omitted, the
    /// preconfigured network preset is used.
    #[arg(long)]
    preset: Option<String>,

    /// Join a local network of this cluster id instead of a preset: the node
    /// listens on `--node-ip` alone and dials the nodes `--static-node` names.
    #[arg(long, value_parser = cluster_id, conflicts_with = "preset")]
    cluster_id: Option<ClusterId>,

    /// Multiaddress of a node of the local network to dial at start, as its
    /// ready line gives it. Repeat it for each node.
    #[arg(
        long = "static-node",
        value_name = "MULTIADDR",
        requires = "cluster_id",
        conflicts_with = "preset"
    )]
    static_nodes: Vec<NodeAddress>,

    /// The address the node of a local network listens on and is dialed at.
    #[arg(
        long,
        value_name = "IPV4",
        default_value = "127.0.0.1",
        value_parser = node_ip,
        requires = "cluster_id",
        conflicts_with = "preset"
    )]
    node_ip: Ipv4Addr,

    /// The largest message the node of a local network takes, in KiB.
    #[arg(
        long,
        value_name = "KIB",
        default_value = "512",
        requires = "cluster_id",
        conflicts_with = "preset"
    )]
    max_message_kib: NonZeroU32,

    /// TCP port for the embedded logos-delivery node. When omitted, the
    /// preconfigured port is used.
    #[arg(long)]
    port: Option<u16>,

    /// The log, a file that does not exist yet.
    #[arg(long)]
    log_file: PathBuf,

    /// Override the Logos registry endpoint (account + keypackage store). When
    /// omitted, the preconfigured endpoint is used.
    /// Example: `--registry-url http://127.0.0.1:18080`.
    #[arg(long)]
    registry_url: Option<String>,

    /// How keypackage and account bundles are submitted to the store: over its
    /// HTTP POST API, or published on the delivery network for the store to
    /// pick up by subscription. Queries always use the HTTP API.
    #[arg(long, value_enum, default_value_t = RegistryPublishKind::Http)]
    registry_publish: RegistryPublishKind,
}

/// A cluster id for a local network, which is none a preset holds.
fn cluster_id(text: &str) -> Result<ClusterId, String> {
    let id: u16 = text.parse().map_err(|e| format!("{e}"))?;
    ClusterId::try_from(id).map_err(|e| e.to_string())
}

/// An address a node is dialed at, which 0.0.0.0 is not.
fn node_ip(text: &str) -> Result<Ipv4Addr, String> {
    let ip: Ipv4Addr = text.parse().map_err(|e| format!("{e}"))?;
    if ip.is_unspecified() {
        return Err("0.0.0.0 is no address to dial".into());
    }
    Ok(ip)
}

#[derive(Copy, Clone, Debug, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum RegistryPublishKind {
    Http,
    Delivery,
}

impl From<RegistryPublishKind> for RegistryPublishMode {
    fn from(kind: RegistryPublishKind) -> Self {
        match kind {
            RegistryPublishKind::Http => RegistryPublishMode::Http,
            RegistryPublishKind::Delivery => RegistryPublishMode::Delivery,
        }
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(&cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("chat-cli: {e:#}");
            ExitCode::FAILURE
        }
    }
}

fn run(cli: &Cli) -> Result<()> {
    let group_v2 = group_v2_override(cli)?;
    setup_logging(cli)?;

    let result = serve(cli, group_v2);
    if result.is_err() {
        remove_empty_log(cli);
    }
    result
}

/// A run that failed without a line in its log leaves no log, so the same
/// start can be made again.
fn remove_empty_log(cli: &Cli) {
    if std::fs::metadata(&cli.log_file).is_ok_and(|log| log.len() == 0) {
        let _ = std::fs::remove_file(&cli.log_file);
    }
}

/// Open the client on the chosen transport and run it to its end.
fn serve(cli: &Cli, group_v2: Option<GroupV2Config>) -> Result<()> {
    std::fs::create_dir_all(&cli.data).context("failed to create data directory")?;

    let db_str = db_path(cli)?;
    let _lock = lock_database(&db_str)?;

    match cli.transport {
        TransportKind::LogosDelivery => {
            let p2p_config = p2p_config(cli);

            notice(&format!(
                "Starting logos-delivery node ({})...",
                p2p_config.network
            ));
            notice("This may take a few seconds while connecting to the network.");

            let registry_url = cli.registry_url.as_deref().unwrap_or(REGISTRY_ENDPOINT);
            let mut config = LogosConfig::new(db_str, db_key());
            config.set_registry_url(registry_url);
            config.set_registry_publish_mode(cli.registry_publish.into());
            if let Some(group_v2) = group_v2 {
                // Demo/test-only fast timers; migrates once the library's
                // wallclock/timer abstraction replaces this raw config.
                #[allow(deprecated)]
                config.set_group_v2_config(group_v2);
            }

            let installation_key =
                BasicFileKeyVault::new(cli.keys.clone(), HttpAuthClient::new(registry_url))
                    .install(cli.name.as_str())?;
            let installation =
                Installation::make(installation_key.signing_key, installation_key.account);
            // The client takes the transport whole, so the node is started
            // here and a clone of it kept to ask its state.
            let node = EmbeddedLogosDelivery::start(p2p_config)
                .context("failed to start the delivery node")?;
            serve_on(config, Some(installation), node.clone(), Some(node), cli)
        }
        // The file transport is a local-only path: it reuses the Logos service
        // stack (delegate identity, HTTP registry, encrypted storage) but swaps
        // the transport in via `open_with_transport`.
        TransportKind::File => {
            let transport_dir = cli.data.join("transport");
            let transport = transport::file::FileTransport::new(&transport_dir)
                .context("failed to create file transport")?;

            let mut config = LogosConfig::new(db_str, db_key());
            if let Some(registry_url) = cli.registry_url.as_deref() {
                config.set_registry_url(registry_url);
            }
            config.set_registry_publish_mode(cli.registry_publish.into());
            if let Some(group_v2) = group_v2 {
                // Demo/test-only fast timers; migrates once the library's
                // wallclock/timer abstraction replaces this raw config.
                #[allow(deprecated)]
                config.set_group_v2_config(group_v2);
            }
            serve_on(config, None, transport, None, cli)
        }
    }
}

/// Open the client on `transport` and run it to its end, as `installation`
/// when the key vault gives one and as the database's otherwise. With
/// `--network-seed`, what the transport hands the client goes through a model
/// of the network first.
fn serve_on<T>(
    config: LogosConfig,
    installation: Option<Installation>,
    transport: T,
    node: Option<EmbeddedLogosDelivery>,
    cli: &Cli,
) -> Result<()>
where
    T: Transport + Clone,
{
    let Some(settings) = cli.network.settings() else {
        return open_and_launch(config, installation, transport, node, None, cli);
    };
    let (transport, network) = NetworkModel::over(transport, &cli.name, settings)
        .context("failed to start the network model")?;
    open_and_launch(config, installation, transport, node, Some(network), cli)
}

fn open_and_launch<T>(
    config: LogosConfig,
    installation: Option<Installation>,
    transport: T,
    node: Option<EmbeddedLogosDelivery>,
    network: Option<NetworkModel>,
    cli: &Cli,
) -> Result<()>
where
    T: Transport + Clone,
{
    let opened = match installation {
        Some(installation) => logos_chat::client_with(installation, config, transport),
        None => logos_chat::open_with_transport(config, transport),
    };
    let (client, events) = opened
        .map_err(|e| anyhow::anyhow!("{e:?}"))
        .context("failed to open chat client")?;

    if node.is_some() {
        notice("Node connected.");
    }
    launch(client, events, node, network, cli)
}

/// What the embedded node is started with: the preset or the local network
/// the command line names, and the default preset when it names neither.
fn p2p_config(cli: &Cli) -> P2pConfig {
    let mut p2p_config = P2pConfig::default();
    if let Some(port) = cli.port {
        p2p_config.port = port;
    }
    if let Some(preset) = cli.preset.as_deref() {
        p2p_config.network = Network::Preset(preset.to_string());
    }
    if let Some(cluster_id) = cli.cluster_id {
        p2p_config.network = Network::Local(LocalNetwork {
            cluster_id,
            address: cli.node_ip,
            static_nodes: cli.static_nodes.clone(),
            max_message_kib: cli.max_message_kib,
        });
    }
    p2p_config
}

/// A notice on the way up, on standard error: standard output is the lines'.
fn notice(text: &str) {
    eprintln!("{text}");
}

/// The key chat-cli encrypts its database with.
fn db_key() -> DbKey {
    // Uses a static key. Requesting a passphrase in this demo app adds too much friction.
    // A real application would derive these 32 bytes from a password prompt or reads them out of the
    // OS keychain.
    DbKey::from_encryption_key(*b"chat-cli example database key --")
}

/// Lock the database for the life of the run: two on one database would act
/// as one installation.
fn lock_database(db: &str) -> Result<File> {
    let path = format!("{db}.lock");
    let lock =
        File::create(&path).with_context(|| format!("failed to create lock file: {path}"))?;
    match lock.try_lock() {
        Ok(()) => Ok(lock),
        Err(TryLockError::WouldBlock) => {
            anyhow::bail!("another chat-cli is running on the database {db}")
        }
        Err(TryLockError::Error(e)) => {
            Err(e).with_context(|| format!("failed to lock the database: {path}"))
        }
    }
}

/// Resolve the SQLite database path: `--db` if given, else `<data>/<name>.db`.
fn db_path(cli: &Cli) -> Result<String> {
    let path = cli
        .db
        .clone()
        .unwrap_or_else(|| cli.data.join(format!("{}.db", cli.name)));
    Ok(path
        .to_str()
        .context("db path contains non-UTF-8 characters")?
        .to_string())
}

fn launch<T, R, A, S>(
    client: ChatClient<T, R, A, S>,
    events: Receiver<Event>,
    node: Option<EmbeddedLogosDelivery>,
    network: Option<NetworkModel>,
    cli: &Cli,
) -> Result<()>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    let mut app = ChatApp::new(client, events, node, network, &cli.name, &cli.data)?;
    headless::run(&mut app)
}

fn setup_logging(cli: &Cli) -> Result<()> {
    use tracing_subscriber::EnvFilter;

    // The log is read for its trace records, and an empty RUST_LOG names no
    // other filter.
    let filter = std::env::var(EnvFilter::DEFAULT_ENV)
        .ok()
        .filter(|directives| !directives.trim().is_empty())
        .and_then(|directives| EnvFilter::try_new(directives).ok())
        .unwrap_or_else(|| EnvFilter::new("warn,libchat::groupv2trace=info"));

    // A run never writes over another run's log.
    let file = File::create_new(&cli.log_file)
        .with_context(|| format!("failed to create log file: {}", cli.log_file.display()))?;
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(file)
        .init();

    Ok(())
}

#[cfg(test)]
mod tests {

    use super::*;

    const RAYA: &str =
        "/ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm65ah8ABpi9PiX79T6PthDwhmknXi3161ZKT1ZbeeX1Rd";
    const PAX: &str =
        "/ip4/127.0.0.1/tcp/4002/p2p/16Uiu2HAkwVXqMJzJLxhpLL2QtZrdU9theKgbjMcyBKCzxV1dyjUM";

    fn network(args: &[&str]) -> Network {
        let cli = Cli::parse_from(
            ["chat-cli", "--name", "saro", "--log-file", "saro.log"]
                .iter()
                .chain(args),
        );
        p2p_config(&cli).network
    }

    fn group_v2(args: &[&str]) -> Result<Option<GroupV2Config>> {
        let cli = Cli::parse_from(
            ["chat-cli", "--name", "saro", "--log-file", "saro.log"]
                .iter()
                .chain(args),
        );
        group_v2_override(&cli)
    }

    /// The durations of a group's settings, in the order of their flags.
    fn durations(config: &GroupV2Config) -> [Option<Duration>; 8] {
        [
            Some(config.voting_delay),
            Some(config.consensus_timeout),
            Some(config.commit_batch_window),
            Some(config.freeze_duration),
            Some(config.retry_window),
            Some(config.backup_takeover_window),
            Some(config.proposal_expiration),
            config.recovery_auto_commit_delay,
        ]
    }

    /// The durations the command line asks for, where it asks for any.
    fn asked(args: &[&str]) -> Option<[Option<Duration>; 8]> {
        group_v2(args)
            .expect("the library takes the timers")
            .as_ref()
            .map(durations)
    }

    fn refusal(args: &[&str]) -> String {
        group_v2(args)
            .expect_err("the library refuses the timers")
            .to_string()
    }

    fn local(static_nodes: &[&str], max_message_kib: u32) -> Network {
        Network::Local(LocalNetwork {
            cluster_id: ClusterId::try_from(77).unwrap(),
            address: Ipv4Addr::LOCALHOST,
            static_nodes: static_nodes
                .iter()
                .map(|node| node.parse().unwrap())
                .collect(),
            max_message_kib: NonZeroU32::new(max_message_kib).unwrap(),
        })
    }

    #[test]
    fn without_a_timer_flag_the_timers_are_those_of_the_set() {
        let fast = Some(durations(&fast_group_v2_config()));

        assert_eq!(asked(&["--transport", "file"]), fast);
        assert_eq!(asked(&["--transport", "logos-delivery"]), None);
        assert_eq!(
            asked(&["--transport", "file", "--group-commit", "default"]),
            None
        );
        assert_eq!(
            asked(&["--transport", "logos-delivery", "--group-commit", "fast"]),
            fast
        );
    }

    #[test]
    fn each_timer_flag_gives_its_duration_over_the_default_set() {
        let flags = [
            ("--group-voting-delay-ms", 12_345),
            ("--group-consensus-timeout-ms", 12_345),
            ("--group-commit-batch-window-ms", 12_345),
            ("--group-freeze-duration-ms", 12_345),
            ("--group-retry-window-ms", 12_345),
            ("--group-backup-takeover-window-ms", 12_345),
            ("--group-proposal-expiration-ms", 123_456),
            ("--group-recovery-auto-commit-delay-ms", 0),
        ];
        for (at, (flag, milliseconds)) in flags.into_iter().enumerate() {
            let mut expected = durations(&GroupV2Config::default());
            expected[at] = Some(Duration::from_millis(milliseconds));

            assert_eq!(
                asked(&["--group-commit", "default", flag, &milliseconds.to_string()]),
                Some(expected),
                "{flag}"
            );
        }
    }

    #[test]
    fn a_timer_flag_stands_over_the_fast_set_as_well() {
        let mut expected = durations(&fast_group_v2_config());
        expected[1] = Some(Duration::from_millis(2000));
        expected[4] = Some(Duration::from_millis(300));

        assert_eq!(
            asked(&[
                "--group-commit",
                "fast",
                "--group-consensus-timeout-ms",
                "2000",
                "--group-retry-window-ms",
                "300",
            ]),
            Some(expected)
        );
    }

    #[test]
    fn a_timer_flag_on_the_network_stands_over_the_library_default() {
        let mut expected = durations(&GroupV2Config::default());
        expected[0] = Some(Duration::from_millis(100));

        assert_eq!(
            asked(&[
                "--transport",
                "logos-delivery",
                "--group-voting-delay-ms",
                "100",
            ]),
            Some(expected)
        );
    }

    #[test]
    fn timers_the_library_refuses_are_refused_with_its_reason() {
        // The fast set has a consensus timeout of 250 ms.
        assert_eq!(
            refusal(&["--group-commit", "fast", "--group-voting-delay-ms", "250"]),
            "Invalid conversation config: voting_delay must be < consensus_timeout"
        );
        assert_eq!(
            refusal(&[
                "--group-commit",
                "fast",
                "--group-consensus-timeout-ms",
                "4000"
            ]),
            "Invalid conversation config: proposal_expiration must be > consensus_timeout"
        );
        assert_eq!(
            refusal(&[
                "--group-commit",
                "default",
                "--group-freeze-duration-ms",
                "0"
            ]),
            "Invalid conversation config: freeze_duration must be non-zero"
        );
    }

    #[test]
    fn a_timer_flag_takes_whole_milliseconds() {
        for milliseconds in ["1.5", "-1", "1s", ""] {
            let args = [
                "chat-cli",
                "--name",
                "saro",
                "--log-file",
                "saro.log",
                "--group-voting-delay-ms",
            ];
            assert!(
                Cli::try_parse_from(args.iter().chain(&[milliseconds])).is_err(),
                "{milliseconds}"
            );
        }
    }

    #[test]
    fn no_network_flag_is_the_default_preset() {
        assert_eq!(network(&[]), P2pConfig::default().network);
        assert_eq!(
            network(&["--preset", "logos.dev"]),
            Network::Preset("logos.dev".into())
        );
    }

    #[test]
    fn a_cluster_id_alone_is_a_local_network_that_dials_nobody_and_takes_512_kib() {
        assert_eq!(network(&["--cluster-id", "77"]), local(&[], 512));
    }

    #[test]
    fn a_local_network_takes_each_node_to_dial_and_the_largest_message() {
        assert_eq!(
            network(&[
                "--cluster-id",
                "77",
                "--static-node",
                RAYA,
                "--max-message-kib",
                "64",
                "--static-node",
                PAX,
            ]),
            local(&[RAYA, PAX], 64)
        );
    }

    #[test]
    fn a_node_ip_is_the_address_the_node_listens_on() {
        let Network::Local(local) = network(&["--cluster-id", "77", "--node-ip", "10.89.0.7"])
        else {
            panic!("a local network");
        };
        assert_eq!(local.address, Ipv4Addr::new(10, 89, 0, 7));
    }

    fn network_model(args: &[&str]) -> Option<Settings> {
        let start = ["chat-cli", "--name", "saro", "--log-file", "saro.log"];
        Cli::parse_from(start.iter().chain(args)).network.settings()
    }

    #[test]
    fn without_a_seed_the_network_model_is_off() {
        assert_eq!(network_model(&[]), None);
    }

    #[test]
    fn a_seed_alone_is_a_network_model_that_loses_and_holds_nothing() {
        assert_eq!(
            network_model(&["--network-seed", "7"]),
            Some(Settings {
                seed: 7,
                loss: Rate::default(),
                welcome_loss: Rate::default(),
                delay_ms: 0,
            })
        );
    }

    #[test]
    fn each_network_flag_gives_the_model_its_value() {
        assert_eq!(
            network_model(&[
                "--network-seed",
                "18446744073709551615",
                "--network-loss",
                "0.25",
                "--network-welcome-loss",
                "1",
                "--network-delay-ms",
                "1500",
            ]),
            Some(Settings {
                seed: u64::MAX,
                loss: Rate::try_from(0.25).unwrap(),
                welcome_loss: Rate::try_from(1.0).unwrap(),
                delay_ms: 1500,
            })
        );
    }
}
