use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;
use crossbeam_channel::Receiver;
use logos_chat::{
    AccountAddr, AuthService, ChatClient, ConversationClass, ConversationStore,
    EmbeddedLogosDelivery, Event, GroupMetadata, NodeState, RegistrationService, Transport,
};
use message_types::ContentId;
use network_model::AppId;
use serde::{Deserialize, Serialize};

use crate::transport::modelled::{NetworkModel, NetworkState};
use crate::utils::now;

/// One-line snippet of a message body, for the reply preview.
fn snippet_of(body: &str) -> String {
    let line = body.lines().next().unwrap_or("");
    let short: String = line.chars().take(24).collect();
    if line.chars().count() > 24 {
        format!("{short}…")
    } else {
        short
    }
}

/// A reply's display body: the `↩ preview` line above the reply text. The one
/// place this shape is built, so inbound render and outbound echo match.
fn reply_body(preview: &str, body: &str) -> String {
    format!("↩ {preview}\n{body}")
}

/// What `/node` shows of a delivery node's state, a line each.
fn node_lines(node: &NodeState) -> [String; 8] {
    let count =
        |count: Option<u64>| count.map_or_else(|| "unknown".to_string(), |count| count.to_string());
    [
        "── Delivery Node ──".to_string(),
        format!("Address: {}", node.address),
        format!("Peers: {}", count(node.peers)),
        format!("Mesh peers: {}", count(node.mesh_peers)),
        format!("Dials made: {}", count(node.dials_ok)),
        format!("Dials failed: {}", count(node.dials_failed)),
        format!("Publishes refused: {}", node.publish_refused),
        format!("Received frames dropped: {}", node.inbound_dropped),
    ]
}

/// The refusal of a post to a chat this session cannot send to.
const NOT_SENDABLE: &str = "Not an active conversation — it may be a stale one from a previous \
     session, or you're no longer a member.";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DisplayMessage {
    pub from_self: bool,
    pub content: String,
    pub timestamp: u64,
    pub message_id: Option<String>,
    #[serde(default)]
    pub delivered_to: Vec<String>,
    /// Who this message is attributed to: our own account vs. a peer (by account
    /// address). The display name is resolved from the account at render time.
    pub origin: MessageOrigin,
}

impl DisplayMessage {
    fn new(from_self: bool, content: String, origin: MessageOrigin) -> Self {
        Self {
            from_self,
            content,
            timestamp: now(),
            message_id: None,
            delivered_to: Vec::new(),
            origin,
        }
    }
}

/// Attribution of a displayed message. `Own` is our own account (any of its
/// installations); `Foreign` carries the sender's account address, which the
/// app maps to a display name. (The client decodes participant → account; the
/// app resolves account → name.)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MessageOrigin {
    Own,
    Foreign(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatSession {
    pub chat_id: String,
    pub nickname: Option<String>,
    pub kind: ConversationClass,
    pub messages: Vec<DisplayMessage>,
}

impl ChatSession {
    /// Human-readable label: nickname if set, otherwise the first 8 chars of the chat ID.
    pub fn display_name(&self) -> &str {
        self.nickname
            .as_deref()
            .unwrap_or_else(|| &self.chat_id[..8.min(self.chat_id.len())])
    }
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct AppState {
    /// Keyed by chat_id (conversation ID).
    pub chats: HashMap<String, ChatSession>,
    /// Holds the active chat_id.
    pub active_chat: Option<String>,
}

/// What an input came to. Each mode shows it its own way.
#[derive(Debug)]
pub enum Outcome {
    Done {
        text: String,
        /// The conversation the input made or acted on.
        convo: Option<String>,
        /// The delivery node's state, when the input asked for it.
        node: Option<NodeState>,
        /// The network model's state, when the input asked for it or set it.
        network: Option<Box<NetworkState>>,
    },
    /// The input changed nothing, and the text says why.
    Refused(String),
    Quit,
}

impl Outcome {
    fn done(text: impl Into<String>) -> Self {
        Self::Done {
            text: text.into(),
            convo: None,
            node: None,
            network: None,
        }
    }

    fn in_convo(text: impl Into<String>, chat_id: &str) -> Self {
        Self::Done {
            text: text.into(),
            convo: Some(chat_id.to_string()),
            node: None,
            network: None,
        }
    }

    fn refused(text: &str) -> Self {
        Self::Refused(text.to_string())
    }
}

pub struct ChatApp<T, R, A, S>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send + 'static,
{
    pub client: ChatClient<T, R, A, S>,
    events: Receiver<Event>,
    /// The embedded delivery node, on the transport that runs one.
    node: Option<EmbeddedLogosDelivery>,
    /// The model of a lossy network between the transport and the client, in
    /// a run that has one.
    network: Option<NetworkModel>,
    pub state: AppState,
    /// Whether the active chat can accept outbound content this session. Mirrors
    /// [`ChatClient::can_send`] for the active chat; `false` for a chat
    /// restored from a previous session that the MLS client can't reload yet.
    is_active: bool,
    /// Ephemeral command output — not persisted, cleared on chat switch.
    command_output: Vec<DisplayMessage>,
    pub status: String,
    pub user_name: String,
    state_path: PathBuf,
}

impl<T, R, A, S> ChatApp<T, R, A, S>
where
    T: Transport,
    R: RegistrationService + Clone + Send + 'static,
    A: AuthService + Send + 'static,
    S: ConversationStore + Send,
{
    pub fn new(
        client: ChatClient<T, R, A, S>,
        events: Receiver<Event>,
        node: Option<EmbeddedLogosDelivery>,
        network: Option<NetworkModel>,
        user_name: &str,
        data_dir: &Path,
    ) -> Result<Self> {
        fs::create_dir_all(data_dir)?;
        let state_path = data_dir.join(format!("{user_name}_state.json"));
        let state = Self::load_state(&state_path);

        let chat_count = state.chats.len();
        let status = if chat_count == 0 {
            format!("Welcome, {user_name}! Type /help for commands.")
        } else {
            format!(
                "Welcome back, {user_name}! {chat_count} chat(s) loaded — read-only from a previous \
                 session; start a new /dm or /new to chat. Type /help."
            )
        };

        let mut app = Self {
            client,
            events,
            node,
            network,
            state,
            is_active: false,
            command_output: Vec::new(),
            status,
            user_name: user_name.to_string(),
            state_path,
        };
        // Restored chats can't be reloaded into the MLS client yet, so open on the
        // roster (with read-only flags) rather than a dead active chat.
        app.state.active_chat = None;
        app.show_chats_list();
        Ok(app)
    }

    fn load_state(path: &Path) -> AppState {
        if path.exists()
            && let Ok(contents) = fs::read_to_string(path)
            && let Ok(state) = serde_json::from_str(&contents)
        {
            return state;
        }
        AppState::default()
    }

    fn save_state(&self) -> Result<()> {
        let json = serde_json::to_string_pretty(&self.state)?;
        fs::write(&self.state_path, json)?;
        Ok(())
    }

    /// The delivery node's state, or `None` on a transport without a node.
    pub fn node_state(&self) -> Result<Option<NodeState>> {
        self.node
            .as_ref()
            .map(|node| node.node_state().map_err(|e| anyhow::anyhow!("{e}")))
            .transpose()
    }

    /// The model of the network, or `None` in a run without one.
    pub fn network(&self) -> Option<&NetworkModel> {
        self.network.as_ref()
    }

    /// The app id this client stamps on the frames it publishes to `chat_id`,
    /// once the network model has seen it publish one there, or `None` in a run
    /// without the model.
    pub fn app_id(&self, chat_id: &str) -> Option<AppId> {
        self.network.as_ref()?.app_id(chat_id)
    }

    fn set_active_chat(&mut self, chat_id: Option<String>) {
        self.is_active = chat_id
            .as_deref()
            .map(|id| self.client.can_send(id))
            .unwrap_or(false);
        self.state.active_chat = chat_id;
        self.command_output.clear();
    }

    /// Render the chat list; chats restored from a previous session that the MLS
    /// client can't reload are flagged read-only.
    fn show_chats_list(&mut self) {
        self.command_output.clear();
        let sessions: Vec<_> = self.state.chats.values().cloned().collect();
        if sessions.is_empty() {
            self.add_system_message("No chats yet. Use /dm or /new to start one.");
            return;
        }
        self.add_system_message(&format!("── Your Chats ({}) ──", sessions.len()));
        for s in &sessions {
            let active = self.state.active_chat.as_deref() == Some(&s.chat_id);
            let read_only = !self.client.can_send(&s.chat_id);
            let mut tags = String::new();
            if active {
                tags.push_str(" (active)");
            }
            if read_only {
                tags.push_str(" (read-only)");
            }
            let label = format!(
                "  • [{:?}] {} ({}){tags}",
                s.kind,
                s.display_name(),
                &s.chat_id[..8.min(s.chat_id.len())]
            );
            self.add_system_message(&label);
        }
    }

    /// Insert a freshly created conversation.
    fn add_session(&mut self, chat_id: String, kind: ConversationClass, nickname: Option<String>) {
        self.state.chats.insert(
            chat_id.clone(),
            ChatSession {
                chat_id,
                nickname,
                kind,
                messages: Vec::new(),
            },
        );
    }

    /// Insert a freshly created conversation and make it active.
    fn start_session(
        &mut self,
        chat_id: String,
        kind: ConversationClass,
        nickname: Option<String>,
    ) {
        self.add_session(chat_id.clone(), kind, nickname);
        self.set_active_chat(Some(chat_id));
    }

    /// Find a chat_id by nickname (exact) or chat_id prefix.
    fn resolve_chat_id(&self, query: &str) -> Option<&str> {
        // Exact nickname match first.
        if let Some((id, _)) = self
            .state
            .chats
            .iter()
            .find(|(_, s)| s.nickname.as_deref() == Some(query))
        {
            return Some(id.as_str());
        }
        // Fall back to chat_id prefix.
        self.state
            .chats
            .keys()
            .find(|id| id.starts_with(query))
            .map(String::as_str)
    }

    /// The client's events, each for [`Self::process_event`].
    pub fn events(&self) -> &Receiver<Event> {
        &self.events
    }

    /// Apply one event taken from [`Self::events`]. The active chat stays
    /// where the inputs put it.
    pub fn process_event(&mut self, event: Event) -> Result<()> {
        self.handle_event(event);
        self.save_state()
    }

    fn handle_event(&mut self, event: Event) {
        match event {
            Event::ConversationStarted { convo_id, class } => {
                let chat_id = convo_id.to_string();
                if self.state.chats.contains_key(&chat_id) {
                    return;
                }
                let label = chat_id[..8.min(chat_id.len())].to_string();
                self.status = format!("New {class:?} ({label})! Use /nickname to name it.");
                self.add_session(chat_id, class, None);
            }
            Event::MessageReceived {
                convo_id,
                content: bytes,
                sender,
                message_id,
                ..
            } => {
                let chat_id = convo_id.to_string();
                // The client resolved the credential to an account; classify by it.
                let account = sender.account().to_string();
                let origin = if account == self.client.addr() {
                    MessageOrigin::Own
                } else {
                    MessageOrigin::Foreign(account)
                };
                // The client hands us opaque bytes; the content format is ours
                // to apply. Decoding never fails — every body renders as
                // something.
                let decoded = message_types::decode(&bytes);
                let Some(session) = self.state.chats.get_mut(&chat_id) else {
                    return;
                };
                let mut body = decoded.content.to_string();
                // Resolve the replied-to message to a preview; the app owns this,
                // the id is opaque to the core.
                if let Some(target) = decoded.in_reply_to {
                    let target = target.as_str();
                    let preview = session
                        .messages
                        .iter()
                        .find(|m| m.message_id.as_deref() == Some(target))
                        .map(|m| snippet_of(&m.content))
                        .unwrap_or_else(|| format!("re: {}", &target[..8.min(target.len())]));
                    body = reply_body(&preview, &body);
                }
                let mut message = DisplayMessage::new(false, body, origin);
                message.message_id = Some(message_id);
                session.messages.push(message);
            }
            Event::MessageAcked {
                convo_id,
                message_id,
                acked_by,
            } => {
                let Some(session) = self.state.chats.get_mut(convo_id.as_ref()) else {
                    return;
                };
                let Some(message) = session
                    .messages
                    .iter_mut()
                    .find(|m| m.message_id.as_deref() == Some(message_id.as_str()))
                else {
                    return; // sent before this session, or not ours
                };
                let id = acked_by.to_string();
                let peer = format!("{}…", &id[..8.min(id.len())]);
                if !message.delivered_to.contains(&peer) {
                    message.delivered_to.push(peer);
                }
            }
            Event::MessageMissing {
                convo_id,
                sender_hint,
                ..
            } => {
                let Some(session) = self.state.chats.get(convo_id.as_ref()) else {
                    return;
                };
                // The hint is not authenticated (see `Event::MessageMissing`),
                // so name the author loosely rather than as an established fact.
                let id = sender_hint.to_string();
                let author = format!("{}…", &id[..8.min(id.len())]);
                self.status = format!(
                    "A message from {author} never arrived in '{}'.",
                    session.display_name()
                );
            }
            Event::ConversationMembersChanged { convo_id } => {
                let chat_id = convo_id.to_string();
                if let Some(session) = self.state.chats.get(&chat_id) {
                    self.status = format!("Membership changed in {}.", session.display_name());
                }
                // The commit may be the one that removed us; `is_active` is
                // otherwise only recomputed on a chat switch.
                if self.state.active_chat.as_deref() == Some(&chat_id) {
                    self.is_active = self.client.can_send(&chat_id);
                }
            }
            Event::InboundError { message } => {
                self.status = format!("Could not process incoming message: {message}");
            }
            _ => {}
        }
    }

    /// Send a plain-text message to the active chat.
    pub fn send_message(&mut self, content: &str) -> Result<Outcome> {
        let chat_id = self.active_sendable_chat()?;
        self.send_text(&chat_id, content)
    }

    /// Send a plain-text message to `chat_id`.
    fn send_text(&mut self, chat_id: &str, content: &str) -> Result<Outcome> {
        if !self.client.can_send(chat_id) {
            anyhow::bail!(NOT_SENDABLE);
        }
        let bytes = message_types::encode_text(content)?;
        let message_id = self
            .client
            .send_message(chat_id, &bytes)
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        self.echo_sent(chat_id, message_id, content.to_string())?;
        Ok(Outcome::in_convo("Message sent", chat_id))
    }

    /// Reply to `in_reply_to` in the active chat.
    fn send_reply(&mut self, in_reply_to: &ContentId, content: &str, preview: &str) -> Result<()> {
        let chat_id = self.active_sendable_chat()?;
        let bytes = message_types::encode_reply(in_reply_to, content)?;
        let message_id = self
            .client
            .send_message(&chat_id, &bytes)
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        self.echo_sent(&chat_id, message_id, reply_body(preview, content))
    }

    /// The active chat, if there is one and it can still be sent to.
    fn active_sendable_chat(&self) -> Result<String> {
        let chat_id = self
            .state
            .active_chat
            .clone()
            .ok_or_else(|| anyhow::anyhow!("No active chat. Use /dm or /new first."))?;

        if !self.is_active {
            anyhow::bail!(NOT_SENDABLE);
        }
        Ok(chat_id)
    }

    /// Put our own message in our own view; the wire carries no copy back.
    fn echo_sent(&mut self, chat_id: &str, message_id: String, body: String) -> Result<()> {
        if let Some(session) = self.state.chats.get_mut(chat_id) {
            let mut message = DisplayMessage::new(true, body, MessageOrigin::Own);
            message.message_id = Some(message_id);
            session.messages.push(message);
        }
        self.save_state()?;
        Ok(())
    }

    pub fn add_system_message(&mut self, content: &str) {
        self.command_output.push(DisplayMessage::new(
            true,
            content.to_string(),
            MessageOrigin::Own,
        ));
    }

    /// Take the command output written so far, a line an entry.
    pub fn take_command_output(&mut self) -> Vec<String> {
        self.command_output
            .drain(..)
            .map(|message| message.content)
            .collect()
    }

    pub fn handle_command(&mut self, cmd: &str) -> Result<Outcome> {
        let parts: Vec<&str> = cmd.splitn(2, ' ').collect();
        let command = parts[0];
        let args = parts.get(1).copied().unwrap_or("");

        match command {
            "/help" => {
                self.add_system_message("── Commands ──");
                self.add_system_message("/account - Show your account address");
                self.add_system_message("/dm <address> - Start a direct (1:1) chat");
                self.add_system_message("/new <name> [address...] - Create a group chat");
                self.add_system_message("/add <address>... - Add people to the active group");
                self.add_system_message("/remove <address> - Remove someone from the active group");
                self.add_system_message("/members - List members of the active conversation");
                self.add_system_message("/send <name|id> <text> - Send a message to a chat");
                self.add_system_message("/reply <text> - Reply to the latest message");
                self.add_system_message("/nickname <name> - Name the active chat");
                self.add_system_message("/chats - List all chats");
                self.add_system_message("/switch <name|id> - Switch active chat");
                self.add_system_message("/delete <name|id> - Delete a chat");
                self.add_system_message("/status - Show connection status");
                self.add_system_message("/node - Show the delivery node's address and peers");
                self.add_system_message(
                    "/network [cut <app id>...|heal|withhold candidate] - Show or set the network \
                     model",
                );
                self.add_system_message("/clear - Clear current chat messages");
                self.add_system_message("/quit - Exit");
                Ok(Outcome::done("Help displayed"))
            }
            "/account" => {
                let address = self.client.addr().to_string();
                let signer = self.client.installation_name();
                self.add_system_message("── Your Account Address ──");
                self.add_system_message(&address);
                self.add_system_message(&signer);
                Ok(Outcome::Done {
                    text: "Account address shown".to_string(),
                    convo: None,
                    node: None,
                    network: None,
                })
            }
            "/dm" => {
                let address = args.trim();
                if address.is_empty() {
                    return Ok(Outcome::refused("Usage: /dm <address>"));
                }
                let chat_id = self
                    .client
                    .create_direct_conversation(address)
                    .map_err(|e| anyhow::anyhow!("{e:?}"))?;
                let label = chat_id[..8.min(chat_id.len())].to_string();
                self.start_session(chat_id.clone(), ConversationClass::Dm, None);
                self.save_state()?;
                self.status = format!("Direct chat started ({label}). Say hello!");
                Ok(Outcome::in_convo(format!("DM started ({label})"), &chat_id))
            }
            "/new" => {
                // First token is the group name (required); any remaining tokens
                // are addresses to invite at creation.
                let mut tokens = args.split_whitespace();
                let Some(name) = tokens.next().map(str::to_string) else {
                    return Ok(Outcome::refused("Usage: /new <name> [address...]"));
                };
                // The creator is already a member; drop self and any repeats so we
                // don't propose a duplicate signature key (which MLS rejects).
                let my_addr = self.client.addr().to_string();
                let mut members: Vec<&str> = tokens.filter(|a| *a != my_addr).collect();
                members.sort_unstable();
                members.dedup();
                let chat_id = self
                    .client
                    .create_group_conversation(&members, GroupMetadata::new(name.clone(), ""))
                    .map_err(|e| anyhow::anyhow!("{e:?}"))?;
                let label = chat_id[..8.min(chat_id.len())].to_string();
                self.start_session(chat_id.clone(), ConversationClass::Group, Some(name));
                self.save_state()?;
                let msg = if members.is_empty() {
                    format!("Group created ({label}).")
                } else {
                    format!(
                        "Group created ({label}); {} invite(s) pending.",
                        members.len()
                    )
                };
                self.status = msg.clone();
                Ok(Outcome::in_convo(msg, &chat_id))
            }
            "/add" => {
                let mut addresses: Vec<&str> = args.split_whitespace().collect();
                if addresses.is_empty() {
                    return Ok(Outcome::refused("Usage: /add <address>..."));
                }
                let chat_id = self.state.active_chat.as_deref().ok_or_else(|| {
                    anyhow::anyhow!("No active conversation. Use /new to create a group.")
                })?;
                // DMs are 1:1 and reject adds at the protocol level; refuse early
                // with a friendly hint rather than surfacing UnsupportedFunction.
                if self.state.chats.get(chat_id).map(|s| s.kind) == Some(ConversationClass::Dm) {
                    return Ok(Outcome::refused(
                        "DMs are 1:1 — start a group with /new to add people.",
                    ));
                }
                // Adding a signature key already in the group (yourself, or a
                // member/pending invite) makes MLS reject the commit with
                // DuplicateSignatureKey. Catch it here as a friendly no-op.
                if addresses.contains(&self.client.addr()) {
                    return Ok(Outcome::refused(
                        "That's your own address — you're already in the group.",
                    ));
                }
                addresses.sort_unstable();
                addresses.dedup();
                let already_present = self
                    .client
                    .members(chat_id)
                    .into_iter()
                    .chain(self.client.pending_members(chat_id))
                    .flatten()
                    .map(|m| m.account.to_string())
                    .find(|account| addresses.contains(&account.as_str()));
                if let Some(account) = already_present {
                    // Among several addresses, the one is named.
                    let who = if addresses.len() == 1 {
                        "That account".to_string()
                    } else {
                        format!("{}…", &account[..16.min(account.len())])
                    };
                    return Ok(Outcome::Refused(format!(
                        "{who} is already in the group (or its invite is pending)."
                    )));
                }
                self.client
                    .add_group_participants(chat_id, &addresses)
                    .map_err(|e| anyhow::anyhow!("{e:?}"))?;
                self.status = "Invite pending — the group will commit it shortly.".to_string();
                Ok(Outcome::in_convo("Invite pending", chat_id))
            }
            "/remove" => {
                let address = args.trim();
                if address.is_empty() {
                    return Ok(Outcome::refused("Usage: /remove <address>"));
                }
                let chat_id = self
                    .state
                    .active_chat
                    .clone()
                    .ok_or_else(|| anyhow::anyhow!("No active conversation."))?;
                self.client
                    .remove_group_participants(&chat_id, &[address])
                    .map_err(|e| anyhow::anyhow!("{e}"))?;
                // The group votes on the removal; the member stays on the
                // roster until the commit ejecting them lands.
                let msg = "Removal pending — the group will commit it shortly.".to_string();
                self.status = msg.clone();
                Ok(Outcome::in_convo(msg, &chat_id))
            }
            "/members" => {
                let chat_id = self
                    .state
                    .active_chat
                    .clone()
                    .ok_or_else(|| anyhow::anyhow!("No active conversation."))?;
                let participants = self
                    .client
                    .participants(&chat_id)
                    .map_err(|e| anyhow::anyhow!("{e:?}"))?;
                let pending = self
                    .client
                    .pending_members(&chat_id)
                    .map_err(|e| anyhow::anyhow!("{e:?}"))?;
                // One row per account; an account with a committed installation is not
                // also listed as pending.
                let joined: BTreeSet<String> =
                    participants.iter().map(AccountAddr::to_string).collect();
                let invited: BTreeSet<String> = pending
                    .iter()
                    .map(|m| m.account.to_string())
                    .filter(|account| !joined.contains(account))
                    .collect();
                let total = joined.len() + invited.len();
                let my_addr = self.client.addr().to_string();
                self.add_system_message(&format!("── Members ({total}) ──"));
                let rows = joined
                    .iter()
                    .map(|account| (account, false))
                    .chain(invited.iter().map(|account| (account, true)));
                for (id, is_pending) in rows {
                    let short = &id[..16.min(id.len())];
                    let mut tags = String::new();
                    if *id == my_addr {
                        tags.push_str(" (you)");
                    }
                    if is_pending {
                        tags.push_str(" (pending)");
                    }
                    self.add_system_message(&format!("  • {short}…{tags}"));
                }
                Ok(Outcome::in_convo(format!("{total} member(s)"), &chat_id))
            }
            "/send" => {
                let (target, text) = args.split_once(' ').unwrap_or((args, ""));
                if target.is_empty() || text.is_empty() {
                    return Ok(Outcome::refused("Usage: /send <nickname|id-prefix> <text>"));
                }
                let chat_id = self
                    .resolve_chat_id(target)
                    .map(str::to_string)
                    .ok_or_else(|| anyhow::anyhow!("No chat matching '{target}'."))?;
                self.send_text(&chat_id, text)
            }
            "/reply" => {
                let text = args.trim();
                if text.is_empty() {
                    return Ok(Outcome::refused("Usage: /reply <text>"));
                }
                let chat_id = self
                    .state
                    .active_chat
                    .clone()
                    .ok_or_else(|| anyhow::anyhow!("No active chat. Use /dm or /new first."))?;
                // Reply to the most recent message with an id: an input line has
                // no cursor to pick another.
                let target = self
                    .state
                    .chats
                    .get(&chat_id)
                    .and_then(|s| s.messages.iter().rev().find(|m| m.message_id.is_some()))
                    .map(|m| {
                        (
                            ContentId::new(m.message_id.clone().expect("filtered on Some")),
                            snippet_of(&m.content),
                        )
                    });
                let Some((target, preview)) = target else {
                    return Ok(Outcome::refused("Nothing to reply to yet."));
                };
                self.send_reply(&target, text, &preview)?;
                Ok(Outcome::in_convo("Reply sent", &chat_id))
            }
            "/nickname" => {
                if args.is_empty() {
                    return Ok(Outcome::refused("Usage: /nickname <name>"));
                }
                let chat_id = self
                    .state
                    .active_chat
                    .clone()
                    .ok_or_else(|| anyhow::anyhow!("No active chat."))?;
                let session = self
                    .state
                    .chats
                    .get_mut(&chat_id)
                    .ok_or_else(|| anyhow::anyhow!("Chat session not found."))?;
                session.nickname = Some(args.to_string());
                self.save_state()?;
                self.status = format!("Chat named '{args}'.");
                Ok(Outcome::in_convo(
                    format!("Nickname set to '{args}'"),
                    &chat_id,
                ))
            }
            "/chats" => {
                self.show_chats_list();
                let n = self.state.chats.len();
                Ok(Outcome::done(if n == 0 {
                    "No chats yet".to_string()
                } else {
                    format!("{n} chat(s)")
                }))
            }
            "/switch" => {
                if args.is_empty() {
                    return Ok(Outcome::refused("Usage: /switch <nickname|id-prefix>"));
                }
                let chat_id = self
                    .resolve_chat_id(args)
                    .map(str::to_string)
                    .ok_or_else(|| anyhow::anyhow!("No chat matching '{args}'."))?;
                let label = self.state.chats[&chat_id].display_name().to_string();
                self.set_active_chat(Some(chat_id.clone()));
                self.save_state()?;
                self.status = format!("Switched to '{label}'.");
                Ok(Outcome::in_convo(
                    format!("Switched to '{label}'"),
                    &chat_id,
                ))
            }
            "/delete" => {
                if args.is_empty() {
                    return Ok(Outcome::refused("Usage: /delete <nickname|id-prefix>"));
                }
                let chat_id = self
                    .resolve_chat_id(args)
                    .map(str::to_string)
                    .ok_or_else(|| anyhow::anyhow!("No chat matching '{args}'."))?;
                let label = self.state.chats[&chat_id].display_name().to_string();
                self.state.chats.remove(&chat_id);
                if self.state.active_chat.as_deref() == Some(&chat_id) {
                    self.state.active_chat = self.state.chats.keys().next().cloned();
                }
                self.save_state()?;
                self.status = format!("Deleted '{label}'.");
                Ok(Outcome::in_convo(format!("Deleted '{label}'"), &chat_id))
            }
            "/status" => {
                let active_label = self
                    .state
                    .active_chat
                    .as_ref()
                    .and_then(|id| self.state.chats.get(id))
                    .map(|s| {
                        format!(
                            "{} ({})",
                            s.display_name(),
                            &s.chat_id[..8.min(s.chat_id.len())]
                        )
                    })
                    .unwrap_or_else(|| "none".to_string());
                let status = format!(
                    "User: {}\nIdentity: {}\nChats: {}\nActive: {}",
                    self.user_name,
                    self.client.installation_name(),
                    self.state.chats.len(),
                    active_label,
                );
                Ok(Outcome::done(status))
            }
            "/node" => {
                let Some(node) = self.node_state()? else {
                    return Ok(Outcome::refused("This transport runs no delivery node."));
                };
                for line in node_lines(&node) {
                    self.add_system_message(&line);
                }
                Ok(Outcome::Done {
                    text: "Node state shown".to_string(),
                    convo: None,
                    node: Some(node),
                    network: None,
                })
            }
            "/network" => {
                let Some(network) = &self.network else {
                    return Ok(Outcome::refused(
                        "The network model is off. Start with --network-seed to turn it on.",
                    ));
                };
                let mut words = args.split_whitespace();
                let text = match (words.next(), words.next()) {
                    (None, _) => "Network model shown".to_string(),
                    (Some("cut"), Some(first)) => {
                        let mut senders = Vec::new();
                        for word in std::iter::once(first).chain(words) {
                            match word.parse::<AppId>() {
                                Ok(sender) => senders.push(sender),
                                Err(e) => {
                                    return Ok(Outcome::Refused(format!(
                                        "'{word}' is no app id: {e}."
                                    )));
                                }
                            }
                        }
                        network.cut(senders);
                        "Cut set".to_string()
                    }
                    (Some("heal"), None) => {
                        network.heal();
                        "Cut healed".to_string()
                    }
                    (Some("withhold"), Some("candidate")) if words.next().is_none() => {
                        if let Err(e) = network.withhold_next_candidate() {
                            return Ok(Outcome::Refused(format!(
                                "No commit candidate is withheld: {e}."
                            )));
                        }
                        "The next commit candidate is withheld".to_string()
                    }
                    _ => {
                        return Ok(Outcome::refused(
                            "Usage: /network [cut <app id>... | heal | withhold candidate]",
                        ));
                    }
                };
                Ok(Outcome::Done {
                    text,
                    convo: None,
                    node: None,
                    network: Some(Box::new(network.state())),
                })
            }
            "/clear" => {
                if let Some(active) = &self.state.active_chat.clone()
                    && let Some(session) = self.state.chats.get_mut(active)
                {
                    session.messages.clear();
                    self.save_state()?;
                }
                Ok(Outcome::Done {
                    text: "Messages cleared".to_string(),
                    convo: self.state.active_chat.clone(),
                    node: None,
                    network: None,
                })
            }
            "/quit" => Ok(Outcome::Quit),
            _ => Ok(Outcome::Refused(format!(
                "Unknown command: {command}. Type /help for commands."
            ))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_lines_of_a_node_name_each_count_as_its_own() {
        let node = NodeState {
            address: "/ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm".into(),
            peers: Some(6),
            mesh_peers: Some(4),
            dials_ok: Some(7),
            dials_failed: Some(1),
            publish_refused: 2,
            inbound_dropped: 3,
        };
        assert_eq!(
            node_lines(&node),
            [
                "── Delivery Node ──",
                "Address: /ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm",
                "Peers: 6",
                "Mesh peers: 4",
                "Dials made: 7",
                "Dials failed: 1",
                "Publishes refused: 2",
                "Received frames dropped: 3",
            ]
        );
    }

    #[test]
    fn a_count_the_node_does_not_give_is_shown_as_unknown() {
        let node = NodeState {
            address: String::new(),
            peers: None,
            mesh_peers: Some(4),
            dials_ok: None,
            dials_failed: None,
            publish_refused: 0,
            inbound_dropped: 0,
        };
        assert_eq!(node_lines(&node)[2..4], ["Peers: unknown", "Mesh peers: 4"]);
        assert_eq!(
            node_lines(&node)[4..6],
            ["Dials made: unknown", "Dials failed: unknown"]
        );
    }
}
