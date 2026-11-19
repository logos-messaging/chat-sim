//! A scenario's log: the level `LOG` asks for, and the trace records read
//! back from it.

use libchat::{ParticipantId, SignerKey};
use std::collections::BTreeMap;
use std::io;
use std::sync::{Arc, Mutex};
use tracing::Level;
use tracing_subscriber::Layer;
use tracing_subscriber::filter::{FilterExt, Targets, filter_fn};
use tracing_subscriber::fmt::writer::MakeWriterExt;
use tracing_subscriber::fmt::{MakeWriter, TestWriter};
use tracing_subscriber::layer::{Filter, SubscriberExt};

/// The target the core writes its trace records on.
pub(crate) const TRACE: &str = "libchat::mls_trace";
/// The targets of the ops' spans, a GroupV2 group's and a GroupV3 group's,
/// whose fields name the signer and the conversation a record is about.
const OPS: [&str; 2] = [
    "libchat::conversation::group_v2",
    "libchat::conversation::group_v3",
];

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

/// What a log takes: the trace records, the ops' spans they are written in,
/// and of everything else what `LOG` asks for.
pub(crate) fn lines<S: tracing::Subscriber>() -> impl Filter<S> {
    let ops = filter_fn(|meta| meta.is_span() && OPS.contains(&meta.target()));
    Targets::new()
        .with_default(log_level())
        .with_target(TRACE, Level::TRACE)
        .or(ops)
}

/// One trace record: its name and what it says.
#[derive(Debug)]
pub struct Record {
    pub name: String,
    pub fields: BTreeMap<String, String>,
}

impl Record {
    /// The record a rendered line holds, or `None` for any other line. Its
    /// `signer` and `convo` are read off the span of the op that wrote it.
    pub(crate) fn read(line: &str) -> Option<Self> {
        let (prefix, record) = line.split_once(TRACE)?;
        let record = record.strip_prefix(": ")?;
        let mut words = record.split_whitespace();
        let name = words.next().expect("record name").to_string();
        let span_fields = prefix
            .rfind('{')
            .map(|open| &prefix[open + 1..prefix.rfind('}').expect("span fields")]);
        let context = span_fields
            .into_iter()
            .flat_map(str::split_whitespace)
            .filter(|word| word.starts_with("signer=") || word.starts_with("convo="));
        let mut fields = BTreeMap::new();
        for word in context.chain(words) {
            let (key, value) = word.split_once('=').expect("key=value");
            let repeated = fields.insert(key.to_string(), value.to_string());
            assert!(repeated.is_none(), "{key} twice in {prefix}{record}");
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
    let layer = tracing_subscriber::fmt::layer()
        .with_writer(
            buffer
                .clone()
                .and(TestWriter::new().with_max_level(log_level())),
        )
        .with_ansi(false)
        .with_filter(lines());
    let subscriber = tracing_subscriber::registry().with(layer);
    let result = tracing::subscriber::with_default(subscriber, scenario);

    let log = String::from_utf8(buffer.0.lock().unwrap().clone()).expect("utf-8 log");
    let records = log.lines().filter_map(Record::read).collect();
    (result, records)
}

/// A client as the records name it.
pub struct Member {
    pub signer: String,
    pub participant: String,
}

impl Member {
    /// Shortens a signer and a participant the way the records do.
    pub fn new(signer: SignerKey, participant: ParticipantId) -> Self {
        let short = |id: String| id.chars().take(8).collect();
        Self {
            signer: short(signer.to_string()),
            participant: short(participant.to_string()),
        }
    }
}

/// `members` as an `epoch` record lists them.
pub fn signers(members: &[&Member]) -> String {
    let entries: Vec<String> = members
        .iter()
        .map(|m| format!("{}:{}", m.signer, m.participant))
        .collect();
    entries.join(",")
}

/// The records `member` wrote of the epochs it entered and the conversations
/// it left, as name and epoch, in order.
pub fn written_by<'a>(records: &'a [Record], member: &Member) -> Vec<(&'a str, u64)> {
    records
        .iter()
        .filter(|r| {
            matches!(r.name.as_str(), "epoch" | "left") && r.fields["signer"] == member.signer
        })
        .map(|r| (r.name.as_str(), r.epoch()))
        .collect()
}

/// The `sent` records `member` wrote, one for each post, in order.
pub fn sent_by<'a>(records: &'a [Record], member: &Member) -> Vec<&'a Record> {
    records
        .iter()
        .filter(|r| r.name == "sent" && r.fields["signer"] == member.signer)
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
            r.fields.get("frame").is_some_and(|f| f == frame) && r.fields["signer"] == member.signer
        })
        .map(|r| (r.name.as_str(), r.fields.get("why").map(String::as_str)))
        .collect()
}
