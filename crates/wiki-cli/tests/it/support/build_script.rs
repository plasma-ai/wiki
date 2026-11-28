//! The runner of the crate's build script.

use std::fs;
use std::hash::DefaultHasher;
use std::hash::Hash as _;
use std::hash::Hasher as _;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

use crate::support::Sandbox;
use crate::support::TestResult;

impl Sandbox {
    /// Runs the crate's build script for a workspace at `root` and returns
    /// what it tells cargo.
    #[expect(
        clippy::disallowed_methods,
        reason = "the harness reads cargo's line protocol and resolves paths that exist"
    )]
    pub(crate) fn run_build_script(&self, root: &Path) -> TestResult<BuildScriptOutput> {
        let output = Command::new(build_script_program()?)
            .stdin(Stdio::null())
            .env_clear()
            .env("CARGO_MANIFEST_DIR", root.join("crates/wiki-cli"))
            .output()?;
        anyhow::ensure!(
            output.status.success(),
            "the build script should succeed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let mut build_id = None;
        let mut watched = Vec::new();
        for line in String::from_utf8(output.stdout)?.lines() {
            if let Some(id) = line.strip_prefix("cargo::rustc-env=WIKI_BUILD_ID=") {
                build_id = Some(id.to_owned());
            } else if let Some(path) = line.strip_prefix("cargo::rerun-if-changed=") {
                // A linked worktree points to the shared folder with `..`,
                // and a path the script names must exist.
                watched.push(Path::new(path).canonicalize()?);
            } else {
                anyhow::bail!("the build script should print no other line: {line:?}");
            }
        }
        Ok(BuildScriptOutput {
            build_id: build_id.ok_or_else(|| anyhow::anyhow!("the script should set the id"))?,
            watched,
        })
    }
}

/// What the build script tells cargo: the build id, and the paths whose
/// change reruns it.
#[derive(Debug)]
#[must_use]
pub(crate) struct BuildScriptOutput {
    pub(crate) build_id: String,
    pub(crate) watched: Vec<PathBuf>,
}

/// Compiles the crate's build script as a program and returns its path.
///
/// Cargo runs no test inside a build script, so the tests run the script as
/// cargo does and read the lines it prints. Each test is its own process, and
/// several may get here at once: the program is named for the script's
/// content, and a process that compiles it renames its own copy into place.
fn build_script_program() -> TestResult<PathBuf> {
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("build.rs");
    let mut hasher = DefaultHasher::new();
    fs::read(&source)?.hash(&mut hasher);
    let target = Path::new(env!("CARGO_TARGET_TMPDIR"));
    let program = target.join(format!("build-script-{:016x}", hasher.finish()));
    if !program.is_file() {
        let scratch = tempfile::Builder::new()
            .prefix("build-script-")
            .tempdir_in(target)?;
        let compiled = scratch.path().join("program");
        // The compiler beside the cargo that built this test is the pinned one.
        let status = Command::new(Path::new(env!("CARGO")).with_file_name("rustc"))
            .arg("--edition=2024")
            .arg("-o")
            .arg(&compiled)
            .arg(&source)
            .stdin(Stdio::null())
            .status()?;
        anyhow::ensure!(status.success(), "rustc should compile the build script");
        fs::rename(&compiled, &program)?;
    }
    Ok(program)
}
