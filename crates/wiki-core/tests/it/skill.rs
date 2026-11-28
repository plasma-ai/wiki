//! Tests for the installation of the bundled skills.

use std::collections::BTreeMap;
use std::fs;
use std::os::unix;
use std::path::Path;
use std::path::PathBuf;

use anyhow::Context as _;
use tempfile::TempDir;
use wiki_core::skill;
use wiki_core::skill::LinkSkillsError;

use crate::support::TestResult;

/// Checks that an install copies the bundled skills for both agents, and that
/// a second install restores a changed copy.
#[test]
fn install_copies_the_bundled_skills_and_replaces_an_earlier_copy() -> TestResult {
    let (_guard, root) = temp_root()?;
    let bundled = read_tree(Path::new(skill::SOURCE_DIR))?;

    let installed = skill::install(&root)?;
    assert_eq!(read_tree(&root.join(".claude/skills"))?, bundled);
    assert_eq!(read_tree(&root.join(".agents/skills"))?, bundled);

    let first = installed.first().context("a skill should be bundled")?;
    fs::write(first.path.join("stray.md"), "left by an earlier version")?;
    fs::remove_file(first.path.join("SKILL.md"))?;
    assert_eq!(skill::install(&root)?, installed);
    assert_eq!(read_tree(&root.join(".claude/skills"))?, bundled);
    assert_eq!(read_tree(&root.join(".agents/skills"))?, bundled);
    Ok(())
}

/// Checks that agent directories linked to one place receive one copy.
#[test]
fn install_writes_linked_agent_directories_once() -> TestResult {
    let (_guard, root) = temp_root()?;
    let claude = root.join(".claude/skills");
    fs::create_dir_all(&claude)?;
    fs::create_dir(root.join(".agents"))?;
    unix::fs::symlink(&claude, root.join(".agents/skills"))?;

    let installed = skill::install(&root)?;
    let bundled = fs::read_dir(skill::SOURCE_DIR)?.count();
    assert_eq!(installed.len(), bundled);
    for skill in &installed {
        assert_eq!(skill.path.parent(), Some(claude.as_path()));
    }
    Ok(())
}

/// Checks that a link replaces an installed copy and points each agent at the
/// source folder, so an edit there needs no further install.
#[test]
#[expect(
    clippy::disallowed_methods,
    reason = "the test resolves the source folder as the installer does"
)]
fn link_points_both_agents_at_the_source_folder() -> TestResult {
    let (_guard, root) = temp_root()?;
    let source = Path::new(skill::SOURCE_DIR).canonicalize()?;
    let installed = skill::install(&root)?;

    let linked = skill::link(&source, &root)?;
    assert_eq!(linked, installed);
    for skill in &linked {
        assert_eq!(fs::read_link(&skill.path)?, source.join(&skill.name));
    }
    assert_eq!(
        read_tree(&root.join(".agents/skills"))?,
        read_tree(&source)?
    );
    Ok(())
}

/// Checks that a source folder that is not there is a typed failure, which is
/// what a binary built on another machine meets.
#[test]
fn link_fails_when_the_source_folder_is_missing() -> TestResult {
    let (_guard, root) = temp_root()?;
    let result = skill::link(&root.join("no-clone"), &root);
    assert!(
        matches!(result, Err(LinkSkillsError::ReadSource(_))),
        "{result:?}"
    );
    Ok(())
}

/// Creates a temporary directory for one test and returns it with its
/// canonical path, since a temporary directory is often behind a symlink.
#[expect(
    clippy::disallowed_methods,
    reason = "the test resolves a temporary directory it has just made"
)]
fn temp_root() -> TestResult<(TempDir, PathBuf)> {
    let guard = tempfile::Builder::new().prefix("wiki-test-").tempdir()?;
    let root = guard.path().canonicalize()?;
    Ok((guard, root))
}

/// Reads the tree under `root` as relative paths, each with its contents when
/// it is a file.
fn read_tree(root: &Path) -> TestResult<BTreeMap<PathBuf, Option<Vec<u8>>>> {
    let mut tree = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(dir) = pending.pop() {
        for entry in fs::read_dir(&dir)? {
            let path = entry?.path();
            let relative = path.strip_prefix(root)?.to_path_buf();
            if path.is_dir() {
                tree.insert(relative, None);
                pending.push(path);
            } else {
                tree.insert(relative, Some(fs::read(&path)?));
            }
        }
    }
    Ok(tree)
}
