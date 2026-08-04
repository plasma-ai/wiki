"""The ``wiki`` package.

Indexed knowledge bases with command-line tools for agents.
"""

# the CLI is an entry point, not library API: star-importing it would
# rebind the `cli` attribute from the subpackage to the Typer app runner,
# breaking `import wiki.cli.utils` for library consumers
from . import cli, constants, core, typing, util
from .constants import *
from .core import *

__version__ = '1.2.0'
