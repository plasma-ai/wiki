//! The `completion` command.

use clap::Args;
use clap::CommandFactory as _;
use clap_complete::Shell;

use crate::cli::Cli;
use crate::command::CommandError;
use crate::command::CommandOutcome;
use crate::output::Output;

/// Print a shell completion script
#[derive(Debug, Args)]
pub(crate) struct CompletionArgs {
    /// Shell to write the script for
    shell: Shell,
}

/// Runs the `completion` command.
#[expect(
    clippy::disallowed_methods,
    reason = "the script is clap's own text, which ends its lines with a line feed"
)]
pub(crate) fn run(
    output: &mut Output,
    args: &CompletionArgs,
) -> Result<CommandOutcome, CommandError> {
    // `generate` panics when its writer fails, so it writes to memory and the
    // output type makes the checked write.
    let mut command = Cli::command();
    let name = command.get_name().to_owned();
    let mut script = Vec::new();
    clap_complete::generate(args.shell, &mut command, name, &mut script);
    let script = String::from_utf8(script).expect("clap_complete should write UTF-8 text");
    for line in script.lines() {
        output.data(line)?;
    }
    Ok(CommandOutcome::Success)
}
