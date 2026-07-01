"""Implements ``wiki`` commands."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import pathlib
import shutil
from typing import Optional

import typer

from wiki.cli.utils import command

__all__ = [
    'version',
    'install',
]


def version(app: typer.Typer) -> typer.Typer:
    """Register the ``--version`` flag on the root callback."""

    def _version_callback(value: bool) -> None:
        """Print the installed ``plasma-wiki`` version and exit."""
        if value:
            typer.echo(importlib.metadata.version('plasma-wiki'))
            raise typer.Exit()

    # version flag
    version_help = 'Show the version and exit.'
    version = typer.Option(
        None,
        '--version',
        callback=_version_callback,
        is_eager=True,
        help=version_help,
    )

    @app.callback()
    def _main(version: Optional[bool] = version) -> None:
        """Wiki command-line interface."""

    return app


def install(app: typer.Typer) -> typer.Typer:
    """Register the ``install`` command."""
    # project flag
    project_help = 'Install config in cwd rather than home directory.'
    project = typer.Option(False, '--project', help=project_help)

    @command(app, 'install')
    def _install(
        project: bool = project,
    ) -> None:
        """Install the wiki skill for Claude Code and Codex.

        Copies the bundled skill into the Claude (.claude/skills) and Codex
        (.agents/skills) skill directories. Targets your home directory by
        default, or the current project with --project.
        """
        # resolve install directory
        if project:
            root = pathlib.Path.cwd()
        else:
            root = pathlib.Path.home()
        # resolve agent skill directories
        targets = [
            root / '.claude' / 'skills',
            root / '.agents' / 'skills',
        ]
        # copy each bundled skill into every target (replaces any prior copy)
        base = importlib.resources.files('wiki')
        skills = base.joinpath('skills')
        for skill in sorted(path for path in skills.iterdir() if path.is_dir()):
            for target in targets:
                dest = target / skill.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.is_file():
                    dest.unlink()
                elif dest.is_dir():
                    shutil.rmtree(dest)
                shutil.copytree(skill, dest)
                typer.echo(f'Installed {skill.name} -> {dest}')

    return app
