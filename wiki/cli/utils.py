"""Shared helpers for ``wiki`` CLI commands."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import typer

__all__ = [
    'command',
]


def command(
    app: typer.Typer,
    name: str,
    **kwargs: Any,
) -> Callable:
    """Register a CLI command on ``app`` with error wrapping."""

    def decorator(f: Callable, /) -> Callable:
        if private := name.startswith('_'):
            kwargs.setdefault('hidden', True)

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except (typer.Exit, typer.Abort, typer.BadParameter):
                raise
            except Exception as e:
                error = type(e).__name__ if private else 'Error'
                typer.echo(f'{error}: {e}', err=True)
                raise SystemExit(1) from None

        return app.command(name, **kwargs)(wrapper)

    return decorator
