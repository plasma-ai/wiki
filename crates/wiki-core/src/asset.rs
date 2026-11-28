//! The files `wiki` writes into the Obsidian vault of a wiki, embedded at build
//! time.

use include_dir::Dir;
use include_dir::include_dir;

/// The Obsidian configuration templates.
pub static OBSIDIAN: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/../../wiki/_assets/obsidian");

/// The Obsidian plugins that ship with the binary.
pub static PLUGINS: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/../../wiki/_assets/plugins");
