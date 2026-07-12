"""Type hints for ``wiki``."""

from __future__ import annotations

import os
from typing import Union

__all__ = [
    'Link',
    'PathLike',
]

# index link row as (target, label, desc)
Link = tuple[str, str, str]

# filesystem path accepted at boundaries
PathLike = Union[str, os.PathLike]
