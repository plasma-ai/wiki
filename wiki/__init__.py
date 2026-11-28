"""The ``wiki`` package.

Indexed knowledge bases with command-line tools for agents.
"""

# NOTE: cli is imported by name and never wildcarded -- `from .cli import *`
#   would bind the cli() entry function over the subpackage, and its helpers
#   are library API a caller reaches by attribute path (wiki.cli.utils)
from . import cli, constants, core, typing, util
from .constants import *
from .core import *

__version__ = '1.10.0'
