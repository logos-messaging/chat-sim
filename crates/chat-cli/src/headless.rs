//! Headless mode: one input a line on standard input, one JSON object a line
//! on standard output.

use std::io::{self, BufRead, Write};
use std::thread;

use anyhow::{Context, Result};
use crossbeam_channel::{Receiver, bounded, never, select};
use logos_chat::{
    AuthService, ConversationClass, ConversationStore, Event, NodeState, RegistrationService,
    Transport,
};
use network_model::{AppId, Dropped, Kind, Why};
use serde::Serialize;

use crate::app::{ChatApp, Outcome};
use crate::transport::modelled::NetworkState;

/// A line of standard output.
#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Line {
    Ready {
        name: String,
        address: String,
        installation: String,
        /// The multiaddress another delivery node dials this client's at.
        #[serde(skip_serializing_if = "Option::is_none")]
        node_address: Option<String>,
    },
    /// What the input line numbered `input`, from 1, came to.
    Result {
        input: u64,
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        text: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        convo: Option<String>,
        /// The app id this client stamps on the frames it publishes to
        /// `convo`, once the network model has learned it.
        #[serde(skip_serializing_if = "Option::is_none")]
        app_id: Option<String>,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        output: Vec<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        node: Option<NodeState>,
        #[serde(skip_serializing_if = "Option::is_none")]
        network: Option<Box<NetworkState>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    Joined {
        convo: String,
        kind: &'static str,
        /// The app id this client stamps on the frames it publishes to the
        /// conversation, which the network model learns from the join the
        /// client publishes.
        #[serde(skip_serializing_if = "Option::is_none")]
        app_id: Option<String>,
    },
    Members {
        convo: String,
    },
    Message {
        convo: String,
        from: String,
        text: String,
    },
    InboundError {
        message: String,
    },
    /// A frame the network model dropped, the `n`-th to arrive.
    NetworkDrop {
        n: u64,
        why: &'static str,
        kind: &'static str,
        bytes: usize,
        /// The app id of the frame's sender.
        #[serde(skip_serializing_if = "Option::is_none")]
        app_id: Option<String>,
        /// The seat of the steward that minted a commit candidate.
        #[serde(skip_serializing_if = "Option::is_none")]
        seat: Option<u32>,
    },
}

/// Serve `app` until standard input ends, a `/quit` arrives or the reader of
/// standard output goes away.
pub fn run<T, R, A, S>(app: &mut ChatApp<T, R, A, S>) -> Result<()>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    let events = app.events().clone();
    // A run without the model drops no frame.
    let mut drops = app
        .network()
        .map_or_else(never, |network| network.drops().clone());
    let input = read_input().context("failed to read standard input")?;
    // The roster the app opens on is no command's output.
    app.take_command_output();

    let ready = Line::Ready {
        name: app.user_name.clone(),
        address: app.client.addr().to_string(),
        installation: app.client.installation_name(),
        node_address: app
            .node_state()
            .context("failed to ask the delivery node its address")?
            .map(|node| node.address),
    };
    if !write_line(&ready)? {
        return Ok(());
    }

    let mut number = 0;
    loop {
        select! {
            recv(input) -> line => {
                // The channel closes when standard input ends.
                let Ok(line) = line else {
                    return Ok(());
                };
                let line = line.context("failed to read standard input")?;
                number += 1;
                let outcome = handle_input(app, line);
                let quit = matches!(outcome, Ok(Outcome::Quit));
                // A drop the result counts has its line before the result.
                for dropped in drops.try_iter() {
                    if !write_line(&drop_line(&dropped))? {
                        return Ok(());
                    }
                }
                let output = app.take_command_output();
                let result = result_line(app, number, outcome, output);
                if !write_line(&result)? || quit {
                    return Ok(());
                }
            }
            recv(events) -> event => {
                let event = event.context("the chat client stopped")?;
                let line = event_line(app, &event);
                app.process_event(event)?;
                if let Some(line) = line
                    && !write_line(&line)?
                {
                    return Ok(());
                }
            }
            recv(drops) -> dropped => {
                // The model's thread ends with the transport's stream, and
                // no frame is dropped after it.
                let Ok(dropped) = dropped else {
                    drops = never();
                    continue;
                };
                if !write_line(&drop_line(&dropped))? {
                    return Ok(());
                }
            }
        }
    }
}

/// Standard input a line at a time, without the line break. The channel
/// closes when the input ends.
///
/// Lines are bytes, so one that is not UTF-8 is still an input with a number.
fn read_input() -> io::Result<Receiver<io::Result<Vec<u8>>>> {
    let (tx, rx) = bounded(0);
    thread::Builder::new()
        .name("headless-input".into())
        .spawn(move || {
            let mut stdin = io::stdin().lock();
            loop {
                let mut line = Vec::new();
                let read = stdin.read_until(b'\n', &mut line);
                // The input ended. What follows its last line break is no
                // line: its writer never finished it.
                if read.is_ok() && line.last() != Some(&b'\n') {
                    return;
                }
                if line.last() == Some(&b'\n') {
                    line.pop();
                    if line.last() == Some(&b'\r') {
                        line.pop();
                    }
                }
                let failed = read.is_err();
                if tx.send(read.map(|_| line)).is_err() || failed {
                    return;
                }
            }
        })?;
    Ok(rx)
}

/// One input line: a command when it starts with `/`, a message for the
/// active chat otherwise.
fn handle_input<T, R, A, S>(app: &mut ChatApp<T, R, A, S>, line: Vec<u8>) -> Result<Outcome>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    let Ok(line) = String::from_utf8(line) else {
        return Ok(Outcome::Refused("The input is not UTF-8.".to_string()));
    };
    if line.is_empty() {
        return Ok(Outcome::Refused("The input is empty.".to_string()));
    }
    if line.starts_with('/') {
        app.handle_command(&line)
    } else {
        app.send_message(&line)
    }
}

fn result_line<T, R, A, S>(
    app: &ChatApp<T, R, A, S>,
    input: u64,
    outcome: Result<Outcome>,
    output: Vec<String>,
) -> Line
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    let (text, convo, node, network, error) = match outcome {
        Ok(Outcome::Done {
            text,
            convo,
            node,
            network,
            ..
        }) => (Some(text), convo, node, network, None),
        Ok(Outcome::Quit) => (None, None, None, None, None),
        Ok(Outcome::Refused(why)) => (None, None, None, None, Some(why)),
        Err(e) => (None, None, None, None, Some(format!("{e:#}"))),
    };
    let app_id = convo.as_deref().and_then(|convo| app.app_id(convo));
    Line::Result {
        input,
        ok: error.is_none(),
        text,
        convo,
        app_id: app_id.as_ref().map(AppId::to_string),
        output,
        node,
        network,
        error,
    }
}

/// The line for an event, or `None` for one that has no line.
fn event_line<T, R, A, S>(app: &ChatApp<T, R, A, S>, event: &Event) -> Option<Line>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    match event {
        Event::ConversationStarted { convo_id, class } => Some(Line::Joined {
            convo: convo_id.to_string(),
            kind: match class {
                ConversationClass::Dm => "dm",
                ConversationClass::Group => "group",
            },
            app_id: app.app_id(convo_id).as_ref().map(AppId::to_string),
        }),
        Event::MessageReceived {
            convo_id,
            content,
            sender,
            ..
        } => Some(Line::Message {
            convo: convo_id.to_string(),
            from: sender.account().to_string(),
            text: message_types::decode(content).content.to_string(),
        }),
        Event::ConversationMembersChanged { convo_id } => Some(Line::Members {
            convo: convo_id.to_string(),
        }),
        Event::InboundError { message } => Some(Line::InboundError {
            message: message.clone(),
        }),
        _ => None,
    }
}

/// The line for a frame the network model dropped.
fn drop_line(dropped: &Dropped) -> Line {
    Line::NetworkDrop {
        n: dropped.n,
        why: match dropped.why {
            Why::Loss => "loss",
            Why::WelcomeLoss => "welcome_loss",
            Why::Cut => "cut",
            Why::Withheld => "withheld",
        },
        kind: match dropped.frame.kind {
            Kind::Welcome => "welcome",
            Kind::Candidate { .. } => "candidate",
            Kind::Group => "group",
            Kind::Other => "other",
        },
        bytes: dropped.bytes,
        app_id: dropped.frame.sender.as_ref().map(AppId::to_string),
        seat: match dropped.frame.kind {
            Kind::Candidate { seat } => seat,
            _ => None,
        },
    }
}

/// Characters JSON leaves as they are in a string and some readers break a
/// line at.
const LINE_BREAKS: [char; 3] = ['\u{85}', '\u{2028}', '\u{2029}'];

/// Write `line` whole and flush it. `false` when the reader has gone away.
fn write_line(line: &Line) -> Result<bool> {
    let mut text = serde_json::to_string(line)?;
    // They can only be inside a string, where an escape reads the same.
    for c in LINE_BREAKS {
        if text.contains(c) {
            text = text.replace(c, &format!("\\u{:04x}", u32::from(c)));
        }
    }
    text.push('\n');
    let mut out = io::stdout().lock();
    match out.write_all(text.as_bytes()).and_then(|()| out.flush()) {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == io::ErrorKind::BrokenPipe => Ok(false),
        Err(e) => Err(e).context("failed to write standard output"),
    }
}
