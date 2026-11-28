//! The search index, which is kept in the SQLite built into the binary.

use rusqlite::Connection;

/// The failure of [`sqlite_version`].
#[derive(Debug, thiserror::Error)]
pub enum QuerySqliteVersionError {
    /// The in-memory database could not be opened.
    #[error("cannot open an in-memory database")]
    OpenDatabase(#[source] rusqlite::Error),
    /// The query for the version failed.
    #[error("cannot read the version of the bundled SQLite")]
    ReadVersion(#[source] rusqlite::Error),
}

/// Returns the version of the SQLite built into the binary.
///
/// The answer is the result of a query on an in-memory database, so the call
/// runs the library and does not only read a constant of it.
///
/// # Errors
///
/// Returns [`QuerySqliteVersionError::OpenDatabase`] when the in-memory
/// database cannot be opened, and [`QuerySqliteVersionError::ReadVersion`]
/// when the query fails.
pub fn sqlite_version() -> Result<String, QuerySqliteVersionError> {
    let connection = Connection::open_in_memory().map_err(QuerySqliteVersionError::OpenDatabase)?;
    connection
        .query_row("SELECT sqlite_version()", (), |row| row.get(0))
        .map_err(QuerySqliteVersionError::ReadVersion)
}
