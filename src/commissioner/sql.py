"""Mountable table and the durable ledger — ``commissioner[sql]`` (ADR-0050, spec §7).

This module ships **a table shape, not a database**. :func:`mount_egress_tables` adds one table to
a :class:`~sqlalchemy.MetaData` the *application* owns, so it appears in the application's own
``alembic revision --autogenerate`` beside the tables it wrote itself, upgrades with its own
history, and is backed up, restored and pruned by whoever owns that database. Two applications
mounting this table have two tables in two databases — never one.

What this module deliberately does not have:

* **No engine, no URL, no session of its own.** :class:`SqlEgressLedger` takes a callable
  returning a session and nothing else (ADR-0050 decision 3). It reads no environment variable,
  opens no file, and holds no connection between calls.
* **No migration history, and no ``create_all``.** Not on import, not lazily, not ever — a package
  that migrated an application's database would own half of a history nobody could reason about
  (ADR-0050 decision 5). Tests create the table; the library does not.
* **No sibling import**, ``weightsdb`` included: the engine, pragma, migration-runner and backup
  machinery belongs to the application that owns the database (ADR-0050 decision 4, enforced by
  ``.importlinter``).
* **No update path and no delete path.** A ledger of governance decisions is only as trustworthy
  as its inability to rewrite its own history (spec §14); this module offers exactly two
  operations, insert and select, and no third one arrives quietly. See
  ``tests/unit/test_ledger_surface.py`` for the structural assertion, and
  ``tests/integration/test_sql_ledger.py`` for the same claim against this module's own AST.

**Where the evaluation lives.** Nowhere here. A decision is already fully formed —
``decision_id``, verdict, reason, policy name and version, both timestamps — by the time
:meth:`SqlEgressLedger.record` ever sees it (spec §7): this ledger persists and queries, and
computes nothing. Unlike a budget ledger, there is no shared aggregate a concurrent writer could
corrupt (no balance, no running total), so a decision's row is independent of every other row and
:meth:`record` is a single, ordinary ``INSERT``.

**No observable difference from :class:`~commissioner.ledger.InMemoryEgressLedger`.** A decision
read back through :meth:`SqlEgressLedger.decisions` is field-for-field what :meth:`record` was
given — there is no derived value analogous to LoadLedger's ``CostEstimate`` for this ledger to
omit. The stored ``decision_json`` is the whole decision, rendered through the same
``to_payload()``/``from_payload()`` this package's payload round-trip already golden-tests
(spec §11 contract 4), so this module invents no second serialization of a decision.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

import sqlalchemy as sa
from setspec import canonical_dumps
from setspec.governance.v1 import GovernanceEgressDecisionIn
from sqlalchemy.exc import SQLAlchemyError

from commissioner.errors import StoreFailure, UnsupportedDialect
from commissioner.types import EgressDecision, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from datetime import datetime

    from sqlalchemy import MetaData, RowMapping, Table
    from sqlalchemy.orm import Session

__all__ = ["EgressTables", "SqlEgressLedger", "mount_egress_tables"]

DEFAULT_TABLE_PREFIX = "egress_"
"""The documented default prefix, part of the mounted contract (ADR-0050 consequences).

Changing it in a host that has already migrated is a table rename, not a configuration change.
"""


@dataclass(frozen=True, slots=True)
class EgressTables:
    """The one table :func:`mount_egress_tables` added to a host's metadata (spec §7, §10).

    A handle, not a repository. The honest answer to "what does a host do with this?" is *hold it,
    or drop it on the floor*: the table is already in the metadata the host passed, which is what
    autogenerate reads, so a host that mounts and discards the return value has lost nothing. It is
    returned so that a host can name the table in a ``create_all(tables=...)`` call, assert on its
    shape in its own tests, or check the prefix it got — and so that :class:`SqlEgressLedger` has
    one place to look it up.

    An application that reaches in for the :class:`~sqlalchemy.Table` in order to **join** it to
    one of its own entities is doing what ADR-0050 decision 2 forbids: this table carries no
    foreign key out of the mounted set, ``run_id`` and ``target_name`` are opaque strings, and a
    join against it freezes a shape this package is free to change under an upgrade note
    (spec §19). Read it through :class:`SqlEgressLedger`.

    Attributes:
        prefix: The prefix the table, its indexes and its primary-key constraint all carry.
        decisions: One row per recorded decision, approved, denied or a written-after-the-fact
            violation alike. The whole ledger — there is nothing to aggregate, so there is nothing
            beside it.
    """

    prefix: str
    decisions: Table

    @property
    def metadata(self) -> MetaData:
        """Return the host metadata this table was mounted into.

        Reachable because it already is — ``tables.decisions.metadata`` is the same object — and
        naming it here saves a host reaching through the table to find what it passed in.
        """
        return self.decisions.metadata

    @property
    def all_tables(self) -> tuple[Table, ...]:
        """Return every mounted table.

        A tuple of one, kept for shape parity with LoadLedger's ``LedgerTables`` — a host that
        already writes ``metadata.create_all(engine, tables=list(tables.all_tables))`` for one
        mounted package writes the identical line for this one.
        """
        return (self.decisions,)


def mount_egress_tables(metadata: MetaData, *, prefix: str = DEFAULT_TABLE_PREFIX) -> EgressTables:
    """Add this package's table to an application's metadata, and return a handle to it.

    The whole of ADR-0050's mounting pattern, for a single table. The application passes its
    **own** :class:`~sqlalchemy.MetaData` — the one its Alembic ``env.py`` names as
    ``target_metadata`` — and gets one table in it that autogenerate sees exactly like a table the
    application wrote.  Nothing is created here: no DDL is emitted, no connection is opened, and no
    migration is run (ADR-0050 decision 5).

    **Mount eagerly, at module import, in the host's model package.** Autogenerate only sees what
    was mounted before the metadata was inspected, so a host that mounts lazily — inside a request
    handler, or behind a feature flag — gets a migration that silently *drops* this table. That is
    the named failure mode of this pattern, and it is why the miniature-host test in this
    repository autogenerates rather than merely creating the table.

    Why this shape — the table and its keys are normative, in spec §10:

    * **Columns are plain and portable** — ``VARCHAR``, ``TEXT`` and ``TIMESTAMP WITH TIME ZONE``,
      and nothing else. No ORM base with domain meaning, no dialect-specific type, no ``JSONB``,
      no ``ENUM``, no foreign key leaving the set. A host's autogenerated revision must run
      unchanged on both supported dialects (ADR-0006), and a type that renders differently on each
      is how that stops being true.
    * **``verdict`` is a plain string column holding the enum's value, not ``sa.Enum``.**
      ``sa.Enum`` creates a native ``ENUM`` type on PostgreSQL and a ``CHECK`` constraint on
      SQLite — the same code producing two different schemas on the two supported dialects, which
      is exactly the failure mode the development plan names ("dialect-specific enum storage").
      Storing ``Verdict(...).value`` as text and constructing ``Verdict(...)`` back at the
      boundary keeps one column type on both dialects and needs no migration when a third verdict
      is ever added.
    * **There is no accumulating column, and therefore no ``BigInteger`` trap here.** LoadLedger's
      balances sum money and tokens without bound, which is why every summed column there is
      eight bytes wide. This table never sums anything — it is one immutable row per decision —
      so nothing in it grows past what a caller wrote.
    * **``decided_at`` is ``DateTime(timezone=True)``, and the ledger normalizes to UTC before
      binding.** SQLite has no timezone-aware storage: it writes whatever wall clock it is handed
      and returns a *naive* value. Storing UTC and attaching UTC on the way out makes the two
      dialects agree and keeps a string comparison on SQLite ordering correctly, which is what the
      ``since`` filter relies on.
    * **``decision_json`` is ``TEXT`` holding the canonical form of the decision's own SetSpec
      payload**, not ``sa.JSON``. This is a decision ledger's whole reason to exist: a stored
      record must describe, byte for byte, what was decided under a policy that may since have
      changed. ``sa.JSON`` would store whatever the driver's own ``json.dumps`` produced instead,
      and PostgreSQL's ``json`` type has no equality operator, so ``SELECT DISTINCT`` or
      ``WHERE col = :x`` against it fails there and succeeds on SQLite — a dialect divergence
      introduced by the storage type itself, in a package whose whole claim is portability. The
      cost is real and accepted: no server-side JSON operators and no index into a record's own
      fields, on either dialect. A host that needs to query inside a decision adds its own column,
      index or view in its own migration — it must not change this one.

    Args:
        metadata: The application's own :class:`~sqlalchemy.MetaData`. Mutated: one table is added
            to it.
        prefix: The string the table, its indexes and its primary-key constraint name all begin
            with. Configurable so two mounts can coexist in one metadata, and so a host with a
            colliding name can move out of the way; collisions are the host's to avoid. Index
            names are global per schema on PostgreSQL, which is why they carry the prefix too.

    Returns:
        An :class:`EgressTables` naming the table just added.

    Raises:
        ValueError: If ``prefix`` is empty or is not a plain SQL identifier prefix
            (``[A-Za-z_][A-Za-z0-9_]*``). An empty prefix would mount a table called ``decisions``
            into an application's schema, which is a collision waiting for a second package.
        sqlalchemy.exc.InvalidRequestError: If a table of the same name is already in
            ``metadata`` — mounting the same prefix twice, usually because a host's model module
            was imported under two names. Mount once, at import.
    """
    if not prefix or not prefix[0].isascii() or not (prefix[0].isalpha() or prefix[0] == "_"):
        raise ValueError(
            f"mount_egress_tables(prefix=...) must be a non-empty SQL identifier prefix "
            f"beginning with a letter or underscore; got {prefix!r}. An empty prefix would mount "
            f"a table named 'decisions' into the application's schema."
        )
    if not all(
        character.isascii() and (character.isalnum() or character == "_") for character in prefix
    ):
        raise ValueError(
            f"mount_egress_tables(prefix=...) must contain only ASCII letters, digits and "
            f"underscores; got {prefix!r}."
        )

    decisions = sa.Table(
        f"{prefix}decisions",
        metadata,
        sa.Column("decision_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("target_name", sa.String(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("decision_id", name=f"pk_{prefix}decisions"),
        # (run_id, decided_at) rather than (run_id): a per-run history query is answered from the
        # index alone, already in the order decisions() returns.
        sa.Index(f"ix_{prefix}decisions_run", "run_id", "decided_at"),
        sa.Index(f"ix_{prefix}decisions_decided_at", "decided_at"),
    )
    return EgressTables(prefix=prefix, decisions=decisions)


class SqlEgressLedger:
    """A ledger over a table an application mounted in its own database (spec §7).

    Observably :class:`~commissioner.ledger.InMemoryEgressLedger` with a different store — see the
    module docstring for why there is nothing this class derives that the in-memory ledger does
    not also derive.

    **Stateless and cheap to construct.** Nothing is cached between calls and nothing is
    configured beyond the prefix: this ledger reads no clock, mints no id and evaluates no policy —
    it persists whatever :class:`~commissioner.types.EgressDecision` it is given.

    ## Concurrency

    Every write is a single ``INSERT`` of an independent row, keyed by the caller's own
    ``decision_id``. Unlike a budget ledger there is no shared aggregate for two writers to race
    over — no balance, no running total — so two processes recording two different decisions never
    contend for the same row, and there is nothing here for an upsert to protect. A duplicate
    ``decision_id`` is a caller bug (two decisions cannot share an identity), and it surfaces as
    :class:`~commissioner.errors.StoreFailure` from the primary-key violation rather than being
    silently absorbed — an append-only ledger must not have an "insert or ignore" path, because
    that path is indistinguishable from a quiet update.

    ## Sessions

    The factory must return a session **this ledger may own**: it commits it and closes it. A host
    that needs a record inside its own unit of work passes a factory that returns a session joined
    to its transaction as a savepoint::

        session_factory=lambda: Session(bind=connection, join_transaction_mode="create_savepoint")

    in which case this class's ``commit`` releases the savepoint and the host's transaction still
    decides the outcome.
    """

    __slots__ = ("_session_factory", "_tables")

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        table_prefix: str = DEFAULT_TABLE_PREFIX,
    ) -> None:
        """Build a ledger over a host's already-migrated table.

        Args:
            session_factory: Returns a SQLAlchemy 2.0 :class:`~sqlalchemy.orm.Session` this ledger
                may commit and close. Injected, per ADR-0050 decision 3: this package opens no
                connection, reads no URL and holds no engine, so the application decides the
                dialect, the pool, the pragmas and the file.
            table_prefix: The prefix the host passed to :func:`mount_egress_tables`. It must
                match; nothing here can check that it does until a statement fails, because the
                package never inspects the database's schema.

        Raises:
            ValueError: If ``table_prefix`` is not a valid identifier prefix — the same rule
                :func:`mount_egress_tables` applies, checked here so a typo fails at construction
                rather than at the first record.
        """
        self._session_factory = session_factory
        # A private, throwaway MetaData, used only to build statements. The table that exists is
        # the host's; this is the identical shape from the same function, and nothing here ever
        # emits DDL against it.
        self._tables = mount_egress_tables(sa.MetaData(), prefix=table_prefix)

    @property
    def tables(self) -> EgressTables:
        """Return the table shape this ledger builds its statements against.

        The host's table is a separate object in the host's own metadata; this is the identical
        shape from :func:`mount_egress_tables`, exposed so a test can assert the prefix matches
        what was mounted.
        """
        return self._tables

    def record(self, decision: EgressDecision) -> None:
        """Persist one decision — approved, denied or a written-after-the-fact violation alike.

        A denial is as durable as an approval (spec §11 contract 1): this method inspects
        ``decision.verdict`` only to project it into an indexed column, never to decide whether to
        write the row.

        Args:
            decision: The decision to persist, already fully formed.

        Raises:
            StoreFailure: If the row could not be written — most commonly a ``decision_id`` this
                ledger has already recorded, which is a caller bug rather than a legitimate retry:
                two decisions cannot share an identity, and an "insert or ignore" here would be an
                update wearing an insert's name.
            UnsupportedDialect: If the session is bound to anything but SQLite or PostgreSQL.
        """
        with self._writing() as session:
            _require_supported_dialect(session)
            try:
                session.execute(sa.insert(self._tables.decisions).values(**_row_values(decision)))
            except SQLAlchemyError as exc:
                raise StoreFailure(
                    f"Could not record decision {decision.decision_id!r}: {exc}",
                    details={"decision_id": decision.decision_id},
                ) from exc

    def decisions(
        self,
        *,
        run_id: str | None = None,
        verdict: Verdict | None = None,
        target: str | None = None,
        since: datetime | None = None,
    ) -> Sequence[EgressDecision]:
        """Return recorded decisions, oldest-decided first, narrowed by whichever filters are given.

        Side-effect-free, and structurally so: the session this opens is rolled back and closed
        rather than committed, so even a future edit that wrote a row here could not persist one.

        Every field on a returned decision is reconstructed from the record's own
        ``decision_json`` through :meth:`~commissioner.types.EgressDecision.from_payload` — the
        same payload round trip spec §11 contract 4 already golden-tests — so nothing here
        reinvents a second way to turn a stored row into a decision.

        Args:
            run_id: Keep only decisions for this run. Pushed into SQL, and indexed.
            verdict: Keep only decisions with this verdict. Pushed into SQL.
            target: Keep only decisions whose request targeted this target name. Pushed into SQL.
            since: Keep only decisions decided at or after this instant. The window is half-open —
                ``decided_at >= since`` — so two consecutive queries with touching bounds return
                each decision exactly once. Pushed into SQL, normalized to UTC first.

        Returns:
            A tuple, oldest-decided first. Filters combine with AND.

        Raises:
            ValueError: If ``since`` is naive. Comparing a naive bound against stored UTC instants
                would silently shift the window by the reader's local offset.
        """
        if since is not None and (since.tzinfo is None or since.tzinfo.utcoffset(since) is None):
            raise ValueError(
                "decisions(since=...) requires a timezone-aware instant; got a naive one."
            )
        table = self._tables.decisions
        query = sa.select(table).order_by(table.c.decided_at.asc(), table.c.decision_id.asc())
        if run_id is not None:
            query = query.where(table.c.run_id == run_id)
        if verdict is not None:
            query = query.where(table.c.verdict == verdict.value)
        if target is not None:
            query = query.where(table.c.target_name == target)
        if since is not None:
            query = query.where(table.c.decided_at >= _as_utc(since))
        with self._reading() as session:
            rows = session.execute(query).mappings().all()
        return tuple(_decision_from_row(row) for row in rows)

    # -- internals -----------------------------------------------------------------------------

    @contextmanager
    def _writing(self) -> Iterator[Session]:
        """Yield a session whose work commits as one transaction, or not at all.

        The unit of work :meth:`record` runs inside. On any exception the transaction is rolled
        back before it propagates, which is what makes a refused write leave no partial row
        behind.
        """
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:  # noqa: BLE001 — re-raised; the rollback is the whole point
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def _reading(self) -> Iterator[Session]:
        """Yield a session that is rolled back on the way out, never committed.

        The mechanical half of this ledger's append-only surface: a read path physically cannot
        leave a row behind, so :meth:`decisions` stays side-effect-free even if a later edit
        forgets why it had to be.
        """
        session = self._session_factory()
        try:
            yield session
        finally:
            session.rollback()
            session.close()


def _require_supported_dialect(session: Session) -> None:
    """Raise :class:`~commissioner.errors.UnsupportedDialect` unless ``session`` is one of the two
    dialects this package supports.

    Every statement this ledger issues — a plain ``INSERT``, a plain ``SELECT`` — happens to be
    ordinary SQL that would run on nearly any dialect SQLAlchemy knows. That is exactly why this
    check exists rather than being skipped as unnecessary: ADR-0006 admits SQLite and PostgreSQL
    and nothing else, and a package that worked by accident on a third, untested dialect because
    its SQL happened to be portable would be half-supporting it. :meth:`SqlEgressLedger.record` is
    the one place this is checked; a read never needs a dialect-specific statement, so
    :meth:`SqlEgressLedger.decisions` does not check it.

    Args:
        session: The session whose bind names the dialect.

    Raises:
        UnsupportedDialect: For anything but SQLite or PostgreSQL.
    """
    name = session.get_bind().dialect.name
    if name not in {"sqlite", "postgresql"}:
        raise UnsupportedDialect(
            f"Commissioner supports SQLite and PostgreSQL; this session is bound to {name!r}. "
            "The suite runs on exactly two dialects (ADR-0006).",
            details={"dialect": name},
        )


def _row_values(decision: EgressDecision) -> dict[str, object]:
    """Build the column values one recorded decision writes.

    ``decision_json`` is the record; the other four columns are a projection of the same facts,
    kept as columns so they can be indexed and filtered
    (``tests/integration/test_sql_ledger.py::test_the_indexed_columns_agree_with_the_canonical_record``
    asserts the two never disagree).
    """
    return {
        "decision_id": decision.decision_id,
        "run_id": decision.request.run_id,
        "verdict": decision.verdict.value,
        "target_name": decision.request.target.name,
        "decided_at": _as_utc(decision.decided_at),
        "decision_json": canonical_dumps(decision.to_payload()),
    }


def _decision_from_row(row: RowMapping) -> EgressDecision:
    """Rebuild one :class:`~commissioner.types.EgressDecision` from its stored row.

    Reads ``decision_json`` alone, through the same
    :meth:`~commissioner.types.EgressDecision.from_payload` a caller reading an exported payload
    uses — never the projected columns beside it, which exist only to be indexed and filtered.
    """
    payload = GovernanceEgressDecisionIn.model_validate(json.loads(row["decision_json"]))
    return EgressDecision.from_payload(payload)


def _as_utc(when: datetime) -> datetime:
    """Return ``when`` as a timezone-aware UTC instant, for binding to a column.

    Normalizing before the bind is what makes the two dialects agree. SQLite has no
    timezone-aware storage: it writes the wall clock of whatever offset it is handed and drops the
    offset, so an instant bound as ``23:30-05:00`` would come back as ``23:30`` on the wrong day.
    It also makes the stored string sort correctly, which is what the ``since`` filter relies on.
    """
    return when.astimezone(UTC)


def _from_utc(when: datetime) -> datetime:
    """Return a stored instant as timezone-aware UTC.

    PostgreSQL hands back an aware value; SQLite hands back a naive one that *is* UTC, because
    :func:`_as_utc` normalized it on the way in. Attaching the timezone here rather than trusting
    the driver is what keeps a column read back comparable to the instant that went in, on both
    dialects. Used only where a test reads ``decided_at`` straight from a row — the ledger's own
    reconstruction in :func:`_decision_from_row` never touches the column at all.
    """
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)
