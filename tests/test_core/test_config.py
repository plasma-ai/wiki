"""Behavioral tests for ``Wiki.update_config``.

The Obsidian install: staged plugin downloads (stubbed at the
``_download`` boundary), config merging, returned warnings vs
notices, and the ``OFFLINE_MODE`` matrix.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest

from wiki.constants import OFFLINE_MODE
from wiki.core import _obsidian
from wiki.core.wiki import Wiki

from ._helpers import _capture_notices

__all__ = [
    'test_update_config_installs_plugin',
    'test_update_config_refuses_a_checksum_mismatch',
    'test_update_config_offline_warns',
    'test_update_config_keeps_notices_off_warnings',
    'test_update_config_preserves_existing',
    'test_update_config_is_idempotent',
    'test_update_config_seeds_missing_config_dir',
    'test_update_config_rejects_type_mismatch',
    'test_update_config_reports_malformed_target_json',
    'test_update_config_offline_mode',
    'test_update_config_rejects_bad_offline_mode',
    'test_init_rejects_bad_offline_mode_before_scaffolding',
]


# ------ fixtures


@pytest.fixture
def stub_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the plugin download with a marker write (no real network).

    The digest pins follow the marker bytes, so the stubbed install
    passes the checksum gate the way a genuinely pinned release does.
    """

    def download(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Write marker bytes instead of fetching from the network."""
        target.write_bytes(b'CODE')

    monkeypatch.setattr(Wiki, '_download', download)
    digest = hashlib.sha256(b'CODE').hexdigest()
    for plugin, digests in _obsidian._OBSIDIAN_PLUGIN_DIGESTS.items():
        monkeypatch.setitem(
            _obsidian._OBSIDIAN_PLUGIN_DIGESTS,
            plugin,
            dict.fromkeys(digests, digest),
        )


# ------ plugin install and merge


def test_update_config_installs_plugin(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` installs the bundled plugin into ``.obsidian/``."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()

    # update_config copies settings, downloads code, and enables the plugin
    warnings = wiki.update_config()
    assert warnings == []
    plugin_id = 'obsidian-front-matter-title-plugin'
    plugin = tmp_path / '.obsidian' / 'plugins' / plugin_id
    assert (plugin / 'main.js').read_bytes() == b'CODE'
    assert (plugin / 'manifest.json').read_bytes() == b'CODE'
    assert (plugin / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))
    # installed assets honor the umask like every write_atomic surface,
    # never mkstemp's owner-only temp mode
    umask = os.umask(0)
    os.umask(umask)
    expected = 0o666 & ~umask
    assert (plugin / 'main.js').stat().st_mode & 0o777 == expected
    assert (plugin / 'manifest.json').stat().st_mode & 0o777 == expected


def test_update_config_refuses_a_checksum_mismatch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A download failing its pinned digest is refused, never installed.

    Release assets are mutable upstream, so the install trusts the
    pinned digest, not the URL: a swapped artifact is discarded before
    it reaches the vault, the warning names the mismatch (a refusal,
    not a network hiccup), and no staged temp file is left behind.
    """
    wiki = Wiki(tmp_path)
    wiki.init()

    # the fetched bytes do not match the real pinned digests
    def tampered(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Write bytes that fail the pinned-digest check."""
        target.write_bytes(b'EVIL')

    monkeypatch.setattr(Wiki, '_download', tampered)

    # the mismatch is refused with a warning naming the digest failure
    warnings = wiki.update_config()
    assert any('refused' in warning.lower() for warning in warnings)
    assert any('digest' in warning.lower() for warning in warnings)

    # nothing installed: no plugin code, no stray staged temp files
    plugin_id = 'obsidian-front-matter-title-plugin'
    plugin = tmp_path / '.obsidian' / 'plugins' / plugin_id
    assert not (plugin / 'main.js').exists()
    assert not list(plugin.glob('*main.js'))
    assert not list(plugin.glob('*manifest.json'))
    # settings and the enabled-plugins list still apply (soft failure)
    assert (plugin / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


def test_update_config_offline_warns(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed plugin download warns but still applies local config."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()

    # stub the download boundary to simulate a failure
    def offline(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Raise as if the download failed."""
        raise OSError('no network')

    monkeypatch.setattr(Wiki, '_download', offline)

    # the failed download is a soft failure (warning, not error)
    warnings = wiki.update_config()
    assert any('could not download' in warning.lower() for warning in warnings)

    # settings and the enabled-plugins list are still written
    plugin_id = 'obsidian-front-matter-title-plugin'
    plugin = tmp_path / '.obsidian' / 'plugins' / plugin_id
    assert (plugin / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


def test_update_config_keeps_notices_off_warnings(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """Restoring the root marker is a notice, never a returned warning.

    The returned warnings mean setup needs another run and gate the
    CLI's success output, so an informational restoration must ride the
    notice channel (stderr) instead of masquerading as a failed plugin
    download.
    """
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / '.wiki' / 'settings.json').unlink()
    notices = _capture_notices(wiki)

    # the marker is restored and announced, with no warnings returned
    assert wiki.update_config() == []
    assert 'Restored missing' in '\n'.join(event.description for event in notices)
    assert (tmp_path / '.wiki' / 'settings.json').is_file()


def test_update_config_preserves_existing(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` merges into existing config without clobbering it."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()

    # seed an unrelated enabled plugin and a top-level app.json
    obsidian_dir = tmp_path / '.obsidian'
    (obsidian_dir / 'plugins' / 'other-plugin').mkdir(parents=True)
    (obsidian_dir / 'plugins' / 'other-plugin' / 'main.js').write_bytes(b'OTHER')
    (obsidian_dir / 'community-plugins.json').write_text(
        json.dumps(['other-plugin']),
        encoding='utf-8',
    )
    (obsidian_dir / 'app.json').write_text(
        json.dumps({'existing': 1}),
        encoding='utf-8',
    )
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(
        json.dumps({'setting': True}),
        encoding='utf-8',
    )

    wiki.update_config()

    # the unrelated plugin and its enabled entry survive
    other_main = obsidian_dir / 'plugins' / 'other-plugin' / 'main.js'
    assert other_main.read_bytes() == b'OTHER'
    enabled = json.loads(
        (obsidian_dir / 'community-plugins.json').read_text(encoding='utf-8')
    )
    assert 'other-plugin' in enabled
    assert 'obsidian-front-matter-title-plugin' in enabled

    # the top-level app.json is deep-merged, not replaced
    app = json.loads((obsidian_dir / 'app.json').read_text(encoding='utf-8'))
    assert app == {'existing': 1, 'setting': True}


def test_update_config_is_idempotent(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """Re-running ``update_config`` leaves the merged config unchanged."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # first run materializes the config
    wiki.update_config()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    first = cp_file.read_text(encoding='utf-8')

    # a second run must not duplicate the enabled-plugin entry
    wiki.update_config()
    assert cp_file.read_text(encoding='utf-8') == first
    enabled = json.loads(first)
    assert enabled.count('obsidian-front-matter-title-plugin') == 1


def test_update_config_seeds_missing_config_dir(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """A missing ``.wiki/obsidian`` is seeded from the stock template.

    An adopted index tree (or a wiki whose ``.wiki/`` was lost) has no
    staged Obsidian config, and ``init`` refuses to re-run on it, so
    ``update_config`` must seed the staging directory itself and complete
    the full setup rather than aborting on an internal path.
    """
    # a wiki that was never initialized has no .wiki/obsidian
    wiki = Wiki(tmp_path)
    assert wiki.update_config() == []

    # the staging directory is seeded and the plugin fully installed
    plugin_id = 'obsidian-front-matter-title-plugin'
    config_dir = tmp_path / '.wiki' / 'obsidian'
    assert (config_dir / 'community-plugins.json').is_file()
    assert (config_dir / 'plugins' / plugin_id / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


# ------ config validation


def test_update_config_rejects_type_mismatch(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` raises on a top-level JSON type mismatch."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # a list source against a dict target cannot be merged
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(json.dumps([1, 2]), encoding='utf-8')
    obsidian_dir = tmp_path / '.obsidian'
    obsidian_dir.mkdir(exist_ok=True)
    (obsidian_dir / 'app.json').write_text(json.dumps({'a': 1}), encoding='utf-8')

    with pytest.raises(TypeError, match=r'\.obsidian/app\.json'):
        wiki.update_config()


def test_update_config_reports_malformed_target_json(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """A malformed existing ``.obsidian`` JSON names the file instead of a bare error.

    The target is user-editable, so a hand-edit or truncated write must
    surface a diagnosable message rather than an undiagnosable JSON error.
    """
    wiki = Wiki(tmp_path)
    wiki.init()

    # an already-installed top-level config is then corrupted by hand
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(json.dumps({'a': 1}), encoding='utf-8')
    obsidian_dir = tmp_path / '.obsidian'
    obsidian_dir.mkdir(exist_ok=True)
    (obsidian_dir / 'app.json').write_text('{ "a": 1, }', encoding='utf-8')

    with pytest.raises(ValueError, match=r'\.obsidian/app\.json'):
        wiki.update_config()


# ------ OFFLINE_MODE


@pytest.mark.parametrize(
    argnames=('value', 'offline'),
    argvalues=[
        ('true', True),
        ('TRUE', True),
        (' true ', True),
        ('false', False),
        ('', False),
    ],
)
def test_update_config_offline_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    offline: bool,
) -> None:
    """``OFFLINE_MODE`` skips the download only for truthy values."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # record whether the download boundary is reached
    reached = []

    def download(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Record the call and write marker bytes."""
        reached.append(url)
        target.write_bytes(b'CODE')

    monkeypatch.setattr(Wiki, '_download', download)
    monkeypatch.setenv(OFFLINE_MODE, value)

    wiki.update_config()
    assert (not reached) == offline


@pytest.mark.parametrize('value', ['1', '0', 'maybe'])
def test_update_config_rejects_bad_offline_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """An unrecognized ``OFFLINE_MODE`` value is rejected."""
    wiki = Wiki(tmp_path)
    wiki.init()
    monkeypatch.setenv(OFFLINE_MODE, value)
    with pytest.raises(ValueError, match='OFFLINE_MODE'):
        wiki.update_config()


def test_init_rejects_bad_offline_mode_before_scaffolding(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad ``OFFLINE_MODE`` aborts ``init`` before writing anything.

    ``init`` scaffolds the wiki that ``update_config`` configures next, so a
    value ``update_config`` would reject must fail up front rather than strand
    a half-built wiki (root index written, ``.obsidian`` missing) that the
    re-init guard then refuses to finish.
    """
    root = tmp_path / 'wiki'
    monkeypatch.setenv(OFFLINE_MODE, 'maybe')

    # init raises and leaves no scaffolding behind
    wiki = Wiki(root)
    with pytest.raises(ValueError, match='OFFLINE_MODE'):
        wiki.init()
    assert not root.exists()
