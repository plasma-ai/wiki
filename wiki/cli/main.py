"""Command-line interface for ``wiki``."""

from __future__ import annotations

from typing import Any

import typer

from . import cmd

__all__ = ['cli']


def cli(**kwargs: Any) -> None:
    """Run the ``wiki`` CLI."""
    # construct app
    kwargs.setdefault('pretty_exceptions_enable', False)
    app = typer.Typer(name='wiki', **kwargs)
    # version callback
    cmd.version(app)
    # wiki commands
    cmd.install(app)
    cmd.init(app)
    cmd.config(app)
    cmd.trust(app)
    cmd.read(app)
    cmd.search(app)
    cmd.update(app)
    cmd.lint(app)
    cmd.map(app)
    cmd.merge(app)
    # run app
    app()


if __name__ == '__main__':
    cli()
