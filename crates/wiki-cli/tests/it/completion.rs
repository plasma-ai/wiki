//! Tests for the `completion` command.

use crate::support::Sandbox;
use crate::support::TestResult;

/// Checks that the script is the command's product: on stdout, with nothing
/// on stderr.
#[test]
fn completion_writes_the_script_to_stdout() -> TestResult {
    let sandbox = Sandbox::new()?;
    let output = sandbox.run(&["completion", "bash"])?;
    assert!(output.status.success(), "{output}");
    assert!(output.stdout.contains("complete -F"), "{output}");
    assert!(output.stderr.is_empty(), "{output}");
    Ok(())
}
