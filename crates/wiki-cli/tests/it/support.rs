//! Helpers shared by the tests in this binary: the hermetic runner of `wiki`.

use std::env;
use std::fmt;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::process::ExitStatus;
use std::process::Stdio;

use tempfile::TempDir;

mod build_script;
mod git;

/// The result a fallible test returns.
pub(crate) type TestResult<T = ()> = anyhow::Result<T>;

/// A test's own root directory, holding the home, temporary and working
/// directories the binary runs with.
pub(crate) struct Sandbox {
    _guard: TempDir,
    root: PathBuf,
}

impl Sandbox {
    /// Creates the root and its three directories.
    pub(crate) fn new() -> TestResult<Self> {
        let guard = tempfile::Builder::new().prefix("wiki-test-").tempdir()?;
        // A temporary directory is often behind a symlink, and the binary
        // prints real paths.
        let root = guard.path().canonicalize()?;
        for dir in ["home", "tmp", "work"] {
            fs::create_dir(root.join(dir))?;
        }
        Ok(Self {
            _guard: guard,
            root,
        })
    }

    /// Returns the home directory the binary sees.
    pub(crate) fn home(&self) -> PathBuf {
        self.root.join("home")
    }

    /// Returns the directory the binary runs in.
    pub(crate) fn work(&self) -> PathBuf {
        self.root.join("work")
    }

    /// Builds the command that runs the binary with `args`.
    ///
    /// The environment holds only `HOME`, `TMPDIR` and a fixed `COLUMNS`, plus
    /// the caller's `PATH` and `LLVM_PROFILE_FILE`, which keeps the coverage
    /// profile of the binary out of the working directory.
    #[expect(
        clippy::disallowed_methods,
        reason = "the harness passes two variables of its own environment to the binary"
    )]
    pub(crate) fn command(&self, args: &[&str]) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_wiki"));
        command
            .args(args)
            .current_dir(self.work())
            .stdin(Stdio::null())
            .env_clear()
            .env("HOME", self.home())
            .env("TMPDIR", self.root.join("tmp"))
            .env("COLUMNS", "80");
        for name in ["PATH", "LLVM_PROFILE_FILE"] {
            if let Some(value) = env::var_os(name) {
                command.env(name, value);
            }
        }
        command
    }

    /// Runs the binary with `args` and returns what a user would observe.
    pub(crate) fn run(&self, args: &[&str]) -> TestResult<RunOutput> {
        self.observe(self.command(args))
    }

    /// Runs `command` and returns what a user would observe.
    pub(crate) fn observe(&self, mut command: Command) -> TestResult<RunOutput> {
        let output = command.output()?;
        Ok(RunOutput {
            stdout: self.normalize(output.stdout)?,
            stderr: self.normalize(output.stderr)?,
            status: output.status,
        })
    }

    /// Decodes a stream, writes this sandbox's root as `[ROOT]`, and drops the
    /// trailing spaces clap leaves on some help lines.
    fn normalize(&self, stream: Vec<u8>) -> TestResult<String> {
        let text = String::from_utf8(stream)?.replace(&self.root.display().to_string(), "[ROOT]");
        Ok(text
            .lines()
            .map(|line| format!("{}\n", line.trim_end()))
            .collect())
    }
}

/// What a run of the binary showed: both streams and the exit status.
#[must_use]
pub(crate) struct RunOutput {
    pub(crate) stdout: String,
    pub(crate) stderr: String,
    pub(crate) status: ExitStatus,
}

/// Renders the run as one string for a snapshot: stdout, then stderr under a
/// rule when it is not empty, then the status when it is not zero.
impl fmt::Display for RunOutput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.stdout)?;
        if !self.stderr.is_empty() {
            writeln!(f, "------- stderr -------")?;
            f.write_str(&self.stderr)?;
        }
        match self.status.code() {
            Some(0) => Ok(()),
            Some(code) => writeln!(f, "[exit status: {code}]"),
            None => writeln!(f, "[{}]", self.status),
        }
    }
}
