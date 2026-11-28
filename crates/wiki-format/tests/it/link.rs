//! Tests for the link rows of an index.

use wiki_format::format_link;

/// The index of the example wiki, as the Python tool wrote it.
const EXAMPLE_INDEX: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../examples/hello/_index.md"
));

/// Checks that a link is formatted as the row the example index holds for it.
#[test]
fn format_link_writes_the_row_of_the_example_index() {
    let row = format_link("world", "world", "The customary first page.");
    assert!(
        EXAMPLE_INDEX.split('\n').any(|line| line == row),
        "{row:?} should be a line of the example index"
    );
}

/// Checks the three rows that are not a target, a label and a description on
/// one line: a parent link, a missing description, and a description a
/// formatter moved to its own line.
#[test]
fn format_link_keeps_the_forms_a_reader_expects() {
    assert_eq!(format_link("..", "..", "The parent."), "[[..|..]]");
    assert_eq!(format_link("page", "page", ""), "[[page|page]]: ...");
    assert_eq!(
        format_link("page", "page", "\nOn its own line."),
        "[[page|page]]:\nOn its own line."
    );
}
