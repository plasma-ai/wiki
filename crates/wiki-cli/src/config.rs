//! The values the binary reads from its environment.

use std::env;
use std::path::PathBuf;

/// The environment, read once at startup.
#[derive(Debug)]
pub(crate) struct Config {
    /// The user's home directory, when `HOME` names one.
    pub(crate) home: Option<PathBuf>,
}

impl Config {
    /// Reads the environment variables the binary uses.
    #[expect(
        clippy::disallowed_methods,
        reason = "this is the one place that reads the environment"
    )]
    pub(crate) fn from_env() -> Self {
        Self {
            home: env::var_os("HOME")
                .filter(|home| !home.is_empty())
                .map(PathBuf::from),
        }
    }
}
