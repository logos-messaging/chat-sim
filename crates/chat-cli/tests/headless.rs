//! Headless mode as a program drives it: the built binary with pipes for
//! standard input and output, the file transport in a directory of the test's
//! own, and the mock registry on a port of its own. The tests of the delivery
//! node run it on a local network, which stays on loopback.
//!
//! Every line read from a peer's standard output is parsed as JSON, and
//! [`Peer::finish`] reads that output to its end.

use std::collections::VecDeque;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Output, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;
use tempfile::TempDir;

/// How long a wait for a line may last before it fails the test. Longer than
/// the client's patience with the registry (five tries of 10 s, and up to 3 s
/// of backoff between them), so a step that fails there fails with the
/// client's error.
const LIMIT: Duration = Duration::from_secs(60);

/// How long a node of a local network waits for its dials before it is ready
/// without them.
const DIAL_WAIT: Duration = Duration::from_secs(3);

const REPOSITORY: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");

/// What some reader takes for the end of a line, beside the line feed that
/// ends each.
const LINE_BREAKS: [char; 9] = [
    '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
];

/// The lines of `source`, read on a thread so that a wait for one has a limit.
fn lines(source: impl Read + Send + 'static) -> Receiver<io::Result<String>> {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(source).lines() {
            if tx.send(line).is_err() {
                return;
            }
        }
    });
    rx
}

/// The mock registry, on a port the system picked.
struct Registry {
    process: Child,
    url: String,
}

impl Registry {
    fn start() -> Self {
        let mut process = Command::new("python3")
            .arg(format!("{REPOSITORY}/.github/scripts/mock-registry.py"))
            .args(["--port", "0"])
            .stdout(Stdio::piped())
            .spawn()
            .expect("python3 starts the mock registry");
        let stdout = lines(process.stdout.take().expect("piped"));
        let mut registry = Self {
            process,
            url: String::new(),
        };
        registry.url = stdout
            .recv_timeout(LIMIT)
            .expect("the mock registry prints its address")
            .expect("as UTF-8");
        registry
    }
}

impl Drop for Registry {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
    }
}

/// A directory and a registry of one test's own.
struct Rig {
    dir: TempDir,
    registry: Registry,
}

impl Rig {
    fn new() -> Self {
        Self {
            dir: TempDir::new().expect("a directory for the test"),
            registry: Registry::start(),
        }
    }

    fn data(&self) -> PathBuf {
        self.dir.path().join("data")
    }

    fn log(&self, name: &str) -> PathBuf {
        self.dir.path().join(format!("{name}.log"))
    }

    /// chat-cli named `name`, on the rig's transport and registry.
    /// The log file is the caller's to add.
    fn chat_cli(&self, name: &str) -> Command {
        self.chat_cli_on("file", name)
    }

    /// [`Self::chat_cli`], on the transport named.
    fn chat_cli_on(&self, transport: &str, name: &str) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_chat-cli"));
        command
            .args(["--transport", transport, "--name", name])
            .arg("--data")
            .arg(self.data())
            .arg("--keys")
            .arg(self.dir.path().join("keys"))
            .arg("--registry-url")
            .arg(&self.registry.url)
            .env_remove("RUST_LOG");
        command
    }

    /// Start a peer and read its first line, which says it is ready.
    fn peer(&self, name: &'static str) -> Peer {
        self.peer_with(name, |_| {})
    }

    /// [`Self::peer`], with `adjust` applied to the command before it starts.
    fn peer_with(&self, name: &'static str, adjust: impl FnOnce(&mut Command)) -> Peer {
        let mut peer = self.start(name, adjust);
        peer.read_ready();
        peer
    }

    /// Peers started at the same instant, as a driver starts many, each with
    /// its first line read.
    fn peers<const N: usize>(&self, names: [&'static str; N]) -> [Peer; N] {
        let mut peers = names.map(|name| self.start(name, |_| {}));
        for peer in &mut peers {
            peer.read_ready();
        }
        peers
    }

    /// Start a peer whose delivery node is on a local network and read its
    /// first line. `args` say what its node is given beside the cluster id.
    /// On a node the group timers are the slow ones unless asked otherwise.
    fn node_peer(&self, name: &'static str, args: &[&str]) -> Peer {
        let mut command = self.chat_cli_on("logos-delivery", name);
        command
            .arg("--log-file")
            .arg(self.log(name))
            .args(["--group-commit", "fast", "--cluster-id", "77"])
            .args(args);
        let mut peer = self.spawn(name, command);
        peer.read_ready();
        peer
    }

    /// Start a peer, with nothing read from it yet.
    fn start(&self, name: &'static str, adjust: impl FnOnce(&mut Command)) -> Peer {
        let mut command = self.chat_cli(name);
        command.arg("--log-file").arg(self.log(name));
        adjust(&mut command);
        self.spawn(name, command)
    }

    fn spawn(&self, name: &'static str, mut command: Command) -> Peer {
        let stderr = self.dir.path().join(format!("{name}.stderr"));
        let mut process = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(&stderr).expect("a file for standard error"))
            .spawn()
            .expect("chat-cli starts");
        Peer {
            name,
            stdin: process.stdin.take(),
            lines: lines(process.stdout.take().expect("piped")),
            process,
            stderr,
            read: Vec::new(),
            unclaimed: VecDeque::new(),
            inputs: 0,
            address: String::new(),
            installation: String::new(),
            node_address: String::new(),
        }
    }
}

/// A headless chat-cli and what it has written.
struct Peer {
    name: &'static str,
    process: Child,
    stdin: Option<ChildStdin>,
    lines: Receiver<io::Result<String>>,
    stderr: PathBuf,
    /// Every line read so far, in order.
    read: Vec<Value>,
    /// The lines read that no wait has claimed yet.
    unclaimed: VecDeque<Value>,
    inputs: u64,
    address: String,
    installation: String,
    /// Empty on a transport without a delivery node.
    node_address: String,
}

impl Peer {
    /// Fail the test with what the peer wrote so far.
    fn fail(&self, why: &str) -> ! {
        panic!(
            "{}: {why}\nstandard output so far: {:#?}\nstandard error: {}",
            self.name,
            self.read,
            fs::read_to_string(&self.stderr).unwrap_or_default(),
        )
    }

    /// Read the first line, which says the peer is ready.
    fn read_ready(&mut self) {
        let deadline = Instant::now() + LIMIT;
        let ready = self
            .read_line(deadline)
            .unwrap_or_else(|| self.fail("no first line"));
        if ready["type"] != "ready" {
            self.fail("the first line is not the ready line");
        }
        self.address = text(&ready["address"]);
        self.installation = text(&ready["installation"]);
        self.node_address = text(&ready["node_address"]);
    }

    /// The next line of standard output, or `None` at its end.
    fn read_line(&mut self, deadline: Instant) -> Option<Value> {
        let wait = deadline.saturating_duration_since(Instant::now());
        let line = match self.lines.recv_timeout(wait) {
            Ok(line) => line.unwrap_or_else(|e| self.fail(&format!("unreadable output: {e}"))),
            Err(RecvTimeoutError::Disconnected) => return None,
            Err(RecvTimeoutError::Timeout) => self.fail(&format!("no line within {LIMIT:?}")),
        };
        let parsed: Value = serde_json::from_str(&line)
            .unwrap_or_else(|e| self.fail(&format!("not a JSON line ({e}): {line:?}")));
        if !parsed["type"].is_string() {
            self.fail(&format!("a line without a type: {line:?}"));
        }
        if line.contains(LINE_BREAKS) {
            self.fail(&format!("a line some reader would break: {line:?}"));
        }
        self.read.push(parsed.clone());
        Some(parsed)
    }

    /// The first unclaimed line `wanted` accepts, waiting for it up to [`LIMIT`].
    fn wait_for(&mut self, what: &str, wanted: impl Fn(&Value) -> bool) -> Value {
        if let Some(at) = self.unclaimed.iter().position(&wanted) {
            return self.unclaimed.remove(at).expect("in range");
        }
        let deadline = Instant::now() + LIMIT;
        loop {
            let Some(line) = self.read_line(deadline) else {
                self.fail(&format!("standard output ended before {what}"));
            };
            if wanted(&line) {
                return line;
            }
            self.unclaimed.push_back(line);
        }
    }

    fn event(&mut self, kind: &str) -> Value {
        self.wait_for(&format!("a {kind} line"), |line| line["type"] == kind)
    }

    /// Write `bytes` to standard input as they are.
    fn write(&mut self, bytes: &[u8]) {
        let stdin = self.stdin.as_mut().expect("standard input is open");
        stdin
            .write_all(bytes)
            .and_then(|()| stdin.flush())
            .unwrap_or_else(|e| self.fail(&format!("standard input is closed: {e}")));
    }

    /// Write one input line and return its result line.
    fn input(&mut self, line: &str) -> Value {
        self.input_bytes(line.as_bytes())
    }

    /// [`Self::input`], for a line that need not be text.
    fn input_bytes(&mut self, line: &[u8]) -> Value {
        self.write(&[line, b"\n"].concat());
        self.inputs += 1;
        let number = self.inputs;
        self.wait_for(&format!("the result of input {number}"), |line| {
            line["type"] == "result" && line["input"] == number
        })
    }

    /// An input that has to work.
    fn run(&mut self, line: &str) -> Value {
        let result = self.input(line);
        if result["ok"] != true {
            self.fail(&format!("{line:?} did not work: {result}"));
        }
        result
    }

    /// End standard input and read standard output to its end.
    fn end_input(&mut self) {
        drop(self.stdin.take());
        let deadline = Instant::now() + LIMIT;
        while self.read_line(deadline).is_some() {}
    }

    /// End standard input, read standard output to its end, and return how
    /// the process ended.
    fn finish(mut self) -> ExitStatus {
        self.end_input();
        self.process.wait().expect("chat-cli was started")
    }
}

impl Drop for Peer {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
    }
}

fn text(value: &Value) -> String {
    value.as_str().unwrap_or_default().to_string()
}

/// 32 bytes in lowercase hex, as an account address and an installation are
/// written.
fn is_key(value: &Value) -> bool {
    value.as_str().is_some_and(|key| {
        key.len() == 64 && key.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// `creator` makes a group and adds every joiner in one `/add`. Returns the
/// conversation id once each joiner has its joined line.
fn group(creator: &mut Peer, joiners: &mut [&mut Peer]) -> String {
    let convo = text(&creator.run("/new weekend")["convo"]);
    let addresses: Vec<&str> = joiners.iter().map(|peer| peer.address.as_str()).collect();
    creator.run(&format!("/add {}", addresses.join(" ")));
    for joiner in joiners {
        let joined = joiner.event("joined");
        assert_eq!(joined["convo"], convo.as_str());
        assert_eq!(joined["kind"], "group");
    }
    convo
}

/// The next post in `convo` that `reader` gets.
fn read_post(reader: &mut Peer, convo: &str) -> Value {
    reader.wait_for("a post", |line| {
        line["type"] == "message" && line["convo"] == convo
    })
}

/// The trace records in a log.
fn trace_records(log: &Path) -> usize {
    fs::read_to_string(log)
        .expect("the log is there")
        .lines()
        .filter(|line| line.contains("libchat::groupv2trace"))
        .count()
}

/// A log line without the escape sequences that colour it.
fn plain(line: &str) -> String {
    let mut plain = String::new();
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            chars.find(|end| *end == 'm');
        } else {
            plain.push(c);
        }
    }
    plain
}

/// The number a log line gives for `field`, as `field=<number>`.
fn logged(line: &str, field: &str) -> usize {
    let line = plain(line);
    let (_, value) = line
        .split_once(&format!(" {field}="))
        .unwrap_or_else(|| panic!("no {field} in the line: {line}"));
    let digits = value.split(' ').next().expect("a first word");
    digits
        .parse()
        .unwrap_or_else(|e| panic!("{field} is no number ({e}): {line}"))
}

/// Ask `peer` for its node's state until `wanted` accepts it, up to [`LIMIT`].
fn node_until(peer: &mut Peer, what: &str, wanted: impl Fn(&Value) -> bool) -> Value {
    let deadline = Instant::now() + LIMIT;
    loop {
        let node = peer.run("/node")["node"].clone();
        if wanted(&node) {
            return node;
        }
        if Instant::now() >= deadline {
            peer.fail(&format!("its node never had {what}: {node}"));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

/// What clap leaves of a command line it refuses: its error on standard error.
fn refused_by_clap(command: &mut Command) -> String {
    let output = command.output().expect("chat-cli starts");
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    assert!(output.stdout.is_empty(), "{output:?}");
    String::from_utf8(output.stderr).expect("UTF-8")
}

/// What a start that is refused leaves: a status, nothing on standard output
/// and one line on standard error.
fn refused(output: &Output) -> String {
    assert!(!output.status.success(), "{output:?}");
    assert!(output.stdout.is_empty(), "{output:?}");
    let stderr = String::from_utf8(output.stderr.clone()).expect("UTF-8");
    assert_eq!(stderr.lines().count(), 1, "{output:?}");
    stderr
}

#[test]
fn the_first_line_says_the_client_is_ready() {
    let rig = Rig::new();
    let saro = rig.peer("saro");

    let ready = &saro.read[0];
    assert_eq!(ready["type"], "ready");
    assert_eq!(ready["name"], "saro");
    assert!(is_key(&ready["address"]), "{ready}");
    assert!(is_key(&ready["installation"]), "{ready}");

    assert!(saro.finish().success());
}

#[test]
fn a_result_answers_every_input_by_its_number() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let created = saro.input("/new weekend");
    assert_eq!(created["input"], 1);
    assert_eq!(created["ok"], true);
    assert!(!text(&created["convo"]).is_empty(), "{created}");

    let unknown = saro.input("/nope");
    assert_eq!(unknown["input"], 2);
    assert_eq!(unknown["ok"], false);
    assert!(
        text(&unknown["error"]).contains("Unknown command"),
        "{unknown}"
    );

    // The process goes on after a failed input.
    let quit = saro.input("/quit");
    assert_eq!(quit["input"], 3);
    assert_eq!(quit["ok"], true);

    assert!(saro.finish().success());
}

#[test]
fn two_peers_read_each_other_by_conversation_id() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    let convo = group(&mut saro, &mut [&mut raya]);

    // Neither has the shared group as its active chat when it posts.
    for peer in [&mut saro, &mut raya] {
        let other = text(&peer.run("/new elsewhere")["convo"]);
        assert_ne!(other, convo);
    }

    let sent = saro.run(&format!("/send {convo} hello from saro"));
    assert_eq!(sent["convo"], convo.as_str());
    let post = read_post(&mut raya, &convo);
    assert_eq!(post["from"], saro.address.as_str());
    assert_eq!(post["text"], "hello from saro");

    raya.run(&format!("/send {convo} hello from raya"));
    let post = read_post(&mut saro, &convo);
    assert_eq!(post["from"], raya.address.as_str());
    assert_eq!(post["text"], "hello from raya");

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn add_seats_several_addresses() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    // Joiners started at the same instant read the transport in step, so
    // both join, and write to the conversation, at the same instant.
    let [mut raya, mut pax] = rig.peers(["raya", "pax"]);

    let convo = group(&mut saro, &mut [&mut raya, &mut pax]);

    let members = saro.run("/members");
    assert_eq!(members["convo"], convo.as_str());
    assert_eq!(members["text"], "3 member(s)");

    // A seat that cannot read fails the test.
    saro.run(&format!("/send {convo} hello from saro"));
    for joiner in [&mut raya, &mut pax] {
        assert_eq!(read_post(joiner, &convo)["text"], "hello from saro");
    }

    for peer in [saro, raya, pax] {
        assert!(peer.finish().success());
    }
}

#[test]
fn a_post_arrives_as_it_was_sent() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    let convo = group(&mut saro, &mut [&mut raya]);

    // Quotes, a backslash, a tab, an emoji, the two characters of a newline
    // escape, and three characters some readers break a line at.
    let sent = "she said \"hi\", a back\\slash, a\ttab, 🎉 and \\n, \u{85}, \u{2028} and \u{2029}";
    saro.run(&format!("/send {convo} {sent}"));
    assert_eq!(read_post(&mut raya, &convo)["text"], sent);

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn it_ends_with_status_0_when_standard_input_ends() {
    let rig = Rig::new();
    let saro = rig.peer("saro");

    assert_eq!(saro.finish().code(), Some(0));
}

#[test]
fn a_reader_that_goes_away_ends_it_quietly() {
    let rig = Rig::new();
    let (reader, writer) = io::pipe().expect("a pipe");
    let mut process = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(rig.log("saro"))
        .stdin(Stdio::piped())
        .stdout(writer)
        .stderr(Stdio::piped())
        .spawn()
        .expect("chat-cli starts");
    // Standard input stays open, so only the closed pipe ends it: the ready
    // line has no reader.
    let _stdin = process.stdin.take();
    drop(reader);

    // Standard error ends, with nothing on it, when the process does.
    let stderr = lines(process.stderr.take().expect("piped")).recv_timeout(LIMIT);
    assert!(
        matches!(stderr, Err(RecvTimeoutError::Disconnected)),
        "{stderr:?}"
    );
    assert!(process.wait().expect("chat-cli was started").success());
}

#[test]
fn a_log_file_that_exists_is_refused_and_left_alone() {
    let rig = Rig::new();
    let log = rig.log("saro");
    fs::write(&log, "another run's log\n").expect("the log is written");

    let output = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(&log)
        .output()
        .expect("chat-cli starts");

    refused(&output);
    assert_eq!(
        fs::read_to_string(&log).expect("the log is there"),
        "another run's log\n"
    );
    // It refused before it did anything else.
    assert!(!rig.data().exists());
}

#[test]
fn a_run_without_a_log_file_is_refused() {
    let rig = Rig::new();

    let stderr = refused_by_clap(&mut rig.chat_cli("saro"));

    assert!(stderr.contains("--log-file"), "{stderr}");
    assert!(!rig.data().exists());
}

#[test]
fn group_timers_the_library_refuses_are_refused_before_anything_else() {
    let rig = Rig::new();
    let log = rig.log("saro");

    // The file transport runs the fast timers, whose consensus timeout is 250 ms.
    let output = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(&log)
        .args(["--group-voting-delay-ms", "250"])
        .output()
        .expect("chat-cli starts");

    assert_eq!(
        refused(&output),
        "chat-cli: Invalid conversation config: voting_delay must be < consensus_timeout\n"
    );
    assert!(!log.exists());
    assert!(!rig.data().exists());
}

#[test]
fn a_duration_given_by_its_flag_is_the_one_the_group_runs_by() {
    // The fast timers seat a member within their batch window and their freeze,
    // half a second each.
    const WINDOW: Duration = Duration::from_secs(3);
    let rig = Rig::new();
    let mut saro = rig.peer_with("saro", |command| {
        command.args([
            "--group-commit-batch-window-ms",
            &WINDOW.as_millis().to_string(),
        ]);
    });
    let mut raya = rig.peer("raya");

    let asked_at = Instant::now();
    group(&mut saro, &mut [&mut raya]);

    assert!(asked_at.elapsed() >= WINDOW, "{:?}", asked_at.elapsed());
    for peer in [saro, raya] {
        assert!(peer.finish().success());
    }
}

#[test]
fn the_log_of_a_joiner_holds_the_records_the_fork_report_reads() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    let convo = group(&mut saro, &mut [&mut raya]);
    // Saro's own commit has landed too, so both logs hold the epoch it made.
    saro.event("members");

    let report = rig.dir.path().join("report.json");
    let output = Command::new("python3")
        .arg(format!("{REPOSITORY}/tools/forks"))
        .arg("report")
        .arg("--json")
        .arg(&report)
        .args([rig.log("saro"), rig.log("raya")])
        .output()
        .expect("python3 runs the fork report");
    // Status 0 says that the group reads whole and no record was malformed.
    assert!(output.status.success(), "{output:?}");

    let report: Value =
        serde_json::from_str(&fs::read_to_string(&report).expect("the report is written"))
            .expect("the report is JSON");
    let conversation = &report["conversations"][0];
    assert_eq!(conversation["id"], convo.as_str());
    // The tool cuts an installation and an account to four bytes.
    for peer in [&saro, &raya] {
        let seat = conversation["installations"]
            .as_array()
            .expect("the seats")
            .iter()
            .find(|seat| seat["id"] == peer.installation[..8])
            .unwrap_or_else(|| panic!("no seat for {} in {conversation}", peer.name));
        assert_eq!(seat["account"], &peer.address[..8]);
        assert_eq!(seat["runs"], serde_json::json!([peer.name]));
        assert_eq!(seat["position"], "tip");
    }

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn a_join_leaves_the_active_chat_where_the_inputs_put_it() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    let mut pax = rig.peer("pax");

    let own = text(&raya.run("/new own")["convo"]);
    // Raya has read the line that says she was joined to Saro's group.
    let convo = group(&mut saro, &mut [&mut raya]);
    assert_ne!(own, convo);

    let added = raya.run(&format!("/add {}", pax.address));
    assert_eq!(added["convo"], own.as_str());
    assert_eq!(pax.event("joined")["convo"], own.as_str());

    let sent = raya.run("for the group raya made");
    assert_eq!(sent["convo"], own.as_str());
    assert_eq!(read_post(&mut pax, &own)["text"], "for the group raya made");

    for peer in [saro, raya, pax] {
        assert!(peer.finish().success());
    }
}

#[test]
fn an_input_that_fails_gets_a_result_with_its_error() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let nowhere = saro.input("/send nowhere hello");
    assert_eq!(nowhere["ok"], false);
    assert_eq!(nowhere["error"], "No chat matching 'nowhere'.");
    assert!(nowhere.get("text").is_none(), "{nowhere}");

    saro.run("/new weekend");
    let nobody = saro.input("/add nobody");
    assert_eq!(nobody["ok"], false);
    assert!(!text(&nobody["error"]).is_empty(), "{nobody}");
    assert!(nobody.get("text").is_none(), "{nobody}");

    assert!(saro.finish().success());
}

#[test]
fn an_empty_line_and_one_that_is_not_utf8_are_inputs_that_fail() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let empty = saro.input("");
    assert_eq!(empty["input"], 1);
    assert_eq!(empty["ok"], false);

    let not_utf8 = saro.input_bytes(b"/new \xff\xfe");
    assert_eq!(not_utf8["input"], 2);
    assert_eq!(not_utf8["ok"], false);

    // The process goes on, and has counted both.
    assert_eq!(saro.run("/new weekend")["input"], 3);

    assert!(saro.finish().success());
}

#[test]
fn a_carriage_return_before_the_line_break_is_no_part_of_the_input() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let unknown = saro.input("/nope\r");
    assert_eq!(
        unknown["error"],
        "Unknown command: /nope. Type /help for commands."
    );

    assert!(saro.finish().success());
}

#[test]
fn a_last_line_without_a_line_break_is_not_run() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    saro.run("/new weekend");
    saro.write(b"/new never");
    saro.end_input();

    let results = saro
        .read
        .iter()
        .filter(|line| line["type"] == "result")
        .count();
    assert_eq!(results, 1, "{:#?}", saro.read);
    assert_eq!(saro.finish().code(), Some(0));
}

#[test]
fn a_line_that_is_no_command_is_a_post_to_the_active_chat() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    // The group Saro made is his active chat.
    let convo = group(&mut saro, &mut [&mut raya]);

    let sent = saro.run("hello, with no command");
    assert_eq!(sent["convo"], convo.as_str());
    let post = read_post(&mut raya, &convo);
    assert_eq!(post["from"], saro.address.as_str());
    assert_eq!(post["text"], "hello, with no command");

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn a_direct_chat_is_joined_as_a_dm() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");

    let convo = text(&saro.run(&format!("/dm {}", raya.address))["convo"]);
    let joined = raya.event("joined");
    assert_eq!(joined["convo"], convo.as_str());
    assert_eq!(joined["kind"], "dm");

    saro.run(&format!("/send {convo} hello raya"));
    assert_eq!(read_post(&mut raya, &convo)["text"], "hello raya");

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn account_shows_the_address_and_the_installation_and_no_more() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let account = saro.run("/account");
    assert_eq!(
        account["output"],
        serde_json::json!([
            "── Your Account Address ──",
            saro.address,
            saro.installation
        ])
    );

    assert!(saro.finish().success());
}

#[test]
fn add_refuses_an_account_that_is_already_in_the_group() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer("raya");
    let pax = rig.peer("pax");
    group(&mut saro, &mut [&mut raya]);

    let again = saro.input(&format!("/add {}", raya.address));
    assert_eq!(again["ok"], false);
    assert_eq!(
        again["error"],
        "That account is already in the group (or its invite is pending)."
    );

    // Among several addresses, the one is named.
    let several = saro.input(&format!("/add {} {}", pax.address, raya.address));
    assert_eq!(several["ok"], false);
    assert_eq!(
        several["error"],
        format!(
            "{}… is already in the group (or its invite is pending).",
            &raya.address[..16]
        )
    );

    for peer in [saro, raya, pax] {
        assert!(peer.finish().success());
    }
}

#[test]
fn a_chat_that_cannot_be_sent_to_refuses_a_post_the_same_either_way() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let convo = text(&saro.run("/new weekend")["convo"]);
    assert!(saro.finish().success());

    // A chat kept from an earlier run cannot be sent to.
    fs::rename(rig.log("saro"), rig.log("saro-earlier")).expect("the log is moved aside");
    let mut saro = rig.peer("saro");
    saro.run(&format!("/switch {convo}"));

    let plain = saro.input("to a chat from an earlier run");
    assert_eq!(plain["ok"], false);
    assert!(
        text(&plain["error"]).starts_with("Not an active conversation"),
        "{plain}"
    );
    let by_id = saro.input(&format!("/send {convo} to a chat from an earlier run"));
    assert_eq!(by_id["ok"], false);
    assert_eq!(by_id["error"], plain["error"]);

    assert!(saro.finish().success());
}

#[test]
fn a_reader_that_goes_away_later_ends_it_at_its_next_line() {
    let rig = Rig::new();
    let (reader, writer) = io::pipe().expect("a pipe");
    let mut process = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(rig.log("saro"))
        .stdin(Stdio::piped())
        .stdout(writer)
        .stderr(Stdio::piped())
        .spawn()
        .expect("chat-cli starts");

    // The reader takes the ready line and leaves.
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut line = String::new();
        let read = reader.read_line(&mut line);
        let _ = tx.send((read, line, reader));
    });
    let (read, ready, reader) = rx.recv_timeout(LIMIT).expect("a first line");
    read.expect("the first line is read");
    assert!(ready.contains(r#""type":"ready""#), "{ready:?}");
    drop(reader);

    // Standard input stays open, so only a result that has no reader ends
    // it. A process another test starts holds the reading end until it
    // execs, and a result written in that instant is taken: inputs go in
    // until one has no reader.
    let mut stdin = process.stdin.take().expect("piped");
    let stderr = lines(process.stderr.take().expect("piped"));
    let deadline = Instant::now() + LIMIT;
    let ended = loop {
        // The write fails once the process has ended.
        let _ = writeln!(stdin, "/status");
        match stderr.recv_timeout(Duration::from_millis(100)) {
            Err(RecvTimeoutError::Timeout) if Instant::now() < deadline => {}
            ended => break ended,
        }
    };

    // Standard error ends, with nothing on it, when the process does.
    assert!(
        matches!(ended, Err(RecvTimeoutError::Disconnected)),
        "{ended:?}"
    );
    assert!(process.wait().expect("chat-cli was started").success());
}

#[test]
fn a_second_start_on_a_database_in_use_is_refused() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    let output = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(rig.log("saro-again"))
        .output()
        .expect("chat-cli starts");

    assert!(
        refused(&output).contains("another chat-cli is running"),
        "{output:?}"
    );
    // The first is as it was.
    saro.run("/new weekend");
    assert!(saro.finish().success());
}

#[test]
fn a_start_that_fails_leaves_no_log_and_can_be_made_again() {
    let rig = Rig::new();
    // A file where the data directory would be.
    fs::write(rig.data(), "in the way").expect("the file is written");

    let output = rig
        .chat_cli("saro")
        .arg("--log-file")
        .arg(rig.log("saro"))
        .output()
        .expect("chat-cli starts");

    assert!(refused(&output).contains("data directory"), "{output:?}");
    assert!(!rig.log("saro").exists());

    fs::remove_file(rig.data()).expect("the file is removed");
    assert!(rig.peer("saro").finish().success());
}

#[test]
fn a_command_line_clap_refuses_ends_with_status_2_and_its_usage() {
    let rig = Rig::new();

    let output = rig
        .chat_cli("saro")
        .arg("--no-such-flag")
        .output()
        .expect("chat-cli starts");

    assert_eq!(output.status.code(), Some(2), "{output:?}");
    assert!(output.stdout.is_empty(), "{output:?}");
    let stderr = String::from_utf8(output.stderr.clone()).expect("UTF-8");
    assert!(stderr.starts_with("error: "), "{output:?}");
    assert!(stderr.contains("Usage: "), "{output:?}");
    assert!(!rig.data().exists());
}

#[test]
fn rust_log_sets_the_filter_of_the_log() {
    let rig = Rig::new();
    let mut saro = rig.peer_with("saro", |command| {
        command.env("RUST_LOG", "warn");
    });

    saro.run("/new weekend");
    assert!(saro.finish().success());

    assert_eq!(trace_records(&rig.log("saro")), 0);
}

#[test]
fn an_empty_rust_log_sets_no_filter() {
    let rig = Rig::new();
    let mut saro = rig.peer_with("saro", |command| {
        command.env("RUST_LOG", "");
    });

    saro.run("/new weekend");
    assert!(saro.finish().success());

    // The run and the epoch of the group it made.
    assert_eq!(trace_records(&rig.log("saro")), 2);
}

#[test]
fn the_mock_registry_answers_a_key_package_post_that_names_no_device() {
    let registry = Registry::start();
    let address = registry.url.strip_prefix("http://").expect("an address");
    let mut stream = TcpStream::connect(address).expect("the mock registry listens");
    stream
        .set_read_timeout(Some(LIMIT))
        .expect("a limit on the read");

    stream
        .write_all(
            b"POST /v0/keypackage HTTP/1.1\r\nHost: mock\r\nConnection: close\r\n\
              Content-Length: 8\r\n\r\nnot JSON",
        )
        .expect("the request is written");
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .expect("the response is read");

    assert!(response.starts_with("HTTP/1.1 200 "), "{response:?}");
}

#[test]
fn a_cluster_id_cannot_be_given_with_a_preset() {
    let rig = Rig::new();

    let error = refused_by_clap(rig.chat_cli("saro").args([
        "--cluster-id",
        "77",
        "--preset",
        "logos.test",
    ]));

    assert!(error.contains("cannot be used with"), "{error}");
    assert!(!rig.data().exists());
}

#[test]
fn each_flag_of_a_local_network_needs_a_cluster_id() {
    let rig = Rig::new();

    for flag in [
        ["--static-node", "/ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm"],
        ["--node-ip", "10.89.0.7"],
        ["--max-message-kib", "64"],
    ] {
        let error = refused_by_clap(rig.chat_cli("saro").args(flag));
        assert!(error.contains("--cluster-id"), "{flag:?}: {error}");
    }
    assert!(!rig.data().exists());
}

#[test]
fn each_flag_of_a_local_network_cannot_be_given_with_a_preset() {
    let rig = Rig::new();
    let node = ["--static-node", "/ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm"];
    let ip = ["--node-ip", "10.89.0.7"];
    let largest = ["--max-message-kib", "64"];

    for flags in [
        &node[..],
        &ip[..],
        &largest[..],
        &[node, ip, largest].concat()[..],
    ] {
        let error = refused_by_clap(
            rig.chat_cli("saro")
                .args(["--preset", "logos.test"])
                .args(flags),
        );
        assert!(error.contains("cannot be used with"), "{flags:?}: {error}");
        assert!(error.contains("--preset"), "{flags:?}: {error}");
    }
    assert!(!rig.data().exists());
}

#[test]
fn a_node_to_dial_that_is_no_node_address_is_refused() {
    let rig = Rig::new();

    for node in [
        "",
        "notanaddress",
        "/ip4/127.0.0.1/tcp/4001",
        "/ip4/0.0.0.0/tcp/4001/p2p/16Uiu2HAm",
        "/ip4/127.0.0.1/tcp/0/p2p/16Uiu2HAm",
        "a\"b",
        "]",
    ] {
        let error = refused_by_clap(rig.chat_cli("saro").args([
            "--cluster-id",
            "77",
            "--static-node",
            node,
        ]));
        assert!(error.contains("--static-node"), "{node:?}: {error}");
        assert!(
            error.contains("/ip4/<address>/tcp/<port>/p2p/<peer id>"),
            "{node:?}: {error}"
        );
    }
    assert!(!rig.data().exists());
}

#[test]
fn a_node_ip_that_is_no_address_to_dial_is_refused() {
    let rig = Rig::new();

    for ip in ["0.0.0.0", "localhost", "10.89.0.256", "::1", ""] {
        let error =
            refused_by_clap(
                rig.chat_cli("saro")
                    .args(["--cluster-id", "77", "--node-ip", ip]),
            );
        assert!(error.contains("--node-ip"), "{ip:?}: {error}");
    }
    assert!(!rig.data().exists());
}

#[test]
fn a_cluster_id_the_library_reads_as_a_preset_is_refused() {
    let rig = Rig::new();

    for id in ["1", "2"] {
        let error = refused_by_clap(rig.chat_cli("saro").args(["--cluster-id", id]));
        assert!(error.contains("names a preset network"), "{id}: {error}");
    }
    let error = refused_by_clap(rig.chat_cli("saro").args([
        "--cluster-id",
        "77",
        "--max-message-kib",
        "0",
    ]));
    assert!(error.contains("--max-message-kib"), "{error}");
    assert!(!rig.data().exists());
}

#[test]
fn the_file_transport_has_no_node_to_ask() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");

    assert!(
        saro.read[0].get("node_address").is_none(),
        "{}",
        saro.read[0]
    );
    let asked = saro.input("/node");
    assert_eq!(asked["input"], 1);
    assert_eq!(asked["ok"], false);
    assert!(
        text(&asked["error"]).contains("no delivery node"),
        "{asked}"
    );
    assert!(asked.get("node").is_none(), "{asked}");

    assert!(saro.finish().success());
}

#[test]
fn a_node_is_dialed_at_the_address_its_ready_line_gives() {
    let rig = Rig::new();
    let mut saro = rig.node_peer("saro", &[]);
    assert!(
        saro.node_address.starts_with("/ip4/127.0.0.1/tcp/") && saro.node_address.contains("/p2p/"),
        "{}",
        saro.node_address
    );
    let alone = saro.run("/node");
    assert_eq!(alone["node"]["address"], saro.node_address.as_str());
    assert_eq!(alone["node"]["peers"], 0);
    assert_eq!(alone["node"]["mesh_peers"], 0);

    let dialed = saro.node_address.clone();
    let mut raya = rig.node_peer("raya", &["--static-node", &dialed]);
    for peer in [&mut saro, &mut raya] {
        let node = node_until(peer, "one peer in its mesh", |node| {
            node["peers"] == 1 && node["mesh_peers"] == 1
        });
        assert_eq!(node["publish_refused"], 0, "{node}");
        assert_eq!(node["inbound_dropped"], 0, "{node}");
    }

    let convo = group(&mut saro, &mut [&mut raya]);
    raya.run(&format!("/send {convo} over the node"));
    assert_eq!(read_post(&mut saro, &convo)["text"], "over the node");

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

// Linux answers every address of 127.0.0.0/8 on loopback, and macOS 127.0.0.1 alone.
#[cfg(target_os = "linux")]
#[test]
fn a_node_on_an_address_of_its_own_listens_and_is_dialed_there() {
    let rig = Rig::new();
    let mut saro = rig.node_peer("saro", &["--node-ip", "127.0.0.2"]);
    let mut raya = rig.node_peer(
        "raya",
        &[
            "--node-ip",
            "127.0.0.3",
            "--static-node",
            &saro.node_address,
        ],
    );

    for (peer, ip) in [(&saro, "127.0.0.2"), (&raya, "127.0.0.3")] {
        let prefix = format!("/ip4/{ip}/tcp/");
        let port = peer
            .node_address
            .strip_prefix(&prefix)
            .and_then(|rest| rest.split('/').next())
            .unwrap_or_else(|| panic!("{} is not on {ip}", peer.node_address));
        assert!(
            TcpStream::connect(("127.0.0.1", port.parse::<u16>().unwrap())).is_err(),
            "{} also listens on 127.0.0.1",
            peer.node_address
        );
    }
    for peer in [&mut saro, &mut raya] {
        node_until(peer, "a link", |node| node["peers"] == 1);
    }

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn a_node_of_a_local_network_is_ready_once_its_dial_is_up() {
    // A relay has no mesh before its first heartbeat, a second after its start.
    const HEARTBEAT: Duration = Duration::from_secs(1);
    let rig = Rig::new();

    let started = Instant::now();
    let saro = rig.node_peer("saro", &[]);
    let alone = started.elapsed();
    let started = Instant::now();
    let mut raya = rig.node_peer("raya", &["--static-node", &saro.node_address]);
    let dialing = started.elapsed();

    assert!(
        alone < DIAL_WAIT && dialing < DIAL_WAIT,
        "{alone:?}, {dialing:?}"
    );
    // The link is what it waits for, and its mesh is not.
    assert!(dialing < alone + HEARTBEAT / 2, "{alone:?}, {dialing:?}");
    assert_eq!(raya.run("/node")["node"]["peers"], 1);
    assert_eq!(raya.run("/node")["node"]["dials_ok"], 1);

    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn a_node_whose_dial_stays_down_is_ready_when_the_wait_is_over() {
    let rig = Rig::new();
    let saro = rig.node_peer("saro", &[]);
    let gone = saro.node_address.clone();
    assert!(saro.finish().success());

    let started = Instant::now();
    let mut raya = rig.node_peer("raya", &["--static-node", &gone]);
    let ready_after = started.elapsed();

    assert!(
        DIAL_WAIT <= ready_after && ready_after < 2 * DIAL_WAIT,
        "{ready_after:?}"
    );
    assert_eq!(raya.run("/node")["node"]["peers"], 0);
    let node = raya.run("/node")["node"].clone();
    assert_eq!(node["dials_ok"], 0, "{node}");
    assert_eq!(node["dials_failed"], 1, "{node}");
    assert!(raya.finish().success());
}

#[test]
fn a_duration_given_by_its_flag_is_the_one_a_group_over_a_node_runs_by() {
    // The fast timers seat a member within their batch window and their freeze,
    // half a second each.
    const WINDOW: Duration = Duration::from_secs(3);
    let rig = Rig::new();
    let window = WINDOW.as_millis().to_string();
    let mut saro = rig.node_peer("saro", &["--group-commit-batch-window-ms", &window]);
    let dialed = saro.node_address.clone();
    let mut raya = rig.node_peer("raya", &["--static-node", &dialed]);
    for peer in [&mut saro, &mut raya] {
        node_until(peer, "one peer in its mesh", |node| node["mesh_peers"] == 1);
    }

    let asked_at = Instant::now();
    group(&mut saro, &mut [&mut raya]);

    assert!(asked_at.elapsed() >= WINDOW, "{:?}", asked_at.elapsed());
    assert!(saro.finish().success());
    assert!(raya.finish().success());
}

#[test]
fn a_publish_the_node_refuses_is_counted_and_logged_with_its_size() {
    let rig = Rig::new();
    let mut saro = rig.node_peer("saro", &["--max-message-kib", "64"]);
    saro.run("/new weekend");

    // The call that hands the node a post is answered as taken, and the
    // refusal is an event.
    let post = "x".repeat(100_000);
    saro.run(&post);
    let node = node_until(&mut saro, "a publish it refused", |node| {
        node["publish_refused"] != 0
    });
    assert_eq!(node["publish_refused"], 1, "{node}");
    assert!(saro.finish().success());

    let log = fs::read_to_string(rig.log("saro")).expect("the log is there");
    let warning = log
        .lines()
        .find(|line| line.contains("the node refused a publish"))
        .unwrap_or_else(|| panic!("no warning in the log: {log}"));
    assert!(warning.contains("WARN"), "{warning}");
    // The frame is the post and what the group wraps it in.
    let bytes = logged(warning, "bytes");
    assert!(
        (post.len()..post.len() + 1024).contains(&bytes),
        "{warning}"
    );
    assert!(
        warning.contains("Message size exceeded maximum of 65536 bytes"),
        "{warning}"
    );
}

#[test]
fn the_log_holds_the_bytes_of_each_welcome_its_filter_lets_through() {
    let rig = Rig::new();
    let mut saro = rig.peer_with("saro", |command| {
        command.env("RUST_LOG", "warn,libchat::inbox_v2=info");
    });
    let mut raya = rig.peer("raya");
    let mut pax = rig.peer("pax");

    group(&mut saro, &mut [&mut raya, &mut pax]);
    for peer in [saro, raya, pax] {
        assert!(peer.finish().success());
    }

    let log = fs::read_to_string(rig.log("saro")).expect("the log is there");
    let welcomes: Vec<String> = log
        .lines()
        .map(plain)
        .filter(|line| line.contains(" libchat::inbox_v2: Publish WelcomeMessage "))
        .collect();
    // One a joiner, each the whole of a group of three.
    assert_eq!(welcomes.len(), 2, "{log}");
    let bytes = logged(&welcomes[0], "bytes");
    assert!(bytes > 1024, "{welcomes:?}");
    assert_eq!(logged(&welcomes[1], "bytes"), bytes, "{welcomes:?}");
    for welcome in &welcomes {
        assert!(welcome.contains(" INFO "), "{welcome}");
        assert!(welcome.ends_with(&format!(" bytes={bytes}")), "{welcome}");
    }
}

/// What starts a peer with the network model on, under one seed, and `flags`
/// beside it.
fn modelled<'a>(flags: &'a [&'a str]) -> impl FnOnce(&mut Command) + 'a {
    move |command| {
        command.args(["--network-seed", "7"]).args(flags);
    }
}

/// Bytes in lowercase hex, one or more, as an app id is written.
fn is_app_id(value: &Value) -> bool {
    value.as_str().is_some_and(|id| {
        !id.is_empty()
            && id.len() % 2 == 0
            && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// The app id `peer` was told for `convo`: on the result of the `/new` that
/// made it, or on the line that says it was joined.
fn app_id(peer: &Peer, convo: &str) -> String {
    let told = peer
        .read
        .iter()
        .find(|line| line["convo"] == convo && is_app_id(&line["app_id"]))
        .unwrap_or_else(|| peer.fail(&format!("no app id for {convo}")));
    text(&told["app_id"])
}

/// The lines read so far that say the network model of `peer` dropped a frame.
fn drops(peer: &Peer) -> Vec<&Value> {
    peer.read
        .iter()
        .filter(|line| line["type"] == "network_drop")
        .collect()
}

/// What the network model of `peer` answers.
fn network(peer: &mut Peer) -> Value {
    peer.run("/network")["network"].clone()
}

/// The word a log line gives for `field`, as `field=<word>`.
fn logged_word(line: &str, field: &str) -> String {
    let (_, value) = line
        .split_once(&format!(" {field}="))
        .unwrap_or_else(|| panic!("no {field} in the line: {line}"));
    value.split(' ').next().expect("a first word").to_string()
}

/// The group state a log records for the epoch of `convo` that has `members`
/// members, once the log holds it.
fn state_with(log: &Path, convo: &str, members: usize) -> Option<String> {
    fs::read_to_string(log)
        .expect("the log is there")
        .lines()
        .map(plain)
        .filter(|line| line.contains(" libchat::groupv2trace: epoch "))
        .filter(|line| logged_word(line, "convo") == convo)
        .filter(|line| logged(line, "members") == members)
        .map(|line| logged_word(&line, "state"))
        .next_back()
}

/// The group state each of `names` records for the epoch of `convo` that has
/// `members` members, in their order, waiting for it up to [`LIMIT`].
fn states(rig: &Rig, names: &[&str], convo: &str, members: usize) -> Vec<String> {
    let deadline = Instant::now() + LIMIT;
    loop {
        let states: Vec<String> = names
            .iter()
            .filter_map(|name| state_with(&rig.log(name), convo, members))
            .collect();
        if states.len() == names.len() {
            return states;
        }
        assert!(
            Instant::now() < deadline,
            "an epoch of {members} members is in the logs of {} of {names:?}",
            states.len()
        );
        thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn without_a_network_seed_no_frame_is_dropped_and_no_model_answers() {
    let rig = Rig::new();
    let [mut saro, mut raya] = rig.peers(["saro", "raya"]);
    let convo = group(&mut saro, &mut [&mut raya]);
    saro.run("hello");
    read_post(&mut raya, &convo);

    for input in [
        "/network",
        "/network cut 0a",
        "/network heal",
        "/network withhold candidate",
    ] {
        let refused = raya.input(input);
        assert_eq!(refused["ok"], false, "{input}");
        assert_eq!(
            refused["error"], "The network model is off. Start with --network-seed to turn it on.",
            "{input}"
        );
        assert!(refused.get("network").is_none(), "{refused}");
    }
    raya.end_input();
    assert!(drops(&raya).is_empty(), "{:?}", raya.read);
}

#[test]
fn a_network_flag_clap_refuses_ends_with_status_2() {
    let rig = Rig::new();
    let log = rig.log("saro");
    let refused = |args: &[&str]| {
        let mut command = rig.chat_cli("saro");
        command.arg("--log-file").arg(&log).args(args);
        refused_by_clap(&mut command)
    };

    for flag in ["--network-loss", "--network-welcome-loss"] {
        for rate in ["=half", "=1.5", "=-0.5", "=nan", "="] {
            let stderr = refused(&["--network-seed", "7", &format!("{flag}{rate}")]);
            assert!(
                stderr.contains("a rate is a number from 0 to 1"),
                "{flag}{rate}: {stderr}"
            );
        }
    }
    for value in [
        "--network-seed=seven",
        "--network-seed=-1",
        "--network-seed=1.5",
    ] {
        refused(&[value]);
    }
    refused(&["--network-seed", "7", "--network-delay-ms=1.5"]);

    for flag in [
        "--network-loss=0.5",
        "--network-welcome-loss=0.5",
        "--network-delay-ms=50",
    ] {
        let stderr = refused(&[flag]);
        assert!(stderr.contains("--network-seed <N>"), "{flag}: {stderr}");
    }

    assert!(!log.exists());
    assert!(!rig.data().exists());
}

#[test]
fn the_creator_of_a_group_and_a_joiner_are_each_told_an_app_id_of_their_own() {
    let rig = Rig::new();
    let [mut saro, mut raya] = rig.peers(["saro", "raya"]);

    let created = saro.run("/new weekend");
    saro.run(&format!("/add {}", raya.address));
    let joined = raya.event("joined");

    assert_eq!(joined["convo"], created["convo"]);
    assert!(is_app_id(&created["app_id"]), "{created}");
    assert!(is_app_id(&joined["app_id"]), "{joined}");
    assert_ne!(created["app_id"], joined["app_id"]);
}

#[test]
fn a_direct_chat_has_no_app_id() {
    let rig = Rig::new();
    let [mut saro, mut raya] = rig.peers(["saro", "raya"]);

    let started = saro.run(&format!("/dm {}", raya.address));
    let joined = raya.event("joined");

    assert_eq!(joined["kind"], "dm");
    assert!(started.get("app_id").is_none(), "{started}");
    assert!(joined.get("app_id").is_none(), "{joined}");
}

#[test]
fn network_answers_the_rules_it_was_started_with_and_those_it_is_given() {
    let rig = Rig::new();
    let mut raya = rig.peer_with(
        "raya",
        modelled(&[
            "--network-loss",
            "0.25",
            "--network-welcome-loss",
            "0.5",
            "--network-delay-ms",
            "20",
        ]),
    );

    let shown = raya.run("/network");
    assert_eq!(shown["text"], "Network model shown");
    let started_with = serde_json::json!({
        "seed": 7,
        "loss": 0.25,
        "welcome_loss": 0.5,
        "delay_ms": 20,
        "cut": [],
        "own": [],
        "withheld": null,
        "armed": false,
        "frames": {
            "arrived": 0,
            "passed": 0,
            "held": 0,
            "dropped": {"loss": 0, "welcome_loss": 0, "cut": 0, "withheld": 0},
        },
    });
    assert_eq!(shown["network"], started_with);

    // A cut takes the place of the one before it, and reads an app id in
    // either case.
    raya.run("/network cut 0b");
    let cut = raya.run("/network cut c2118f3b0d 5A904C11E7");
    assert_eq!(
        cut["network"]["cut"],
        serde_json::json!(["5a904c11e7", "c2118f3b0d"])
    );
    for (input, error) in [
        (
            "/network cut",
            "Usage: /network [cut <app id>... | heal | withhold candidate]",
        ),
        (
            "/network cut 0b xyz",
            "'xyz' is no app id: an app id is the hex of one byte or more.",
        ),
        (
            "/network cut 0b0",
            "'0b0' is no app id: an app id is the hex of one byte or more.",
        ),
    ] {
        let refused = raya.input(input);
        assert_eq!(refused["ok"], false, "{input}");
        assert_eq!(refused["error"], error, "{input}");
    }
    assert_eq!(network(&mut raya)["cut"], cut["network"]["cut"]);
    let healed = raya.run("/network heal");
    assert_eq!(healed["network"]["cut"], serde_json::json!([]));

    let armed = raya.run("/network withhold candidate");
    assert_eq!(armed["network"]["armed"], true);
    assert!(armed["network"]["withheld"].is_null(), "{armed}");
    let again = raya.input("/network withhold candidate");
    assert_eq!(
        again["error"],
        "No commit candidate is withheld: the rule is armed already."
    );

    for input in [
        "/network mend",
        "/network heal now",
        "/network withhold",
        "/network withhold candidate twice",
    ] {
        let refused = raya.input(input);
        assert_eq!(
            refused["error"], "Usage: /network [cut <app id>... | heal | withhold candidate]",
            "{input}"
        );
    }
    assert!(raya.finish().success());
}

#[test]
fn network_counts_the_frames_that_arrived_and_names_the_app_ids_of_its_own() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer_with("raya", modelled(&[]));
    let convo = group(&mut saro, &mut [&mut raya]);
    saro.run("hello");
    read_post(&mut raya, &convo);

    let network = network(&mut raya);
    assert_eq!(
        network["own"],
        serde_json::json!([app_id(&raya, &convo)]),
        "{network}"
    );
    let frames = &network["frames"];
    let count = |count: &Value| count.as_u64().expect("a count");
    // The welcome that seated Raya and the post, at the least.
    assert!(count(&frames["arrived"]) >= 2, "{network}");
    assert_eq!(
        count(&frames["arrived"]),
        count(&frames["passed"]) + count(&frames["held"]),
        "{network}"
    );
    assert_eq!(
        frames["dropped"],
        serde_json::json!({"loss": 0, "welcome_loss": 0, "cut": 0, "withheld": 0})
    );
    raya.end_input();
    assert!(drops(&raya).is_empty(), "{:?}", raya.read);
}

#[test]
fn at_a_loss_of_1_a_joiner_still_joins_and_a_post_is_dropped() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer_with("raya", modelled(&["--network-loss", "1"]));
    // A welcome is not lost at this rate.
    let convo = group(&mut saro, &mut [&mut raya]);

    // Longer than any frame the group makes of its own, which tells its drop
    // from theirs.
    let post = "x".repeat(20_000);
    saro.run(&post);
    let dropped = raya.wait_for("the drop of the post", |line| {
        line["type"] == "network_drop" && line["bytes"].as_u64() >= Some(20_000)
    });

    assert_eq!(dropped["why"], "loss", "{dropped}");
    assert_eq!(dropped["kind"], "group", "{dropped}");
    assert_eq!(dropped["app_id"], app_id(&saro, &convo), "{dropped}");
    assert!(dropped.get("seat").is_none(), "{dropped}");
    assert!(dropped["n"].as_u64() >= Some(1), "{dropped}");
    raya.end_input();
    assert!(
        raya.read.iter().all(|line| line["type"] != "message"),
        "{:?}",
        raya.read
    );
    // What Raya published as it joined has come back to it, and is no frame
    // the model drops.
    let own = app_id(&raya, &convo);
    assert!(
        drops(&raya)
            .iter()
            .all(|line| line["app_id"] != own.as_str()),
        "{:?}",
        raya.read
    );
}

#[test]
fn a_frame_of_a_joiners_own_that_a_node_hands_back_is_not_dropped() {
    let rig = Rig::new();
    let mut saro = rig.node_peer("saro", &[]);
    let dialed = saro.node_address.clone();
    let mut raya = rig.node_peer(
        "raya",
        &[
            "--static-node",
            &dialed,
            "--network-seed",
            "7",
            "--network-loss",
            "1",
        ],
    );
    for peer in [&mut saro, &mut raya] {
        node_until(peer, "one peer in its mesh", |node| node["mesh_peers"] == 1);
    }
    let convo = group(&mut saro, &mut [&mut raya]);

    // The frame Raya published as it joined is back from its node before a
    // post made after.
    let post = "x".repeat(20_000);
    saro.run(&post);
    raya.wait_for("the drop of the post", |line| {
        line["type"] == "network_drop" && line["bytes"].as_u64() >= Some(20_000)
    });
    let network = network(&mut raya);
    raya.end_input();

    let own = app_id(&raya, &convo);
    assert_eq!(network["own"], serde_json::json!([own]), "{network}");
    assert!(
        drops(&raya)
            .iter()
            .all(|line| line["app_id"] != own.as_str()),
        "{:?}",
        raya.read
    );
}

#[test]
fn a_cut_drops_the_posts_of_its_senders_and_no_others_until_it_is_healed() {
    let rig = Rig::new();
    let [mut saro, mut pax] = rig.peers(["saro", "pax"]);
    let mut raya = rig.peer_with("raya", modelled(&[]));
    let convo = group(&mut saro, &mut [&mut raya, &mut pax]);
    let cut_off = app_id(&pax, &convo);

    raya.run(&format!("/network cut {cut_off}"));
    pax.run(&format!("/send {convo} from pax"));
    saro.run("from saro");
    // The transport hands Raya the two in the order they were made.
    assert_eq!(read_post(&mut raya, &convo)["text"], "from saro");
    let dropped = raya.event("network_drop");
    assert_eq!(dropped["why"], "cut", "{dropped}");
    assert_eq!(dropped["kind"], "group", "{dropped}");
    assert_eq!(dropped["app_id"], cut_off.as_str(), "{dropped}");

    raya.run("/network heal");
    pax.run(&format!("/send {convo} from pax again"));
    assert_eq!(read_post(&mut raya, &convo)["text"], "from pax again");
    let network = network(&mut raya);
    // Pax's post, and whatever else it published while it was cut off.
    let dropped = drops(&raya);
    assert!(
        dropped
            .iter()
            .all(|line| line["why"] == "cut" && line["app_id"] == cut_off.as_str()),
        "{:?}",
        raya.read
    );
    assert_eq!(
        network["frames"]["dropped"]["cut"],
        dropped.len(),
        "{network}"
    );
}

#[test]
fn a_post_is_read_no_sooner_than_the_delay() {
    const DELAY: Duration = Duration::from_millis(1500);
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let delay = DELAY.as_millis().to_string();
    let mut raya = rig.peer_with("raya", modelled(&["--network-delay-ms", &delay]));
    let convo = group(&mut saro, &mut [&mut raya]);

    let sent = Instant::now();
    saro.run("hello");
    read_post(&mut raya, &convo);

    assert!(sent.elapsed() >= DELAY, "{:?}", sent.elapsed());
}

#[test]
fn two_starts_with_one_seed_and_one_name_drop_the_same_frames() {
    const POSTS: u64 = 40;
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    saro.run("/new weekend");
    for n in 0..POSTS {
        saro.run(&format!("post {n}"));
    }

    // The file transport hands a peer every frame written to it, from the
    // first and in one order, so a peer that is in no group has the posts
    // arrive all the same.
    let dropped = |run: &str| {
        let mut command = rig.chat_cli("pax");
        command
            .arg("--log-file")
            .arg(rig.log(run))
            .arg("--db")
            .arg(rig.dir.path().join(format!("{run}.db")))
            .args(["--network-seed", "7", "--network-loss", "0.5"]);
        let mut pax = rig.spawn("pax", command);
        pax.read_ready();
        let deadline = Instant::now() + LIMIT;
        while network(&mut pax)["frames"]["arrived"].as_u64() < Some(POSTS) {
            if Instant::now() >= deadline {
                pax.fail("the posts never arrived");
            }
            thread::sleep(Duration::from_millis(100));
        }
        let dropped: Vec<u64> = drops(&pax)
            .iter()
            .filter_map(|line| line["n"].as_u64())
            .filter(|n| *n <= POSTS)
            .collect();
        assert!(pax.finish().success());
        dropped
    };

    let first = dropped("first");
    assert!(
        !first.is_empty() && (first.len() as u64) < POSTS,
        "{first:?}"
    );
    assert_eq!(dropped("second"), first);
}

#[test]
fn at_a_welcome_loss_of_1_a_member_never_joins() {
    let rig = Rig::new();
    let mut saro = rig.peer("saro");
    let mut raya = rig.peer_with("raya", modelled(&["--network-welcome-loss", "1"]));
    let convo = text(&saro.run("/new weekend")["convo"]);
    saro.run(&format!("/add {}", raya.address));
    // Saro's commit has landed, and with it the group has seated Raya.
    saro.event("members");

    let dropped = raya.event("network_drop");
    assert_eq!(dropped["why"], "welcome_loss", "{dropped}");
    assert_eq!(dropped["kind"], "welcome", "{dropped}");

    // The welcomes were written before this post is, so the poll that hands
    // Raya the post has read them too.
    raya.run(&format!("/network cut {}", app_id(&saro, &convo)));
    saro.run("hello");
    raya.wait_for("the drop of the post", |line| {
        line["type"] == "network_drop" && line["why"] == "cut"
    });
    raya.end_input();
    for line in &raya.read {
        assert_ne!(line["type"], "joined", "{line}");
        assert_ne!(line["type"], "message", "{line}");
    }
    assert!(
        drops(&raya)
            .iter()
            .all(|line| line["why"] == "cut" || line["kind"] == "welcome"),
        "{:?}",
        raya.read
    );
}

#[test]
fn a_member_a_candidate_is_withheld_from_is_alone_exactly_when_the_group_applied_it() {
    // On nodes that each dial the ones before them: over the file transport
    // a group seated one member at a time splits with nothing withheld.
    let rig = Rig::new();
    let mut dials: Vec<String> = Vec::new();
    let [mut saro, mut raya, mut pax, mut mira, mut kes] = ["saro", "raya", "pax", "mira", "kes"]
        .map(|name| {
            let mut args: Vec<&str> = dials.iter().map(String::as_str).collect();
            if name == "pax" {
                args.extend(["--network-seed", "7"]);
            }
            let peer = rig.node_peer(name, &args);
            dials.extend(["--static-node".to_string(), peer.node_address.clone()]);
            peer
        });
    for peer in [&mut saro, &mut raya, &mut pax, &mut mira, &mut kes] {
        node_until(peer, "the four others in its mesh", |node| {
            node["mesh_peers"] == 4
        });
    }

    // Seated one at a time, each round over on every member before the next,
    // so the candidate withheld is one of the round that seats Kes.
    let convo = text(&saro.run("/new weekend")["convo"]);
    let mut seated = vec!["saro"];
    for joiner in [&mut raya, &mut pax, &mut mira] {
        saro.run(&format!("/add {}", joiner.address));
        joiner.event("joined");
        seated.push(joiner.name);
        states(&rig, &seated, &convo, seated.len());
    }
    let others = [&saro, &raya, &mira].map(|peer| app_id(peer, &convo));

    pax.run("/network withhold candidate");
    saro.run(&format!("/add {}", kes.address));
    kes.event("joined");
    let group = states(&rig, &["saro", "raya", "mira", "kes"], &convo, 5);
    assert!(group.iter().all(|state| *state == group[0]), "{group:?}");
    let alone = states(&rig, &["pax"], &convo, 5)[0] != group[0];

    // A steward broadcasts its welcome as it merges its commit, a freeze
    // window before Pax, short of a candidate, applies one. So that welcome
    // has arrived once two posts Kes made after Pax did have.
    pax.run(&format!("/network cut {}", app_id(&kes, &convo)));
    for _ in 0..2 {
        kes.run(&format!("/send {convo} seated"));
        pax.wait_for("the drop of a post of kes", |line| {
            line["type"] == "network_drop" && line["why"] == "cut"
        });
    }

    let network = network(&mut pax);
    pax.end_input();
    let withheld: Vec<&Value> = drops(&pax)
        .into_iter()
        .filter(|line| line["why"] == "withheld")
        .collect();
    let [withheld] = withheld[..] else {
        pax.fail("not one candidate was withheld");
    };
    assert_eq!(withheld["kind"], "candidate", "{withheld}");
    assert!(withheld["seat"].is_u64(), "{withheld}");
    assert!(
        others.contains(&text(&withheld["app_id"])),
        "{withheld}, of {others:?}"
    );
    assert_eq!(network["withheld"]["app_id"], withheld["app_id"]);
    assert_eq!(network["withheld"]["seat"], withheld["seat"]);
    assert_eq!(network["armed"], false, "{network}");
    assert_eq!(network["frames"]["dropped"]["withheld"], 1, "{network}");
    assert_eq!(
        network["withheld"]["applied"], alone,
        "pax is alone exactly when the group applied the candidate withheld from it: {network}"
    );
}

#[test]
fn a_drop_that_a_result_counts_has_its_line_before_that_result() {
    const FRAMES: u64 = 1000;
    let rig = Rig::new();
    let mut saro = rig.peer_with("saro", modelled(&["--network-loss", "1"]));

    // Frames of no group in one file, which the transport hands on in one
    // poll: the model drops them faster than their lines are written.
    let address = rig.data().join("transport").join("flood");
    fs::create_dir_all(&address).expect("a directory of the transport");
    let frame = [&1u32.to_be_bytes()[..], &[0xff]].concat();
    fs::write(address.join("0.bin"), frame.repeat(FRAMES as usize)).expect("the frames");

    let deadline = Instant::now() + LIMIT;
    loop {
        let counted = network(&mut saro)["frames"]["dropped"]["loss"]
            .as_u64()
            .expect("a count");
        let written = drops(&saro).len() as u64;
        assert!(counted <= written, "{counted} counted, {written} written");
        if counted >= FRAMES {
            break;
        }
        if Instant::now() >= deadline {
            saro.fail("the frames never arrived");
        }
    }
}
