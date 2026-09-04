"""The ledger's append-only surface, asserted structurally rather than left to code review.

Development plan's named failure mode for this phase: "a query path added for a UI that quietly
becomes an update path." A public method that should not exist is easy to miss in review and easy
to catch here — the way ToolYard's `test_boundaries.py` asserts "one `Popen`" rather than trusting
a description of where subprocesses are allowed to start.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import commissioner.ledger
import commissioner.sql
from commissioner.ledger import EgressLedger, InMemoryEgressLedger
from commissioner.sql import SqlEgressLedger

_ALLOWED_PUBLIC_METHODS = {"record", "decisions"}

_FORBIDDEN_NAMES = {"update", "delete"}
"""Neither this package's protocol nor either implementation offers these, by name or by SQL."""


def _public_surface(cls: type) -> set[str]:
    """Every public attribute ``cls`` declares — methods and properties alike.

    A plain ``callable(...)`` filter would miss a property: ``getattr(SomeClass, "a_property")``
    on the class itself returns the descriptor, which is not callable, even though it is exactly
    the kind of public surface this test cares about.
    """
    return {name for name in dir(cls) if not name.startswith("_")}


def _forbidden_identifiers_in(module: ModuleType) -> set[str]:
    """Every occurrence of a name in :data:`_FORBIDDEN_NAMES` as an identifier in ``module``'s AST.

    Exact identifier matches only (``ast.Name``/``ast.Attribute``), never a text search — a
    docstring mentioning "no update path" must not itself trip this check, and a real
    ``sa.update(...)`` or ``session.execute(sa.delete(...))`` call would.
    """
    source_path = Path(str(module.__file__))
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            found.add(f"{source_path.name}:{node.attr}")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            found.add(f"{source_path.name}:{node.id}")
    return found


class TestPublicSurface:
    def test_the_protocol_declares_exactly_record_and_decisions(self) -> None:
        assert _public_surface(EgressLedger) == _ALLOWED_PUBLIC_METHODS

    def test_in_memory_ledger_exposes_no_mutation_beyond_record(self) -> None:
        assert _public_surface(InMemoryEgressLedger) == _ALLOWED_PUBLIC_METHODS

    def test_sql_ledger_exposes_no_mutation_beyond_record(self) -> None:
        # `tables` is a read-only property naming the mounted shape, not a mutation path.
        assert _public_surface(SqlEgressLedger) == _ALLOWED_PUBLIC_METHODS | {"tables"}


class TestNoMutationStatementIsEverConstructed:
    def test_sql_py_never_references_update_or_delete(self) -> None:
        assert _forbidden_identifiers_in(commissioner.sql) == set()

    def test_ledger_py_never_references_update_or_delete(self) -> None:
        assert _forbidden_identifiers_in(commissioner.ledger) == set()
