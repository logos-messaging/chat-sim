//! A run's log on the virtual clock, and the trace records it holds, kept as
//! they are written.

use crate::log::{Record, TRACE, lines};
use de_mls::WallClock;
use libchat::MockClock;
use std::fmt;
use std::fs::File;
use std::io;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tracing::Subscriber;
use tracing_subscriber::Layer;
use tracing_subscriber::fmt::MakeWriter;
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::time::FormatTime;
use tracing_subscriber::fmt::writer::MakeWriterExt;
use tracing_subscriber::layer::SubscriberExt;

/// The trace records written and not yet taken, each with the virtual time
/// it was written at.
#[derive(Clone)]
pub(crate) struct Records {
    clock: MockClock,
    written: Arc<Mutex<Vec<(Duration, Record)>>>,
}

impl Records {
    /// The records written since the last call, in order.
    pub(crate) fn take(&self) -> Vec<(Duration, Record)> {
        std::mem::take(&mut self.written.lock().unwrap())
    }
}

impl io::Write for Records {
    // A line of the log is one write.
    fn write(&mut self, line: &[u8]) -> io::Result<usize> {
        if let Some(record) = std::str::from_utf8(line).ok().and_then(Record::read) {
            let written = (self.clock.now(), record);
            self.written.lock().unwrap().push(written);
        }
        Ok(line.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for Records {
    type Writer = Records;

    fn make_writer(&'a self) -> Records {
        self.clone()
    }
}

/// A time past the Unix epoch as a line's stamp: `2026-10-12T15:04:05.123456Z`.
pub(crate) struct Stamp(pub Duration);

impl fmt::Display for Stamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        const DAY: u64 = 24 * 60 * 60;
        let (year, month, day) = civil(self.0.as_secs() / DAY);
        let second = self.0.as_secs() % DAY;
        write!(
            f,
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:06}Z",
            second / 3600,
            second / 60 % 60,
            second % 60,
            self.0.subsec_micros()
        )
    }
}

/// The year, month and day of the Gregorian calendar `days` after 1 January
/// 1970.
fn civil(days: u64) -> (u64, u64, u64) {
    // Counted from 1 March of year 0, a year ends with its leap day, and the
    // calendar repeats every 146097 days, which are 400 years.
    let days = days + 719_468;
    let (era, day_of_era) = (days / 146_097, days % 146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    // Months from March: every five of them hold 153 days.
    let month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month + 2) / 5 + 1;
    let (month, january_or_february) = if month < 10 {
        (month + 3, 0)
    } else {
        (month - 9, 1)
    };
    (era * 400 + year_of_era + january_or_february, month, day)
}

/// The time of a line: the virtual clock's.
struct Timer(MockClock);

impl FormatTime for Timer {
    fn format_time(&self, line: &mut Writer<'_>) -> fmt::Result {
        write!(line, "{}", Stamp(self.0.now()))
    }
}

/// A subscriber that writes `file` with `clock` as the time of each line: the
/// trace records in their ops' spans, and of everything else what `LOG` asks
/// for. The records go to the returned [`Records`] as well.
pub(crate) fn on(
    file: File,
    clock: MockClock,
) -> (impl Subscriber + Send + Sync + 'static, Records) {
    let records = Records {
        clock: clock.clone(),
        written: Arc::default(),
    };
    let kept = records.clone().with_filter(|line| line.target() == TRACE);
    // The file takes each line in one write, so a run that is cut short
    // leaves every line it wrote.
    let layer = tracing_subscriber::fmt::layer()
        .with_timer(Timer(clock))
        .with_ansi(false)
        .with_writer(file.and(kept))
        .with_filter(lines());
    (tracing_subscriber::registry().with(layer), records)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stamp_is_the_civil_time_of_its_seconds() {
        let stamps = [
            (0, "1970-01-01T00:00:00.000000Z"),
            (946_684_799, "1999-12-31T23:59:59.000000Z"),
            // 2000 has a leap day, 2100 has none.
            (951_868_799, "2000-02-29T23:59:59.000000Z"),
            (951_868_800, "2000-03-01T00:00:00.000000Z"),
            (1_709_164_800, "2024-02-29T00:00:00.000000Z"),
            (1_791_817_445, "2026-10-12T15:04:05.000000Z"),
            (1_798_761_600, "2027-01-01T00:00:00.000000Z"),
            (4_107_499_200, "2100-02-28T12:00:00.000000Z"),
            (4_107_542_400, "2100-03-01T00:00:00.000000Z"),
        ];
        for (seconds, stamp) in stamps {
            assert_eq!(Stamp(Duration::from_secs(seconds)).to_string(), stamp);
        }

        let part = Duration::new(1_791_817_445, 123_456_789);
        assert_eq!(Stamp(part).to_string(), "2026-10-12T15:04:05.123456Z");
    }
}
