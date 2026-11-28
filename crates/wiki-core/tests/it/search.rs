//! Tests for the SQLite built into the binary.

use wiki_core::search;

use crate::support::TestResult;

/// Checks that the bundled SQLite makes a full-text table and matches a row of
/// it, which a library built without FTS5 cannot do.
#[test]
fn bundled_sqlite_matches_a_row_of_a_full_text_table() -> TestResult {
    search::check_full_text()?;
    Ok(())
}
