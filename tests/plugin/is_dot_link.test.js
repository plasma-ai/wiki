'use strict';

// The plugin's dot-link predicate answers exactly where lint's `prefixed`
// classifier does: the rows are the link spellings lint's tests judge, and a
// row that drifts between the two fails here.

const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const { test } = require('node:test');

// load the plugin without Obsidian: the module's one require-time effect is
// `require('obsidian')`, answered here with the classes it destructures
const load = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'obsidian') {
    return { FileSystemAdapter: class {}, Notice: class {}, Plugin: class {} };
  }
  return load.call(this, request, ...rest);
};
const { isDotLink } = require(
  path.join(__dirname, '..', '..', 'wiki', '_assets', 'plugins', 'wiki-root-links', 'main.js'),
);
Module._load = load;

const rows = [
  // a literal file under an allowlisted folder, from any depth
  ['literal-file', '../src/main.py', true],
  ['nested-page-depth', '../../src/main.py', true],
  // a markdown file by its explicit name, and by stem
  ['page-explicit-md', '../docs/guide.md', true],
  ['page-stem', '../docs/guide', true],
  // a folder, and the entry itself
  ['plain-folder', '../src/pkg', true],
  ['entry-itself', '../src', true],
  // a dot-prefixed leaf, and segments with spaces and colons
  ['dot-leaf', '../dotdir/.zshrc', true],
  ['spaces-and-colon', '../sp ace/a:b', true],
  // a trailing slash
  ['page-trailing-slash', '../docs/guide/', true],
  // a stray bracket is junk: no '..' segment is seen
  ['stray-bracket', '[../src/main.py', false],
  // surrounding spaces and tabs are not part of the target
  ['leading-space', ' ../src/main.py', true],
  ['trailing-space', '../src/main.py ', true],
  ['tabs-both', '\t../src/main.py\t', true],
  // a folder name carrying a backslash, and an interior '..' that
  // normalizes out
  ['backslash-folder', '../we\\ird/x', true],
  ['normalizes-out', '../src/../docs/x', true],
  // a '..' chain clamped at the filesystem root
  ['clamped-at-filesystem-root', '../../../../../../../..', true],
  // an absolute target is read as written, wherever it lands
  ['absolute-under-entry', '/tmp/src/main.py', false],
  ['absolute-not-allowlisted', '/tmp/wiki/notes/meeting/', false],
  ['absolute-dot', '/../x', false],
  // a backslash is not a separator: no '..' segment is seen
  ['backslash-separator', '..\\src\\main.py', false],
  // spellings landing inside the wiki, and the wiki's parent
  ['dot-root-page', './overview', true],
  ['dot-sibling', './sibling', true],
  ['root', '..', true],
  ['dot-root', '.', true],
  ['interior', 'sibling/../sibling', true],
  ['missing', './gone', true],
  // misses under a present entry whose page-folder mirror names an in-wiki
  // target
  ['page', '../overview', true],
  ['raw-file', '../Makefile', true],
  ['indexed-folder', '../core', true],
  // an alias and an anchor ride outside the target
  ['aliased', '../src/main.py|label', true],
  ['anchored', '../docs/guide#h', true],
  // the alias is cut before the trim and the anchor after it, as lint reads
  // them: a space before the anchor stays part of the target
  ['space-before-anchor', '.. #a', false],
  ['space-before-alias', '.. |x', true],
  // a same-page anchor, and a prefix-free target
  ['same-page-anchor', '#heading', false],
  ['prefix-free', 'core/design', false],
];

for (const [id, link, expected] of rows) {
  test(`isDotLink: ${id}`, () => {
    assert.equal(isDotLink(link), expected, `[[${link}]]`);
  });
}
