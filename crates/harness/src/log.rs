//! A scenario's log: the level `LOG` asks for, and the trace records read
//! back from it.

use libchat::{ParticipantId, SignerKey};
use std::collections::BTreeMap;
use std::io;
use std::sync::{Arc, Mutex};
use tracing::Level;
use tracing_subscriber::fmt::writer::MakeWriterExt;
use tracing_subscriber::fmt::{MakeWriter, TestWriter};

/// The target the core writes its trace records on.
pub(crate) const TRACE: &str = "libchat::groupv2trace";

/// The level `LOG=warn|info|debug` asks of the tracing feed, errors alone
/// without it.
pub fn log_level() -> Level {
    match std::env::var("LOG").as_deref() {
        Ok("debug") => Level::DEBUG,
        Ok("info") => Level::INFO,
        Ok("warn") => Level::WARN,
        _ => Level::ERROR,
    }
}

#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);

impl io::Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for Buffer {
    type Writer = Buffer;

    fn make_writer(&'a self) -> Buffer {
        self.clone()
    }
}

/// One trace record: its name and what it says.
#[derive(Debug)]
pub struct Record {
    pub name: String,
    pub fields: BTreeMap<String, String>,
}

impl Record {
    /// The record a rendered line holds, or `None` for any other line.
    pub(crate) fn read(line: &str) -> Option<Self> {
        let record = line.split_once(TRACE)?.1.strip_prefix(": ")?;
        let mut words = record.split_whitespace();
        let name = words.next().expect("record name").to_string();
        let mut fields = BTreeMap::new();
        for word in words {
            let (key, value) = word.split_once('=').expect("key=value");
            let repeated = fields.insert(key.to_string(), value.to_string());
            assert!(repeated.is_none(), "{key} twice in {record}");
        }
        Some(Record { name, fields })
    }

    /// The epoch the record is about.
    pub fn epoch(&self) -> u64 {
        self.fields["epoch"].parse().expect("epoch number")
    }

    /// The commit an `epoch` record names and the installation that
    /// committed it, `None` when it names neither.
    pub fn commit(&self) -> Option<(&str, &str)> {
        match (self.fields.get("commit"), self.fields.get("by")) {
            (Some(commit), Some(by)) => Some((commit, by)),
            (None, None) => None,
            _ => panic!("a commit without its committer, or the other way: {self:?}"),
        }
    }
}

/// Runs `scenario` and returns the trace records it logged, in the order
/// written, read back from the rendered lines. The lines `LOG` asks for go to
/// the test's output as well.
pub fn trace_of<T>(scenario: impl FnOnce() -> T) -> (T, Vec<Record>) {
    let buffer = Buffer::default();
    let level = log_level();
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(level.max(Level::INFO))
        .with_writer(buffer.clone().and(TestWriter::new().with_max_level(level)))
        .with_ansi(false)
        .finish();
    let result = tracing::subscriber::with_default(subscriber, scenario);

    let log = String::from_utf8(buffer.0.lock().unwrap().clone()).expect("utf-8 log");
    let records = log.lines().filter_map(Record::read).collect();
    (result, records)
}

/// A client as the records name it.
pub struct Member {
    pub installation: String,
    pub account: String,
}

impl Member {
    /// Shortens a signer and an account the way the records do.
    pub fn new(installation: SignerKey, account: ParticipantId) -> Self {
        let short = |id: String| id.chars().take(8).collect();
        Self {
            installation: short(installation.to_string()),
            account: short(account.to_string()),
        }
    }
}

/// `members` as an `epoch` record lists them.
pub fn roster(members: &[&Member]) -> String {
    let entries: Vec<String> = members
        .iter()
        .map(|m| format!("{}:{}", m.installation, m.account))
        .collect();
    entries.join(",")
}

/// The records `member` wrote of the epochs it held and the conversations it
/// left, as name and epoch, in order.
pub fn written_by<'a>(records: &'a [Record], member: &Member) -> Vec<(&'a str, u64)> {
    records
        .iter()
        .filter(|r| {
            matches!(r.name.as_str(), "epoch" | "left")
                && r.fields["installation"] == member.installation
        })
        .map(|r| (r.name.as_str(), r.epoch()))
        .collect()
}

/// The `sent` records `member` wrote, one for each post, in order.
pub fn sent_by<'a>(records: &'a [Record], member: &Member) -> Vec<&'a Record> {
    records
        .iter()
        .filter(|r| r.name == "sent" && r.fields["installation"] == member.installation)
        .collect()
}

/// What `member` wrote of `frame`, in order: a record's name and, for an
/// `unreadable`, why.
pub fn written_of<'a>(
    records: &'a [Record],
    member: &Member,
    frame: &str,
) -> Vec<(&'a str, Option<&'a str>)> {
    records
        .iter()
        .filter(|r| {
            r.fields.get("frame").is_some_and(|f| f == frame)
                && r.fields["installation"] == member.installation
        })
        .map(|r| (r.name.as_str(), r.fields.get("why").map(String::as_str)))
        .collect()
}
