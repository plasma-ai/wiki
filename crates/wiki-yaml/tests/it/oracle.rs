//! Tests that hold the crate to the oracle `parity/oracle.toml` names.

/// The oracle file, read at compile time, so the tests touch no filesystem.
const ORACLE: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../parity/oracle.toml"
));

/// Checks that the crate reproduces the libyaml release the oracle side runs.
#[test]
fn libyaml_version_is_the_one_the_oracle_side_runs() {
    assert_eq!(oracle_side("libyaml"), Some(wiki_yaml::LIBYAML_VERSION));
}

/// Returns the value of `key` in the `[sides.oracle]` table of the oracle
/// file.
fn oracle_side(key: &str) -> Option<&'static str> {
    let (_, table) = ORACLE.split_once("[sides.oracle]\n")?;
    let (table, _) = table.split_once("\n[").unwrap_or((table, ""));
    table.split('\n').find_map(|line| {
        line.strip_prefix(key)?
            .strip_prefix(" = \"")?
            .strip_suffix('"')
    })
}
