'use strict';

// Wiki Root Links
// ---------------
//
// Reads every wikilink from the vault root, as `wiki lint` reads it: a
// target carrying a `.` or `..` segment leaves the wiki. Stock Obsidian
// reads such a target from the note's own folder, so it can open an
// in-vault note the link does not name, and a click on one it cannot
// resolve creates folders outside the vault. Three layers stop that, each
// standing without the others: the resolver never answers a dot link with
// a note, the follow path routes a dot link to the Open step instead of
// the create-note path, and a capture-phase click guard does the same for
// a click the follow path misses. The Open step opens a markdown target in
// the vault that holds it when Obsidian registers that vault on this
// machine, and otherwise names the root-relative path in a notice. The only
// string that ever leaves the plugin is an obsidian:// URI: a filesystem
// path is never handed to the operating system, so a link naming a script
// or an app can run nothing. Any fault of the plugin's own falls through to
// stock behaviour.
//
// The vault root is the wiki root: `wiki init` and `wiki config` install
// the plugin only into the vault of a root they configure.

const fs = require('fs');
const os = require('os');
const path = require('path');
const { FileSystemAdapter, Notice, Plugin } = require('obsidian');

// Return the target a link's text names: cut at the `#anchor` or `|alias`
// suffix and stripped of surrounding spaces and tabs, as lint reads it.
function linkTarget(linktext) {
  const target = linktext.split(/[#|]/, 1)[0];
  return target.replace(/^[ \t]+|[ \t]+$/g, '');
}

// Return whether a link's text names a target outside the wiki: not
// absolute, and carrying a `.` or `..` segment when split on `/`. An
// absolute target is left to stock Obsidian, as lint reads it as written.
function isDotLink(linktext) {
  const target = linkTarget(linktext);
  if (path.isAbsolute(target)) return false;
  return target.split('/').some((segment) => segment === '.' || segment === '..');
}

// Return the link text a pointer event lands on, or null: the `data-href`
// Obsidian's renderer puts on a reading-view anchor, else the text of a
// Live Preview link span.
function clickedLinktext(target) {
  if (!(target instanceof Element)) return null;
  const anchor = target.closest('a.internal-link');
  if (anchor !== null) return anchor.dataset.href ?? null;
  const span = target.closest('.cm-hmd-internal-link, .cm-underline');
  if (span !== null) return span.textContent;
  return null;
}

// Return the path of Obsidian's vault registry, obsidian.json in Electron's
// per-user data folder; the documented per-platform folders stand in when
// the renderer exposes no remote.
function registryPath() {
  const { remote } = require('electron');
  if (remote) return path.join(remote.app.getPath('userData'), 'obsidian.json');
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'obsidian', 'obsidian.json');
  }
  if (process.platform === 'win32') return path.join(process.env.APPDATA, 'Obsidian', 'obsidian.json');
  const config = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(config, 'obsidian', 'obsidian.json');
}

// Return the paths of the vaults Obsidian registers on this machine, or
// null when its registry cannot be read.
function registeredVaults() {
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath(), 'utf8'));
    return Object.values(registry.vaults).map((vault) => vault.path);
  } catch {
    return null;
  }
}

// Return the on-disk form of a path, symlinks resolved and casing as stored,
// or its lexical form when nothing is there to resolve.
function canonical(target) {
  try {
    return fs.realpathSync.native(target);
  } catch {
    return path.resolve(target);
  }
}

// Return whether `folder` holds `target`, compared at a segment boundary so
// a vault at `wiki` never claims `wiki2/x.md`.
function holds(folder, target) {
  const vault = canonical(folder);
  const file = canonical(target);
  return file === vault || file.startsWith(vault + path.sep);
}

module.exports = class WikiRootLinks extends Plugin {
  onload() {
    // patch the resolver and the follow path; each layer installs on its own,
    // and a missing method is told once, since stock reading then applies
    const cache = Object.getPrototypeOf(this.app.metadataCache);
    const workspace = Object.getPrototypeOf(this.app.workspace);
    const resolves = this.patch(cache, 'getFirstLinkpathDest', () => null);
    this.patch(cache, 'getLinkpathDest', () => []);
    const follows = this.patch(workspace, 'openLinkText', (linktext) => this.follow(linktext));
    if (!resolves || !follows) {
      new Notice(
        'Wiki Root Links: this Obsidian lacks the link internals the plugin' +
          ' patches, so ./ and ../ links resolve as stock Obsidian reads them.',
      );
    }
    // guard clicks before Obsidian's own handlers see them
    for (const type of ['mousedown', 'auxclick', 'click']) {
      this.registerDomEvent(document, type, (event) => this.guardClick(event), { capture: true });
    }
  }

  // Wrap `proto[name]` so a dot link gets `answer` and every other call, or
  // any fault of the plugin's own, reaches the original; an `answer` of
  // undefined leaves the call to the original too. Restored on unload,
  // leaving a later patch by another plugin in place. Returns whether the
  // method exists to patch.
  patch(proto, name, answer) {
    const original = proto[name];
    if (typeof original !== 'function') return false;
    const patched = function (linktext, ...rest) {
      try {
        if (isDotLink(linktext)) {
          const result = answer(linktext);
          if (result !== undefined) return result;
        }
      } catch {
        // stock behaviour covers any fault of the plugin's own
      }
      return original.call(this, linktext, ...rest);
    };
    proto[name] = patched;
    this.register(() => {
      if (proto[name] === patched) proto[name] = original;
    });
    return true;
  }

  // Route a followed dot link to the Open step, answering in openLinkText's
  // shape; undefined leaves a vault with no local filesystem to stock
  // behaviour.
  follow(linktext) {
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) return undefined;
    this.openOutside(linktext);
    return Promise.resolve();
  }

  // Stop a pointer event on a dot link before Obsidian's own handlers see it
  // and route the click to the Open step; a mousedown is only stopped, so
  // one press opens once.
  guardClick(event) {
    const linktext = clickedLinktext(event.target);
    if (linktext === null || !isDotLink(linktext)) return;
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.type !== 'mousedown') this.openOutside(linktext);
  }

  // Open a dot link outside the vault: a markdown target inside a vault
  // Obsidian registers on this machine opens there through an
  // obsidian://open URI, whose `path` Obsidian decodes and hands to the most
  // specific vault holding it; every other target draws a notice naming the
  // root-relative path, with the stat's verdict when nothing is there.
  openOutside(linktext) {
    const root = this.app.vault.adapter.getBasePath();
    const spelled = linkTarget(linktext);
    const target = path.resolve(root, spelled);
    // the page a markdown target names: by stem, as a wikilink names a
    // page, or by its explicit .md name
    let page = null;
    if (fs.existsSync(target + '.md')) page = target + '.md';
    else if (target.endsWith('.md') && fs.existsSync(target)) page = target;
    if (page !== null) {
      const vaults = registeredVaults();
      // an unreadable registry leaves the lookup to Obsidian, whose "Vault
      // not found" dialog then precedes the notice
      if (vaults === null || vaults.some((vault) => holds(vault, page))) {
        window.open('obsidian://open?path=' + encodeURIComponent(page));
        if (vaults !== null) return;
      }
    }
    const suffix = page === null && !fs.existsSync(target) ? ' (not found)' : '';
    new Notice(`Outside the vault: ${path.posix.normalize(spelled)}${suffix}`);
  }
};
module.exports.isDotLink = isDotLink;
