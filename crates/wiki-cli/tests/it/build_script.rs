//! Tests for the build script, which reads the build id from git's files.

use std::fs;
use std::path::Path;

use crate::support::Sandbox;
use crate::support::TestResult;

/// Checks that a build in a linked worktree takes the commit of the worktree's
/// own branch: its `HEAD` is in the worktree's git folder, and its branch in
/// the folder every worktree shares.
#[test]
fn a_linked_worktree_takes_the_commit_of_its_own_branch() -> TestResult {
    let sandbox = Sandbox::new()?;
    let main = sandbox.work().join("main");
    let linked = sandbox.work().join("linked");
    init(&sandbox, &main, "main")?;
    sandbox.git(&main, &["worktree", "add", "-b", "session", "../linked"])?;
    let commit = commit(&sandbox, &linked)?;

    let script = sandbox.run_build_script(&linked)?;
    assert_eq!(script.build_id, commit);
    assert_eq!(
        script.watched,
        [
            main.join(".git/worktrees/linked/HEAD"),
            main.join(".git/refs/heads/session"),
        ]
    );
    Ok(())
}

/// Checks that a detached `HEAD` gives the commit it holds, and that nothing
/// else is watched.
#[test]
fn a_detached_head_gives_the_commit_it_holds() -> TestResult {
    let sandbox = Sandbox::new()?;
    let root = sandbox.work();
    let commit = init(&sandbox, &root, "main")?;
    sandbox.git(&root, &["checkout", "--detach"])?;

    let script = sandbox.run_build_script(&root)?;
    assert_eq!(script.build_id, commit);
    assert_eq!(script.watched, [root.join(".git/HEAD")]);
    Ok(())
}

/// Checks that a packed ref gives its commit, and that the ref's own file
/// wins once a later commit writes it, since the packed file then still holds
/// the earlier commit.
#[test]
fn a_packed_ref_gives_its_commit_until_the_ref_has_a_file() -> TestResult {
    let sandbox = Sandbox::new()?;
    let root = sandbox.work();
    let packed = init(&sandbox, &root, "main")?;
    sandbox.git(&root, &["pack-refs", "--all"])?;
    assert_eq!(sandbox.run_build_script(&root)?.build_id, packed);

    let later = commit(&sandbox, &root)?;
    assert_ne!(later, packed);
    let script = sandbox.run_build_script(&root)?;
    assert_eq!(script.build_id, later);
    assert_eq!(
        script.watched,
        [root.join(".git/HEAD"), root.join(".git/refs/heads/main")]
    );
    Ok(())
}

/// Checks that a packed ref is watched through the packed file and the
/// nearest folder that exists above the ref's missing file, where the next
/// commit writes it.
#[test]
fn a_packed_ref_is_watched_through_the_folder_its_file_will_appear_in() -> TestResult {
    let sandbox = Sandbox::new()?;
    let root = sandbox.work();
    init(&sandbox, &root, "topic/deep")?;
    sandbox.git(&root, &["pack-refs", "--all"])?;
    assert!(!root.join(".git/refs/heads/topic").exists());

    let script = sandbox.run_build_script(&root)?;
    assert_eq!(
        script.watched,
        [
            root.join(".git/HEAD"),
            root.join(".git/packed-refs"),
            root.join(".git/refs/heads"),
        ]
    );
    Ok(())
}

/// Checks that a repository whose refs are in the reftable format has no build
/// id: its `HEAD` names a ref that no file holds.
#[test]
fn a_reftable_repository_has_no_build_id() -> TestResult {
    let sandbox = Sandbox::new()?;
    let root = sandbox.work();
    sandbox.git(&root, &["init", "--ref-format=reftable"])?;
    commit(&sandbox, &root)?;

    let script = sandbox.run_build_script(&root)?;
    assert_eq!(script.build_id, "");
    assert_eq!(
        script.watched,
        [root.join(".git/HEAD"), root.join(".git/refs")]
    );
    Ok(())
}

/// Checks that a workspace with no git folder of its own has no build id, even
/// inside the checkout of another repository.
#[test]
fn a_workspace_outside_a_checkout_has_no_build_id() -> TestResult {
    let sandbox = Sandbox::new()?;
    let outer = sandbox.work();
    init(&sandbox, &outer, "main")?;
    let root = outer.join("unpacked");
    fs::create_dir(&root)?;

    let script = sandbox.run_build_script(&root)?;
    assert_eq!(script.build_id, "");
    assert!(script.watched.is_empty(), "{script:?}");
    Ok(())
}

/// Creates a repository in `dir` on the branch `branch`, with one commit, and
/// returns that commit.
fn init(sandbox: &Sandbox, dir: &Path, branch: &str) -> TestResult<String> {
    fs::create_dir_all(dir)?;
    sandbox.git(dir, &["init", "--initial-branch", branch])?;
    commit(sandbox, dir)
}

/// Adds an empty commit to the repository in `dir` and returns it.
fn commit(sandbox: &Sandbox, dir: &Path) -> TestResult<String> {
    let count = sandbox.git(dir, &["rev-list", "--count", "--all"])?;
    let message = format!("commit {count}");
    sandbox.git(dir, &["commit", "--allow-empty", "--message", &message])?;
    sandbox.git(dir, &["rev-parse", "HEAD"])
}
