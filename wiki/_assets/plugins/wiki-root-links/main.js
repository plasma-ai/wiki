'use strict';

// Wiki Root Links
// ---------------
//
// Reads every internal link from the vault root, as `wiki lint` reads a
// wikilink: a target carrying a `.` or `..` segment leaves the wiki. Stock
// Obsidian reads such a target from the note's own folder, so it can open
// an in-vault note the link does not name, and a click on one it cannot
// resolve creates folders outside the vault. Three layers stop that, each
// standing without the others: the resolver never answers a dot link with
// a note, the follow path routes a dot link to the Open step instead of
// the create-note path, and a capture-phase click guard does the same for
// a click the follow path misses. The Open step opens a markdown target in
// the vault that holds it when Obsidian registers that vault on this
// machine, and otherwise names the root-relative path in a notice; a dot
// target landing back inside the vault is the spelling lint fails, so the
// notice names its prefix-free form and nothing opens. The only string that
// ever leaves the plugin is an obsidian:// URI: a filesystem path is never
// handed to the operating system, so a link naming a script or an app can
// run nothing. Any fault of the plugin's own inside a patched method falls
// through to stock behavior.
//
// The vault root is the wiki root: `wiki init` and `wiki config` install
// the plugin only into the vault of a root they configure.

const fs = require('fs');
const os = require('os');
const path = require('path');
const { FileSystemAdapter, Notice, Plugin } = require('obsidian');

// Return the target a link's text names: cut at the `|alias`, stripped of
// surrounding spaces and tabs, then cut at the `#anchor`, as lint reads it.
function linkTarget(linktext) {
  const target = linktext.split('|', 1)[0].replace(/^[ \t]+|[ \t]+$/g, '');
  return target.split('#', 1)[0];
}

// Return whether a link's text names a target outside the wiki: not
// absolute, and carrying a `.` or `..` segment when split on `/`. An
// absolute target is left to stock Obsidian, as lint reads it as written.
function isDotLink(linktext) {
  const target = linkTarget(linktext);
  if (path.isAbsolute(target)) return false;
  return target.split('/').some((segment) => segment === '.' || segment === '..');
}

// The span a wikilink's text sits in: Obsidian's token mark, present in
// source mode and Live Preview on every text node of a wikilink however a
// highlight splits it. The underline mark is built by Live Preview alone and
// is shared with markdown-link and URL text, so a followable Live Preview
// wikilink is text under both marks. A source-mode click is left to stock,
// which follows there on a Mod or middle press alone, through openLinkText
// and so the follow path.
const LINK_SPAN = '.cm-hmd-internal-link';

// Return the link text a pointer event lands on, or null: the `data-href`
// Obsidian's renderer puts on a reading-view anchor, else the text of the
// Live Preview link the click lands in.
function clickedLinktext(element) {
  if (!(element instanceof Element)) return null;
  const anchor = element.closest('a.internal-link');
  if (anchor !== null) return anchor.dataset.href ?? null;
  // the alias segment of [[target|alias]] shows display text, not the
  // target, and Live Preview hides the target when the cursor is off the
  // line: the follow path reads that click's real linkpath
  if (element.closest('.cm-link-alias') !== null) return null;
  const span = element.closest(LINK_SPAN);
  if (span === null || element.closest('.cm-underline') === null) return null;
  return spanLinktext(span);
}

// Return the text of the Live Preview link `span` sits in: an overlapping
// mark (a search match) splits one link into several spans, nested under
// the mark or beside it, so the text is the unbroken run of link text nodes
// around `span` on its line. Any other text ends the run, as does the empty
// element a hidden `[[` or `]]` leaves.
function spanLinktext(span) {
  const line = span.closest('.cm-line') ?? span;
  const leaves = [];
  const collect = (node) => {
    if (node.childNodes.length === 0) leaves.push(node);
    for (const child of node.childNodes) collect(child);
  };
  collect(line);
  const inLink = (leaf) =>
    leaf.nodeType === leaf.TEXT_NODE && leaf.parentElement.closest(LINK_SPAN) !== null;
  const at = leaves.findIndex((leaf) => inLink(leaf) && span.contains(leaf));
  if (at < 0) return null;
  let from = at;
  while (from > 0 && inLink(leaves[from - 1])) from--;
  let to = at;
  while (to + 1 < leaves.length && inLink(leaves[to + 1])) to++;
  return leaves.slice(from, to + 1).map((leaf) => leaf.data).join('');
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
// null when its registry cannot be read; a row without an absolute string
// path holds nothing.
function registeredVaults() {
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath(), 'utf8'));
    return Object.values(registry?.vaults ?? {})
      .map((vault) => vault?.path)
      .filter((vaultPath) => typeof vaultPath === 'string' && path.isAbsolute(vaultPath));
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
// a vault at `wiki` never claims `wiki2/x.md`; a canonical path ends in a
// separator only when it is a filesystem or drive root, which already
// stands at a boundary.
function holds(folder, target) {
  const vault = canonical(folder);
  const file = canonical(target);
  const prefix = vault.endsWith(path.sep) ? vault : vault + path.sep;
  return file === vault || file.startsWith(prefix);
}

module.exports = class WikiRootLinks extends Plugin {
  onload() {
    // patch the resolver and the follow path; each layer installs on its own,
    // and a missing method is told once; the layers that install still run
    const cache = Object.getPrototypeOf(this.app.metadataCache);
    const workspace = Object.getPrototypeOf(this.app.workspace);
    const resolves = this.patch(cache, 'getFirstLinkpathDest', () => null);
    this.patch(cache, 'getLinkpathDest', () => []);
    const follows = this.patch(workspace, 'openLinkText', (linktext) => this.follow(linktext));
    if (!resolves || !follows) {
      new Notice(
        'Wiki Root Links: this Obsidian lacks a link internal the plugin patches,' +
          ' so the missing layer leaves ./ and ../ links to stock Obsidian; the' +
          ' other layers still run.',
      );
    }
    // guard clicks before Obsidian's own handlers see them
    for (const type of ['mousedown', 'auxclick', 'click']) {
      this.registerDomEvent(document, type, (event) => this.guardClick(event), { capture: true });
    }
  }

  // Wrap `proto[name]` so a dot link gets `answer` and every other call, or
  // any fault of the plugin's own, reaches the original; an `answer` of
  // undefined leaves the call to the original too. Restored on unload; when
  // a later patch by another plugin sits on top, that patch stays in place
  // and this wrapper passes every call through to the original. Returns
  // whether the method exists to patch.
  patch(proto, name, answer) {
    const original = proto[name];
    if (typeof original !== 'function') return false;
    let active = true;
    const patched = function (linktext, ...rest) {
      try {
        if (active && isDotLink(linktext)) {
          const result = answer(linktext);
          if (result !== undefined) return result;
        }
      } catch {
        // stock behavior covers any fault of the plugin's own
      }
      return original.call(this, linktext, ...rest);
    };
    proto[name] = patched;
    this.register(() => {
      active = false;
      if (proto[name] === patched) proto[name] = original;
    });
    return true;
  }

  // Route a followed dot link to the Open step, answering in openLinkText's
  // shape; undefined leaves a vault with no local filesystem to stock
  // behavior.
  follow(linktext) {
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) return undefined;
    this.openOutside(linktext);
    return Promise.resolve();
  }

  // Stop a pointer event on a dot link before Obsidian's own handlers see it
  // and route the click to the Open step; a mousedown is only stopped, so
  // one press opens once, and only a primary or middle-button press is
  // stopped or opens, as stock Obsidian opens a link on a primary or middle
  // press alone, and in Live Preview not on a Shift or Alt primary press
  // without Mod.
  guardClick(event) {
    if (event.button !== 0 && event.button !== 1) return;
    const linktext = clickedLinktext(event.target);
    if (linktext === null || !isDotLink(linktext)) return;
    // a Shift or Alt primary press without the Mod key (Cmd on macOS, Ctrl
    // elsewhere) extends the selection in Live Preview and follows nothing,
    // so it reaches stock; a reading-view anchor follows on any primary press
    const mod = process.platform === 'darwin' ? event.metaKey : event.ctrlKey;
    const anchored = event.target.closest('a.internal-link') !== null;
    if (event.button === 0 && !mod && (event.altKey || event.shiftKey) && !anchored) return;
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.type !== 'mousedown') this.openOutside(linktext);
  }

  // Open a dot link outside the vault: a markdown target inside a vault
  // Obsidian registers on this machine opens there through an
  // obsidian://open URI, whose `path` Obsidian decodes and hands to the most
  // specific vault holding it; every other target draws a notice naming the
  // root-relative path, with the stat's verdict when nothing is there. A dot
  // target landing back inside the vault names its prefix-free form instead,
  // as lint's fix does, and opens nothing.
  openOutside(linktext) {
    const root = this.app.vault.adapter.getBasePath();
    const spelled = linkTarget(linktext);
    const target = path.resolve(root, spelled);
    // a target landing back inside the vault, at the segment boundary holds()
    // compares at: its prefix-free form, and nothing opens
    const base = path.resolve(root);
    const prefix = base.endsWith(path.sep) ? base : base + path.sep;
    if (target === base || target.startsWith(prefix)) {
      let form = path.relative(base, target).split(path.sep).join('/') || '_index';
      const paged = fs.existsSync(target + '.md');
      // a folder `wiki update` indexes links by its index page, as lint's fix
      // does; the _index.md update mints marks such a folder, so one made
      // since the last update keeps its bare form until then
      if (target !== base && !paged && fs.existsSync(path.join(target, '_index.md'))) {
        form += '/_index';
      }
      const found = paged || fs.existsSync(target);
      new Notice(found ? `Inside the vault: use [[${form}]]` : `Inside the vault: ${form} (not found)`);
      return;
    }
    // the page a markdown target names: the text plus .md, as lint reads
    // it, so a trailing slash never names a page; or its explicit .md name
    let page = null;
    const pageForm = path.resolve(root, spelled + '.md');
    if (fs.existsSync(pageForm)) page = pageForm;
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
