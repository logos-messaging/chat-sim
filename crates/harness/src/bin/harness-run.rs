//! Runs a plan on the harness: `harness-run PLAN.json OUT_DIR`.
//!
//! On Linux a plan and its seed give one run: the process draws every random
//! number from the seed, and its time of day is the run's virtual clock.

use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    let (Some(plan), Some(out), None) = (args.next(), args.next(), args.next()) else {
        eprintln!("usage: harness-run PLAN.json OUT_DIR");
        return ExitCode::from(2);
    };
    match harness::runner::run(Path::new(&plan), Path::new(&out), host::hand_over) {
        Ok(()) => ExitCode::SUCCESS,
        Err(why) => {
            eprintln!("harness-run: {why}");
            ExitCode::from(2)
        }
    }
}

/// The process's randomness and time of day, stood in for where std, the
/// getrandom crate and OpenMLS ask libc for them.
#[cfg(target_os = "linux")]
mod host {
    use de_mls::WallClock;
    use libchat::MockClock;
    use rand_chacha::ChaCha20Rng;
    use rand_chacha::rand_core::{RngCore, SeedableRng};
    use std::sync::{Mutex, OnceLock, PoisonError};

    /// Draws from seed 0 until the run hands over its own.
    static RANDOM: Mutex<Option<ChaCha20Rng>> = Mutex::new(None);
    static CLOCK: OnceLock<MockClock> = OnceLock::new();

    pub fn hand_over(seed: u64, clock: &MockClock) {
        *RANDOM.lock().unwrap_or_else(PoisonError::into_inner) =
            Some(ChaCha20Rng::seed_from_u64(seed));
        CLOCK.set(clock.clone()).expect("a process does one run");
    }

    /// std and the getrandom crate find it by `dlsym`: the linker exports a
    /// symbol of the executable that libc also defines.
    #[unsafe(no_mangle)]
    unsafe extern "C" fn getrandom(
        buffer: *mut libc::c_void,
        length: libc::size_t,
        _flags: libc::c_uint,
    ) -> libc::ssize_t {
        let bytes = unsafe { std::slice::from_raw_parts_mut(buffer.cast::<u8>(), length) };
        let mut random = RANDOM.lock().unwrap_or_else(PoisonError::into_inner);
        random
            .get_or_insert_with(|| ChaCha20Rng::seed_from_u64(0))
            .fill_bytes(bytes);
        length as libc::ssize_t
    }

    /// Every clock but the time of day is the kernel's.
    #[unsafe(no_mangle)]
    unsafe extern "C" fn clock_gettime(
        clock: libc::clockid_t,
        time: *mut libc::timespec,
    ) -> libc::c_int {
        match (clock, CLOCK.get()) {
            (libc::CLOCK_REALTIME | libc::CLOCK_REALTIME_COARSE, Some(run)) => {
                let now = run.now();
                unsafe {
                    (*time).tv_sec = now.as_secs() as libc::time_t;
                    (*time).tv_nsec = now.subsec_nanos().into();
                }
                0
            }
            _ => unsafe { libc::syscall(libc::SYS_clock_gettime, clock, time) as libc::c_int },
        }
    }
}

#[cfg(not(target_os = "linux"))]
mod host {
    pub fn hand_over(_seed: u64, _clock: &libchat::MockClock) {}
}
