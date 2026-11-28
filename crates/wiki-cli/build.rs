//! Build script: records the commit of the checkout the binary is built in.
//!
//! The commit is the build id, `WIKI_BUILD_ID`, and a build outside a checkout
//! has an empty one. The script reads git's files and never runs git, which
//! refuses a checkout that another user owns.

use std::env;
use std::fs;
use std::path::Path;
use std::path::PathBuf;

/// The file in a git folder that packs the refs with no file of their own.
const PACKED_REFS: &str = "packed-refs";

#[expect(
    clippy::disallowed_methods,
    reason = "a build script takes its inputs from the variables cargo sets"
)]
fn main() {
    let manifest_dir = env::var_os("CARGO_MANIFEST_DIR")
        .map(PathBuf::from)
        .expect("cargo should set `CARGO_MANIFEST_DIR` for a build script");
    // NOTE: `.git` is looked for in the workspace root and nowhere above it. A
    // source archive unpacked inside another repository must not take that
    // repository's commit.
    let root = manifest_dir
        .ancestors()
        .nth(2)
        .expect("the crate should sit two levels below the workspace root");
    let checkout = git_dir(&root.join(".git")).map(|git_dir| Checkout::read(&git_dir));
    let checkout = checkout.unwrap_or_default();
    for path in &checkout.watched {
        println!("cargo::rerun-if-changed={}", path.display());
    }
    println!("cargo::rustc-env=WIKI_BUILD_ID={}", checkout.commit);
}

/// What a git folder says about the commit that is checked out.
#[derive(Default)]
struct Checkout {
    /// The commit `HEAD` names, or nothing when the folder names none.
    commit: String,
    /// The paths whose change can change the commit, all inside the git
    /// folder and all present.
    watched: Vec<PathBuf>,
}

impl Checkout {
    /// Reads the commit `HEAD` names in `git_dir`, the folder that holds it.
    fn read(git_dir: &Path) -> Self {
        let head_file = git_dir.join("HEAD");
        let Ok(head) = fs::read_to_string(&head_file) else {
            return Self::default();
        };
        let head = first_line(&head);
        let mut checkout = Self {
            commit: String::new(),
            watched: vec![head_file],
        };
        let Some(name) = head.strip_prefix("ref: ") else {
            // A detached `HEAD` holds the commit itself.
            checkout.commit = commit(head);
            return checkout;
        };
        if !name.starts_with("refs/") {
            return checkout;
        }
        let common_dir = common_dir(git_dir);
        let ref_file = common_dir.join(name);
        // NOTE: The ref's own file is read first, and the packed file only
        // when it is missing: once a later commit writes the ref's file, the
        // packed file still holds the old value.
        if let Ok(value) = fs::read_to_string(&ref_file) {
            checkout.commit = commit(first_line(&value));
            checkout.watched.push(ref_file);
            return checkout;
        }
        let packed_file = common_dir.join(PACKED_REFS);
        if let Ok(packed) = fs::read_to_string(&packed_file) {
            checkout.commit = packed_commit(&packed, name);
            checkout.watched.push(packed_file);
        }
        // NOTE: Cargo reruns a script on every build while a path it names is
        // missing, so the ref's missing file is never named. The nearest
        // folder above it is: the folder changes when the next commit writes
        // the file.
        if let Some(folder) = ref_file.ancestors().find(|path| path.is_dir()) {
            checkout.watched.push(folder.to_path_buf());
        }
        checkout
    }
}

/// Returns the git folder that `dot_git` stands for, when there is one.
///
/// In a linked worktree `.git` is a file that names the worktree's own git
/// folder.
fn git_dir(dot_git: &Path) -> Option<PathBuf> {
    if dot_git.is_dir() {
        return Some(dot_git.to_path_buf());
    }
    let pointer = fs::read_to_string(dot_git).ok()?;
    let target = first_line(&pointer).strip_prefix("gitdir: ")?;
    Some(dot_git.parent()?.join(target))
}

/// Returns the folder that holds the refs every worktree shares.
///
/// The git folder of a linked worktree names it in its `commondir` file;
/// elsewhere it is the git folder itself.
fn common_dir(git_dir: &Path) -> PathBuf {
    match fs::read_to_string(git_dir.join("commondir")) {
        Ok(pointer) => git_dir.join(first_line(&pointer)),
        Err(_) => git_dir.to_path_buf(),
    }
}

/// Returns the commit the packed refs hold for the ref `name`, or nothing.
fn packed_commit(packed: &str, name: &str) -> String {
    packed
        .split('\n')
        .filter_map(|line| line.split_once(' '))
        .find(|(_, packed_name)| *packed_name == name)
        .map(|(value, _)| commit(value))
        .unwrap_or_default()
}

/// Returns `value` when it is a commit id, and nothing otherwise.
///
/// A ref can also hold the name of another ref, which is no build id.
fn commit(value: &str) -> String {
    let is_id = matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
    if is_id {
        value.to_owned()
    } else {
        String::new()
    }
}

/// Returns `text` up to its first line break.
fn first_line(text: &str) -> &str {
    text.split('\n').next().unwrap_or(text)
}
