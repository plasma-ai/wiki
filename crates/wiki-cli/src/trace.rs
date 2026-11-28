//! The identity record, which tells a harness which binary it ran.

use std::fs::OpenOptions;
use std::io;
use std::io::Write as _;
use std::path::Path;

use wiki_core::search;

/// The name of this engine in the record.
const ENGINE: &str = "rust";

/// The commit of the checkout the binary was built in, which the build script
/// reads. A build outside a checkout has none.
const BUILD: &str = env!("WIKI_BUILD_ID");

/// Appends the identity record to the file at `path`: one line of JSON that
/// holds the engine's name, its version and its build id.
///
/// The record vouches for the whole binary, so it is written only once the
/// bundled SQLite has answered a query. A harness refuses a run that left no
/// record, which is all a failure here needs: nothing is reported.
pub(crate) fn append(path: &Path) {
    if search::sqlite_version().is_err() {
        return;
    }
    // A failed write leaves no record, and the missing record is the report.
    let _ = write(path);
}

/// Writes the record in one call, so records appended to one file by several
/// processes do not mix.
fn write(path: &Path) -> io::Result<()> {
    // None of the three values holds a character that JSON escapes: a Cargo
    // version, and a commit id or nothing.
    let record = format!(
        "{{\"engine\": \"{ENGINE}\", \"version\": \"{}\", \"build\": \"{BUILD}\"}}\n",
        env!("CARGO_PKG_VERSION"),
    );
    let mut file = OpenOptions::new().append(true).create(true).open(path)?;
    file.write_all(record.as_bytes())
}
