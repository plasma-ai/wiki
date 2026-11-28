//! Command-line interface for `wiki`.
//!
//! The crate exports one function, [`main`], which the `wiki` binary
//! calls.

use std::io;
use std::io::Write as _;
use std::panic;
use std::process::ExitCode;

use clap::ColorChoice;

use crate::cli::Cli;
use crate::command::CommandError;
use crate::command::CommandOutcome;
use crate::config::Config;
use crate::output::Output;

mod cli;
mod command;
mod config;
mod output;
mod trace;

/// Where a bug in the program is reported.
const ISSUES_URL: &str = "https://github.com/plasma-ai/wiki/issues";

/// Runs the command line and returns the status the process exits with.
///
/// A successful command exits 0, and exit 1 is left for a command's own
/// negative answer. A failure is printed and exits 2, as a usage error does; a
/// reader that closed stdout early is not a failure and exits 0. A run that
/// received SIGINT or SIGTERM ends by that signal. A panic exits 101.
pub fn main() -> ExitCode {
    install_panic_hook();
    let config = Config::from_env();
    // The record is owed on every run, and clap ends a run itself on a usage
    // error or a request for help, so the record comes before the parse.
    if let Some(path) = &config.trace {
        trace::append(path);
    }
    let cli = Cli::parse_or_version();
    let (color, quiet) = cli.as_ref().map_or((ColorChoice::Auto, false), |cli| {
        (cli.global.color, cli.global.quiet)
    });
    let mut output = Output::new(color, quiet);
    let result = match &cli {
        Some(cli) => cli.command.run(&mut output, &config),
        None => print_version(&mut output),
    }
    .and_then(|outcome| {
        output.flush()?;
        Ok(outcome)
    });
    match result {
        Ok(CommandOutcome::Success) => ExitCode::SUCCESS,
        Err(CommandError::Stdout(error)) if error.is_closed_pipe() => ExitCode::SUCCESS,
        Err(error) => {
            output.error(&error);
            ExitCode::from(2)
        }
    }
}

/// Prints the bare version, the form `--version` has on every org tool.
fn print_version(output: &mut Output) -> Result<CommandOutcome, CommandError> {
    output.data(env!("CARGO_PKG_VERSION"))?;
    Ok(CommandOutcome::Success)
}

/// Adds a line to the default panic message that says where to report it.
#[expect(
    clippy::disallowed_methods,
    reason = "a panic can unwind through the output type, so the hook writes to stderr itself"
)]
fn install_panic_hook() {
    let default_hook = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        default_hook(info);
        // A failed write to stderr has nowhere to be reported.
        let _ = writeln!(
            io::stderr(),
            "This is a bug in wiki {}. Please report it at {ISSUES_URL}.",
            env!("CARGO_PKG_VERSION"),
        );
    }));
}
