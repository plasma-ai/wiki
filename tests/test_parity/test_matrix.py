"""Test the ``parity.matrix`` module.

The stages are covered through the self-check, which runs every case;
this module carries what the sample may draw.
"""

from __future__ import annotations

import pathlib

from parity import matrix

__all__ = ['test_sample_draws_plain_pages_and_the_words_their_commands_read']


def test_sample_draws_plain_pages_and_the_words_their_commands_read(
    tmp_path: pathlib.Path,
) -> None:
    """The sample names regular pages, and words from the text each command reads.

    A symlinked page and a name that opens with a dash are not drawn. Body
    words feed the searches, and the field words come from the field their
    match reads. Part of a longer word is no word: the search index holds
    the whole word, with its accent folded. A page with no ``name`` field
    gives no word: the wiki does not manage it, so search may not hold it.
    """
    # a wiki holding a managed page, a dashed name, a bare page and a symlink
    root = tmp_path / 'wiki'
    (root / 'topic').mkdir(parents=True)
    for folder in (root, root / 'topic'):
        (folder / '_index.md').write_text('# Index\n', encoding='utf-8')
    page = (
        '---\n'
        'name: topic/entry\n'
        'desc: Summary sentence.\n'
        'tags: [alpha, gamma]\n'
        '---\n\n'
        '# Heading\n\nSchrödinger wrote paragraphs.\n'
    )
    (root / 'topic' / 'entry.md').write_text(page, encoding='utf-8')
    (root / 'topic' / '-dashed.md').write_text(page, encoding='utf-8')
    (root / 'topic' / 'bare.md').write_text('Unmanaged prose.\n', encoding='utf-8')
    (root / 'linked.md').symlink_to(root / 'topic' / 'entry.md')
    # the sample names the folder and the regular pages
    drawn = matrix.sample(root, seed=0)
    assert drawn.scopes == ('topic',)
    assert set(drawn.pages) == {'topic/bare.md', 'topic/entry.md'}
    # each word comes from the text its command reads
    body = {'Heading', 'wrote', 'paragraphs'}
    terms = set(drawn.terms)
    assert terms
    assert terms <= body
    assert drawn.scoped in body
    assert drawn.desc in {'Summary', 'sentence'}
    assert drawn.label in {'topic', 'entry', 'alpha', 'gamma'}
    word, tag = drawn.tagged
    assert word in body
    assert tag in {'alpha', 'gamma'}
