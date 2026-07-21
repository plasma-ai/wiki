"""Type hints for ``wiki``."""

from __future__ import annotations

import os
from typing import Union

__all__ = [
    'PathLike',
    'Link',
]

#: filesystem path accepted at boundaries
PathLike = Union[str, os.PathLike]

#: index link row as (target, label, desc)
Link = tuple[str, str, str]
