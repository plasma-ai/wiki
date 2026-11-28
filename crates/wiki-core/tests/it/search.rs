//! Tests for the SQLite built into the binary.

use wiki_core::search;

use crate::support::TestResult;

/// Checks that a query reaches the bundled SQLite and returns the version of
/// the library the binary links.
#[test]
fn sqlite_version_is_the_one_of_the_linked_library() -> TestResult {
    assert_eq!(search::sqlite_version()?, rusqlite::version());
    Ok(())
}
