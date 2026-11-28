//! The on-disk page format of `wiki`: frontmatter, index rows and page text.

/// Formats one link row of an index.
///
/// The row of a parent link, whose label is `..`, has no description. Every
/// other row has one, which is `...` when `description` is empty. A
/// description that opens with a line break keeps the break: a formatter wraps
/// a row too long to hold its description after `]]:`, and the row is then
/// written back as it was read.
pub fn format_link(target: &str, label: &str, description: &str) -> String {
    if label == ".." {
        return format!("[[{target}|{label}]]");
    }
    let description = if description.is_empty() {
        "..."
    } else {
        description
    };
    if description.starts_with('\n') {
        format!("[[{target}|{label}]]:{description}")
    } else {
        format!("[[{target}|{label}]]: {description}")
    }
}
