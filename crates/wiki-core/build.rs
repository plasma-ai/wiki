//! Build script: rebuilds the crate when the bundled skills change.

fn main() {
    // Cargo tracks each embedded file, but not the directory that holds them:
    // without this line a file added there is left out of the next build.
    println!("cargo::rerun-if-changed=../../wiki/skills");
}
