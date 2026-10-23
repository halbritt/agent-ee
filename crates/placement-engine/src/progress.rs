//! Optional progress records for the executable's NDJSON transport.
use std::cell::Cell;
use std::io::{self, Write};
use std::time::{Duration, Instant};

use anyhow::Result;
use serde_json::{Value, json};

thread_local! {
    static ENABLED: Cell<bool> = const { Cell::new(false) };
}

pub(crate) fn with_events<T>(operation: impl FnOnce() -> Result<T>) -> Result<T> {
    struct Restore(bool);
    impl Drop for Restore {
        fn drop(&mut self) {
            ENABLED.set(self.0);
        }
    }
    let _restore = Restore(ENABLED.replace(true));
    operation()
}

/// Emit only when the caller explicitly selected the event protocol.
pub fn emit(record: Value) -> Result<()> {
    if ENABLED.get() {
        let mut output = io::stdout().lock();
        serde_json::to_writer(
            &mut output,
            &json!({
                "schema":"agent-ee.rust-planning-output/1", "kind":"heartbeat", "record":record
            }),
        )?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}

/// Per-search timing; disabled progress performs no clock reads.
pub struct Clock {
    started: Option<Instant>,
    last: Cell<Option<Instant>>,
}

impl Clock {
    pub fn new() -> Self {
        let started = ENABLED.get().then(Instant::now);
        Self {
            started,
            last: Cell::new(started),
        }
    }

    /// Return elapsed seconds at most once every five seconds.
    pub fn elapsed_if_due(&self) -> Option<f64> {
        let started = self.started?;
        let now = Instant::now();
        if now.duration_since(self.last.get()?) < Duration::from_secs(5) {
            return None;
        }
        self.last.set(Some(now));
        Some(now.duration_since(started).as_secs_f64())
    }
}

impl Default for Clock {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scopes_restore_and_clocks_are_quiet_by_default() {
        assert!(!ENABLED.get());
        assert!(Clock::new().started.is_none());
        let failed: Result<()> = with_events(|| {
            assert!(ENABLED.get());
            let clock = Clock::new();
            assert!(clock.started.is_some());
            assert!(clock.elapsed_if_due().is_none());
            with_events(|| Ok(()))?;
            assert!(ENABLED.get());
            anyhow::bail!("scope failure")
        });
        assert!(failed.is_err());
        assert!(!ENABLED.get());
        assert!(Clock::new().elapsed_if_due().is_none());
    }
}
