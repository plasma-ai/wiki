//! The one path from a command to stdout and stderr.

use std::error::Error as _;
use std::io;
use std::io::BufWriter;
use std::io::IsTerminal as _;
use std::io::Stderr;
use std::io::StdoutLock;
use std::io::Write as _;

use anstream::AutoStream;
use anstyle::AnsiColor;
use anstyle::Effects;
use anstyle::Style;

use crate::command::CommandError;

/// The style of the `error:` and `caused by:` labels.
const ERROR: Style = AnsiColor::Red.on_default().effects(Effects::BOLD);

/// The style of the `hint:` label.
const HINT: Style = AnsiColor::Cyan.on_default().effects(Effects::BOLD);

/// The output of a run: the command's product on stdout, and everything
/// else on stderr.
pub(crate) struct Output {
    stdout: BufWriter<StdoutLock<'static>>,
    stderr: AutoStream<Stderr>,
    /// Whether stdout is a terminal, where each line is flushed as written.
    interactive: bool,
    /// Whether `--quiet` silences status messages.
    quiet: bool,
}

impl Output {
    /// Opens both streams.
    ///
    /// stdout carries the product as the command wrote it, with nothing
    /// filtered. stderr decides whether to keep the color of its labels.
    #[expect(
        clippy::disallowed_methods,
        reason = "this is the one place that opens stdout and stderr"
    )]
    pub(crate) fn new(color: clap::ColorChoice, quiet: bool) -> Self {
        let color = match color {
            clap::ColorChoice::Auto => anstream::ColorChoice::Auto,
            clap::ColorChoice::Always => anstream::ColorChoice::Always,
            clap::ColorChoice::Never => anstream::ColorChoice::Never,
        };
        let stdout = io::stdout();
        Self {
            interactive: stdout.is_terminal(),
            stdout: BufWriter::new(stdout.lock()),
            stderr: AutoStream::new(io::stderr(), color),
            quiet,
        }
    }

    /// Writes one line of the command's product to stdout.
    pub(crate) fn data(&mut self, line: &str) -> Result<(), WriteStdoutError> {
        writeln!(self.stdout, "{line}").map_err(WriteStdoutError)?;
        if self.interactive {
            self.flush()?;
        }
        Ok(())
    }

    /// Writes a status message to stderr, unless `--quiet` is set.
    pub(crate) fn status(&mut self, message: &str) -> Result<(), WriteStdoutError> {
        if self.quiet {
            return Ok(());
        }
        self.flush()?;
        // A failed write to stderr has nowhere to be reported.
        let _ = writeln!(self.stderr, "{message}");
        Ok(())
    }

    /// Writes a failure to stderr: the error, each of its causes, then the
    /// hints.
    pub(crate) fn error(&mut self, error: &CommandError) {
        // A failed flush or write here has nowhere to be reported.
        let _ = self.flush();
        let _ = self.write_error(error);
    }

    /// Writes what is buffered for stdout.
    pub(crate) fn flush(&mut self) -> Result<(), WriteStdoutError> {
        self.stdout.flush().map_err(WriteStdoutError)
    }

    fn write_error(&mut self, error: &CommandError) -> io::Result<()> {
        writeln!(self.stderr, "{ERROR}error:{ERROR:#} {error}")?;
        let mut cause = error.source();
        while let Some(error) = cause {
            writeln!(self.stderr, "{ERROR}caused by:{ERROR:#} {error}")?;
            cause = error.source();
        }
        for hint in error.hints() {
            writeln!(self.stderr, "{HINT}hint:{HINT:#} {hint}")?;
        }
        Ok(())
    }
}

/// A failed write to stdout.
#[derive(Debug, thiserror::Error)]
#[error("cannot write to stdout")]
pub(crate) struct WriteStdoutError(#[source] io::Error);

impl WriteStdoutError {
    /// Returns whether the reader closed the pipe, which is not a failure.
    pub(crate) fn is_closed_pipe(&self) -> bool {
        self.0.kind() == io::ErrorKind::BrokenPipe
    }
}
