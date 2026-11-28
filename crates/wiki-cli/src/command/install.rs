//! The `install` command.

use std::path::Path;

use clap::Args;
use wiki_core::skill;

use crate::command::CommandError;
use crate::command::CommandOutcome;
use crate::config::Config;
use crate::output::Output;

/// Install the wiki skill for Claude Code and Codex
///
/// Copies the bundled skill into the Claude (.claude/skills) and Codex
/// (.agents/skills) skill directories, replacing any earlier copy. Targets your
/// home directory by default, or the current directory with --project.
#[derive(Debug, Args)]
pub(crate) struct InstallArgs {
    /// Install into the current directory instead of the home directory
    #[arg(long)]
    project: bool,
    /// Link the skill of the development clone instead of copying it
    ///
    /// Edits to the skill then apply without reinstalling. Only a binary built
    /// in a development clone can link.
    #[arg(long)]
    link: bool,
}

/// Runs the `install` command.
pub(crate) fn run(
    output: &mut Output,
    args: &InstallArgs,
    config: &Config,
) -> Result<CommandOutcome, CommandError> {
    let root = if args.project {
        Path::new(".")
    } else {
        config
            .home
            .as_deref()
            .ok_or(CommandError::NoHomeDirectory)?
    };
    let (skills, verb) = if args.link {
        (skill::link(Path::new(skill::SOURCE_DIR), root)?, "Linked")
    } else {
        (skill::install(root)?, "Installed")
    };
    for skill in skills {
        let path = skill.path.display();
        output.status(&format!("{verb} {} -> {path}.", skill.name))?;
    }
    Ok(CommandOutcome::Success)
}
