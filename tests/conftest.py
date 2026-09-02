"""Shared fixtures: a hand-driven clock.

Nothing here reads the system clock. Every instant in this package's tests comes from
:class:`ManualClock`, because determinism (spec §11 contract 3) is invisible against a clock that
moves on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

MIDDAY = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
"""A millisecond-aligned instant, so it survives a `governance.egress_decision` round trip exactly
(``TimestampField`` truncates to millisecond precision)."""

DAY = timedelta(days=1)


class ManualClock:
    """A clock the test moves by hand, satisfying :data:`baseaicore.Clock`."""

    def __init__(self, start: datetime = MIDDAY) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = when


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()
