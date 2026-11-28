//! Tests for the embedded Obsidian files.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::path::PathBuf;

use include_dir::Dir;
use include_dir::DirEntry;
use wiki_core::asset;

use crate::support::TestResult;

/// Checks that the binary embeds each asset folder whole, byte for byte.
#[test]
fn embedded_assets_equal_the_folders_they_are_built_from() -> TestResult {
    let assets = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../wiki/_assets");
    for (embedded, folder) in [(&asset::OBSIDIAN, "obsidian"), (&asset::PLUGINS, "plugins")] {
        let mut files = BTreeMap::new();
        collect_embedded(embedded, &mut files);
        assert!(!files.is_empty(), "{folder} should hold a file");
        assert_eq!(files, read_files(&assets.join(folder))?, "{folder}");
    }
    Ok(())
}

/// Adds every file embedded under `dir` to `files`, by its path below the
/// embedded folder.
fn collect_embedded(dir: &Dir<'_>, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
    for entry in dir.entries() {
        match entry {
            DirEntry::Dir(child) => collect_embedded(child, files),
            DirEntry::File(file) => {
                files.insert(file.path().to_path_buf(), file.contents().to_vec());
            }
        }
    }
}

/// Reads every file under `root`, by its path below `root`.
fn read_files(root: &Path) -> TestResult<BTreeMap<PathBuf, Vec<u8>>> {
    let mut files = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(dir) = pending.pop() {
        for entry in fs::read_dir(&dir)? {
            let path = entry?.path();
            if path.is_dir() {
                pending.push(path);
            } else {
                files.insert(path.strip_prefix(root)?.to_path_buf(), fs::read(&path)?);
            }
        }
    }
    Ok(files)
}
