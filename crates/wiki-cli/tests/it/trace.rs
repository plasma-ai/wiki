//! Tests for the identity record.

use std::fs;
use std::path::Path;

use crate::support::Sandbox;
use crate::support::TestResult;

/// Checks that each run under `WIKI_TRACE` appends one record that names this
/// engine, its version and the commit of the checkout it was built in, and
/// that a run clap ends with a usage error leaves its record too.
#[test]
fn a_traced_run_appends_its_identity_record() -> TestResult {
    let sandbox = Sandbox::new()?;
    let trace = sandbox.home().join("trace");
    let commit = sandbox.git(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        &["rev-parse", "HEAD"],
    )?;
    let record = format!(
        "{{\"engine\": \"rust\", \"version\": \"{}\", \"build\": \"{commit}\"}}\n",
        env!("CARGO_PKG_VERSION"),
    );

    let mut command = sandbox.command(&["--version"]);
    command.env("WIKI_TRACE", &trace);
    let output = sandbox.observe(command)?;
    assert_eq!(output.to_string(), concat!(env!("CARGO_PKG_VERSION"), "\n"));
    assert_eq!(fs::read_to_string(&trace)?, record);

    let mut command = sandbox.command(&["no-such-command"]);
    command.env("WIKI_TRACE", &trace);
    let output = sandbox.observe(command)?;
    assert_eq!(output.status.code(), Some(2), "{output}");
    assert_eq!(fs::read_to_string(&trace)?, record.repeat(2));
    Ok(())
}
