from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.models import AuditEvent, ReviewDecision
from app.storage import AuditStore


def test_store_initializes_an_empty_database(tmp_path):
    db_path = tmp_path / "nested" / "audit.db"

    store = AuditStore(db_path)

    assert db_path.exists()
    assert store.list_events() == []


def test_record_and_list_events_newest_first(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    older = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)

    first = store.append(
        AuditEvent(
            case_id="case-1",
            field="capital_call_amount",
            decision=ReviewDecision.APPROVED,
            note="Checked against notice",
            created_at=older,
        )
    )
    second = store.record_decision(
        "case-1",
        "due_date",
        "needs investigation",
        created_at=newer,
    )

    events = store.list_events()
    assert [event.id for event in events] == [second.id, first.id]
    assert events[0].decision is ReviewDecision.NEEDS_INVESTIGATION
    assert events[1].note == "Checked against notice"


def test_latest_decision_is_scoped_to_case_and_field(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    first_time = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)

    store.record_decision(
        "case-1", "due_date", "REJECTED", created_at=first_time
    )
    expected = store.record_decision(
        "case-1", "due_date", "APPROVED", created_at=second_time
    )
    store.record_decision(
        "case-2", "due_date", "NEEDS_INVESTIGATION", created_at=second_time
    )

    latest = store.latest_decision("case-1", "due_date")
    assert latest == expected
    assert store.latest_decision("case-1", "currency") is None


def test_database_rejects_update_and_delete(tmp_path):
    db_path = tmp_path / "audit.db"
    store = AuditStore(db_path)
    event = store.record_decision("case-1", "due_date", "REJECTED")

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE audit_events SET decision = 'APPROVED' WHERE id = ?",
                (event.id,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_events WHERE id = ?", (event.id,))

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_events
                    (id, case_id, document_id, field, decision, created_at, actor)
                VALUES (?, 'case-1', 'unspecified', 'due_date', 'APPROVED', ?, 'attacker')
                """,
                (event.id, datetime.now(timezone.utc).isoformat()),
            )

    assert [entry.id for entry in store.list_events()] == [event.id]


def test_invalid_decision_is_not_written(tmp_path):
    store = AuditStore(tmp_path / "audit.db")

    with pytest.raises(ValueError, match="decision must be one of"):
        store.record_decision("case-1", "due_date", "MAYBE")

    assert store.list_events() == []


def test_list_events_honors_limit(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    store.record_decision("case-1", "due_date", "APPROVED")
    expected = store.record_decision("case-1", "currency", "REJECTED")

    assert store.list_events(limit=1) == [expected]

    with pytest.raises(ValueError, match="positive integer"):
        store.list_events(limit=0)


def test_document_scoping_prevents_cross_document_decision_leaks(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    first = store.record_decision(
        "case-1",
        "due_date",
        "APPROVED",
        document_id="sha256:first",
        source_document="first.pdf",
    )
    store.record_decision(
        "case-1",
        "due_date",
        "REJECTED",
        document_id="sha256:second",
        source_document="second.pdf",
    )

    assert store.list_events(
        case_id="case-1", document_id="sha256:first"
    ) == [first]
    assert store.latest_decision(
        "case-1", "due_date", document_id="sha256:first"
    ) == first


def test_existing_audit_database_is_migrated_without_losing_events(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                field TEXT NOT NULL,
                decision TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT,
                actor TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (case_id, field, decision, created_at, note, actor)
            VALUES ('case-1', 'due_date', 'APPROVED', ?, 'legacy', 'Reviewer')
            """,
            (datetime(2026, 9, 4, 9, 0).isoformat(),),
        )

    events = AuditStore(db_path).list_events()

    assert len(events) == 1
    assert events[0].document_id == "unspecified"
    assert events[0].source_document is None
    assert events[0].created_at.tzinfo is timezone.utc
