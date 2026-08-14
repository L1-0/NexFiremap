"""The exception taxonomy every operational write path raises.

These live in their own module rather than beside `OperationsStore`
because *everything* in this package needs them - the vocabulary
validators, the per-aggregate stores, the package import/export code -
and because `api.py` maps them onto HTTP status codes (`OperationsError`
-> 400, `NotFoundError` -> 404, `RevisionConflict`/`PackageConflict` ->
409 with the conflicting record attached). A module with no imports of
its own keeps that dependency edge one-directional and import-cycle-free.

All four are re-exported from `nexfiremap.operations`, which is the name
the rest of the codebase (and the tests) import them under; nothing
outside this package should need to reach in here directly.
"""

from __future__ import annotations

from typing import Any


class OperationsError(ValueError):
    """A request cannot be applied to the operational record."""


class NotFoundError(OperationsError):
    """The referenced incident/period/scenario/feature/... row does not exist
    (or does not belong to the parent it was requested under)."""
    pass


class RevisionConflict(OperationsError):
    """Raised when a write's `expected_revision` no longer matches the row's
    current `revision`. This is the optimistic-concurrency signal used
    throughout the store: every mutable row carries a monotonically
    increasing `revision`, callers must load-then-send-back that number, and
    a mismatch here means someone else (another operator, another device)
    changed the record first. `entity` carries the current, authoritative
    row so the caller can show the operator what changed instead of just
    failing."""

    def __init__(self, entity: dict[str, Any]) -> None:
        super().__init__("record changed since it was loaded")
        self.entity = entity


class PackageConflict(OperationsError):
    """Raised by import_bundle() when an incoming incident package overlaps
    an existing incident and cannot be merged automatically. `report` is the
    same structure preview_import() returns, so the caller can present the
    conflicts for manual resolution rather than losing data by guessing."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("incident package cannot be applied without conflict resolution")
        self.report = report
