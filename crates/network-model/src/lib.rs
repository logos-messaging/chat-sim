//! A seeded model of a lossy network on the frames one client receives.
//!
//! [`read`] tells what an inbound payload is. [`Rules`] decide what becomes of
//! each frame that arrives: lost at a rate, cut off with its sender, withheld
//! as the one commit candidate, or passed. [`Model`] holds what passed for a
//! delay.
//!
//! A decision follows from the seed, the member's name and the frames shown
//! so far, and time is handed in: nothing here reads a clock, sleeps or
//! starts a thread. Whoever feeds a client its inbound payloads puts the
//! model in between:
//!
//! ```
//! use std::time::Duration;
//! use network_model::{Model, read};
//!
//! let mut model = Model::new(7, "saro");
//! model.rules_mut().set_loss("0.05".parse().unwrap());
//! model.set_delay(Duration::from_millis(50));
//!
//! let arrived_at = Duration::from_secs(3);
//! let payload = vec![0u8; 64];
//! // The first frame of this seed and name is not lost at this rate.
//! let dropped = model.arrive(arrived_at, read(&payload), payload);
//! assert_eq!(dropped, None);
//!
//! let due = arrived_at + Duration::from_millis(50);
//! assert_eq!(model.next_due(), Some(due));
//! let for_the_client = model.take_due(due);
//! assert_eq!(for_the_client.len(), 1);
//! ```

mod frame;
mod model;
mod rng;
mod rules;

pub use frame::{AppId, Frame, Kind, NotAnAppId, read};
pub use model::{Counts, Model};
pub use rules::{Dropped, NotARate, NotArmed, Rate, Rules, Why, Withhold};
