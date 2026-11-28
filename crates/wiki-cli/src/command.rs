//! The commands, their dispatch, and the result every command returns.

use clap::Subcommand;
use wiki_core::skill::InstallSkillsError;
use wiki_core::skill::LinkSkillsError;

use crate::config::Config;
use crate::output::Output;
use crate::output::WriteStdoutError;

mod completion;
mod install;

// A variant carries no doc comment: clap would take it over the help written
// on the command's `Args` type.
#[derive(Debug, Subcommand)]
pub(crate) enum Command {
    Install(install::InstallArgs),
    Completion(completion::CompletionArgs),
}

impl Command {
    /// Runs the command and returns its outcome.
    pub(crate) fn run(
        &self,
        output: &mut Output,
        config: &Config,
    ) -> Result<CommandOutcome, CommandError> {
        match self {
            Self::Install(args) => install::run(output, args, config),
            Self::Completion(args) => completion::run(output, args),
        }
    }
}

/// A command's own answer, which the exit status reports.
#[derive(Debug)]
#[must_use]
pub(crate) enum CommandOutcome {
    /// The command did what was asked.
    Success,
}

/// Every failure the binary reports, one variant per kind.
#[derive(Debug, thiserror::Error)]
pub(crate) enum CommandError {
    /// A write to stdout failed.
    #[error(transparent)]
    Stdout(#[from] WriteStdoutError),
    /// The bundled skills could not be installed.
    #[error(transparent)]
    InstallSkills(#[from] InstallSkillsError),
    /// The skills of the development clone could not be linked.
    #[error(transparent)]
    LinkSkills(#[from] LinkSkillsError),
    /// The environment names no home directory.
    #[error("cannot find the home directory, because `HOME` is not set")]
    NoHomeDirectory,
}

impl CommandError {
    /// Returns the remedies the CLI knows for this failure.
    pub(crate) fn hints(&self) -> &'static [&'static str] {
        match self {
            Self::NoHomeDirectory => &["pass `--project` to install into the current directory"],
            Self::LinkSkills(LinkSkillsError::ReadSource(_)) => &[
                "`--link` works only for a binary built in a development clone; run `install` without it to copy the skill",
            ],
            Self::Stdout(_) | Self::InstallSkills(_) | Self::LinkSkills(_) => &[],
        }
    }
}
