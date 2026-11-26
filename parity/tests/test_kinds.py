"""Test the ``parity.kinds`` module.

The committed list is read by every replay and held to v1's kinds by a
whole one, which ``test_replay`` covers on a small recording; this module
carries the refusals that keep a kind from a home nothing can check.
"""

from __future__ import annotations

import pathlib

import pytest

from parity import kinds

__all__ = ['test_list_that_breaks_a_rule_is_refused']

# one list that keeps every rule
_TEST = 'tests/test_core/test_lint.py::test_lint_names_bare_page'
_LIST = f'[notices]\nlink_add = "perturbation"\n\n[issues]\nbare_page = "{_TEST}"\n'


@pytest.mark.parametrize(
    argnames=('text', 'message'),
    argvalues=[
        (f'{_LIST}\n[events]\n', 'unknown key'),
        ('notices = ["link_add"]\n', 'notices must be a table'),
        (_LIST.replace('"perturbation"', '"somewhere"'), 'the home of link_add'),
        (_LIST.replace('"perturbation"', '1'), 'the home of link_add'),
    ],
    ids=['key', 'no table', 'no home', 'shape'],
)
def test_list_that_breaks_a_rule_is_refused(
    tmp_path: pathlib.Path,
    text: str,
    message: str,
) -> None:
    """A kind has a home the replay or the perturbation can answer for.

    The list holds the two families and nothing else, and a home is the
    perturbation or the id of a test. A list that keeps the rules comes
    back by family, each kind with its home, and so does the committed
    one.
    """
    path = tmp_path / 'kinds.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        kinds.load(path)
    path.write_text(_LIST, encoding='utf-8')
    assert kinds.load(path) == {
        'notices': {'link_add': kinds.PERTURBATION},
        'issues': {'bare_page': _TEST},
    }
    assert sorted(kinds.load()) == sorted(kinds.FAMILIES)
