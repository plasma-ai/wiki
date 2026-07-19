"""Implements ``Event`` class."""

from __future__ import annotations

import copy
import logging
import re
import typing
from typing import Any, Optional

__all__ = ['Event']


class Event:
    """Base class for wiki events.

    Concrete events are near-empty declarative subclasses: payload
    fields are bare class annotations extracted from ``kwargs`` by this
    ``__init__`` and deep-copied (snapshot semantics). Wiki events are
    payload-only -- the emitting wiki is available as ``self`` at every
    hook, so no resource binding is carried. ``logging_level`` is the
    ``on_notice`` funnel's fallback severity; per-kind severity lives
    in the hook signature defaults.
    """

    logging_level: int = logging.WARNING

    def __init__(
        self: Event,
        message: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize event.

        Args:
            message: Optional caller-supplied free-form context,
                bound as ``self.message`` for hook overrides to read;
                concrete events render their notice line without it.

        """
        # bind annotated event attributes, deep-copied so the event
        # snapshots state at emission time
        for name in typing.get_type_hints(self.__class__):
            if name in kwargs:
                setattr(self, name, copy.deepcopy(kwargs.pop(name)))
        # a leftover kwarg is a misspelled payload field: fail at the
        # emit site rather than rendering a broken description later
        if kwargs:
            raise TypeError(f'Unexpected event fields: {sorted(kwargs)}')
        # bind message
        self.message = message

    @property
    def name(self: Event) -> str:
        """Return event name.

        Default format is ``EVENT_NAME``, derived from the class name
        in screaming snake case.

        Returns:
            Event name.

        """
        name = self.__class__.__name__
        return re.sub(r'(?<!^)(?=[A-Z])', '_', name).upper()

    @property
    def description(self: Event) -> str:
        """Return event description.

        Composes the event name plus the optional message on a new
        line. Overridden by every concrete event to render its exact
        human-readable notice line.

        Returns:
            Full event description for logging.

        """
        result = self.name
        if self.message:
            result += f'\n{self.message}'
        return result
