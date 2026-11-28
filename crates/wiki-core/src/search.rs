//! The search index, which is kept in the SQLite built into the binary.

use rusqlite::Connection;

/// The failure of [`check_full_text`].
#[derive(Debug, thiserror::Error)]
pub enum CheckFullTextError {
    /// The in-memory database could not be opened.
    #[error("cannot open an in-memory database")]
    OpenDatabase(#[source] rusqlite::Error),
    /// The full-text table could not be made or filled.
    #[error("cannot build a full-text table")]
    BuildTable(#[source] rusqlite::Error),
    /// The query did not return the row it should match.
    #[error("cannot match a row of the full-text table")]
    MatchRow(#[source] rusqlite::Error),
}

/// Checks that the SQLite built into the binary has FTS5, the full-text
/// module the search index is built on.
///
/// The check makes a full-text table in an in-memory database and matches the
/// one row it holds, so it runs the module and does not only read a compile
/// option of the library.
///
/// # Errors
///
/// Returns [`CheckFullTextError::OpenDatabase`] when the in-memory database
/// cannot be opened, [`CheckFullTextError::BuildTable`] when the table cannot
/// be made or filled, which is the failure of a library built without the
/// module, and [`CheckFullTextError::MatchRow`] when the query fails or
/// matches no row.
pub fn check_full_text() -> Result<(), CheckFullTextError> {
    let connection = Connection::open_in_memory().map_err(CheckFullTextError::OpenDatabase)?;
    connection
        .execute_batch(
            "CREATE VIRTUAL TABLE page USING fts5(body);
            INSERT INTO page (body) VALUES ('a page of the wiki');",
        )
        .map_err(CheckFullTextError::BuildTable)?;
    connection
        .query_row("SELECT rowid FROM page WHERE page MATCH 'wiki'", (), |_| {
            Ok(())
        })
        .map_err(CheckFullTextError::MatchRow)
}
