//! The hermetic runner of git, for the tests that need a repository.

use std::env;
use std::path::Path;
use std::process::Command;
use std::process::Stdio;

use crate::support::Sandbox;
use crate::support::TestResult;

impl Sandbox {
    /// Runs git with `args` in `dir` and returns what it prints, without the
    /// final line break.
    ///
    /// The command sees no configuration but git's defaults, and commits
    /// under a fixed name and date.
    #[expect(
        clippy::disallowed_methods,
        reason = "the harness finds git on its own `PATH`"
    )]
    pub(crate) fn git(&self, dir: &Path, args: &[&str]) -> TestResult<String> {
        let mut command = Command::new("git");
        command
            .args(args)
            .current_dir(dir)
            .stdin(Stdio::null())
            .env_clear()
            .env("HOME", self.home())
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_AUTHOR_NAME", "Test")
            .env("GIT_AUTHOR_EMAIL", "test@example.com")
            .env("GIT_AUTHOR_DATE", "2000-01-01T00:00:00Z")
            .env("GIT_COMMITTER_NAME", "Test")
            .env("GIT_COMMITTER_EMAIL", "test@example.com")
            .env("GIT_COMMITTER_DATE", "2000-01-01T00:00:00Z");
        if let Some(path) = env::var_os("PATH") {
            command.env("PATH", path);
        }
        let output = command.output()?;
        anyhow::ensure!(
            output.status.success(),
            "git {args:?} should succeed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let stdout = String::from_utf8(output.stdout)?;
        Ok(stdout.strip_suffix('\n').unwrap_or(&stdout).to_owned())
    }
}
