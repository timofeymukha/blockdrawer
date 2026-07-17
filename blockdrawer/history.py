"""UI-independent undo/redo history for complete topology snapshots."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .model import MeshModel
from .session import from_data, to_data


class ModelHistory:
    """Store bounded, validated model snapshots for undo and redo.

    Complete snapshots are intentionally used instead of inverse commands: model
    operations can propagate one edit across many edges, and restoring a validated
    snapshot keeps those compound changes atomic.
    """

    def __init__(self, model: MeshModel, *, limit: int = 200) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
            raise ValueError("History limit must be an integer of at least 2")
        self.limit = limit
        self._states: list[dict[str, Any]] = []
        self._index = 0
        self._saved_state: dict[str, Any] = {}
        self.reset(model)

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index + 1 < len(self._states)

    def reset(self, model: MeshModel) -> None:
        """Start a new history and treat the supplied model as saved."""
        state = to_data(model)
        self._states = [state]
        self._index = 0
        self._saved_state = deepcopy(state)

    def record(self, model: MeshModel) -> bool:
        """Record a changed model, discarding a now-inaccessible redo branch."""
        state = to_data(model)
        if state == self._states[self._index]:
            return False

        del self._states[self._index + 1:]
        self._states.append(state)
        self._index = len(self._states) - 1
        if len(self._states) > self.limit:
            excess = len(self._states) - self.limit
            del self._states[:excess]
            self._index -= excess
        return True

    def undo(self) -> MeshModel | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return from_data(deepcopy(self._states[self._index]))

    def redo(self) -> MeshModel | None:
        if not self.can_redo:
            return None
        self._index += 1
        return from_data(deepcopy(self._states[self._index]))

    def mark_saved(self, model: MeshModel) -> None:
        self._saved_state = deepcopy(to_data(model))

    def is_dirty(self, model: MeshModel) -> bool:
        return to_data(model) != self._saved_state
