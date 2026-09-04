"""SQLite persistence for human review decisions.

The audit log is deliberately append-only: callers can record a decision and read
history, but there is no update/delete API. Database triggers enforce that rule if
the SQLite file is accessed directly.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.models import AuditEvent, ReviewDecision


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fundops.db"
VALID_DECISIONS = frozenset({"APPROVED", "NEEDS_INVESTIGATION", "REJECTED"})


class AuditStore:
    """Small connection-per-operation SQLite audit store."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL CHECK (length(trim(case_id)) > 0),
                    document_id TEXT NOT NULL DEFAULT 'unspecified'
                        CHECK (length(trim(document_id)) > 0),
                    source_document TEXT,
                    source_location TEXT,
                    field TEXT NOT NULL CHECK (length(trim(field)) > 0),
                    expected_value TEXT,
                    observed_value TEXT,
                    difference TEXT,
                    reviewer_status TEXT,
                    decision TEXT NOT NULL CHECK (
                        decision IN ('APPROVED', 'NEEDS_INVESTIGATION', 'REJECTED')
                    ),
                    created_at TEXT NOT NULL,
                    note TEXT,
                    actor TEXT NOT NULL CHECK (length(trim(actor)) > 0)
                );

                CREATE TRIGGER IF NOT EXISTS audit_events_prevent_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_prevent_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_prevent_replace
                BEFORE INSERT ON audit_events
                WHEN NEW.id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM audit_events WHERE id = NEW.id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(audit_events)")
            }
            if "document_id" not in columns:
                connection.execute(
                    "ALTER TABLE audit_events "
                    "ADD COLUMN document_id TEXT NOT NULL DEFAULT 'unspecified'"
                )
            if "source_document" not in columns:
                connection.execute(
                    "ALTER TABLE audit_events ADD COLUMN source_document TEXT"
                )
            for column in (
                "source_location",
                "expected_value",
                "observed_value",
                "difference",
                "reviewer_status",
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE audit_events ADD COLUMN {column} TEXT"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_events_document_field_time
                    ON audit_events (
                        case_id, document_id, field, created_at DESC, id DESC
                    )
                """
            )

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append an explicit user-created event and return it with its database id."""

        event = AuditEvent.model_validate(event)
        clean_case_id = _required_text(event.case_id, "case_id")
        clean_document_id = _required_text(event.document_id, "document_id")
        clean_field = _required_text(event.field, "field")
        clean_actor = _required_text(event.actor, "actor")
        clean_decision = _normalize_decision(event.decision)
        clean_note = event.note.strip() if event.note and event.note.strip() else None
        event_time = _as_utc(event.created_at)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_events
                    (
                        case_id, document_id, source_document, source_location,
                        field, expected_value, observed_value, difference,
                        reviewer_status, decision, created_at, note, actor
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_case_id,
                    clean_document_id,
                    event.source_document,
                    event.source_location,
                    clean_field,
                    event.expected_value,
                    event.observed_value,
                    event.difference,
                    event.reviewer_status,
                    clean_decision,
                    event_time.isoformat(),
                    clean_note,
                    clean_actor,
                ),
            )
            event_id = int(cursor.lastrowid)

        return AuditEvent(
            id=event_id,
            case_id=clean_case_id,
            document_id=clean_document_id,
            source_document=event.source_document,
            source_location=event.source_location,
            field=clean_field,
            expected_value=event.expected_value,
            observed_value=event.observed_value,
            difference=event.difference,
            reviewer_status=event.reviewer_status,
            decision=ReviewDecision(clean_decision),
            created_at=event_time,
            note=clean_note,
            actor=clean_actor,
        )

    def record_decision(
        self,
        case_id: str,
        field: str,
        decision: str | Enum,
        *,
        document_id: str = "unspecified",
        source_document: str | None = None,
        source_location: str | None = None,
        expected_value: str | None = None,
        observed_value: str | None = None,
        difference: str | None = None,
        reviewer_status: str | None = None,
        note: str | None = None,
        actor: str = "Reviewer",
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Convenience method used by UI button handlers."""

        return self.append(
            AuditEvent(
                case_id=case_id,
                document_id=document_id,
                source_document=source_document,
                source_location=source_location,
                field=field,
                expected_value=expected_value,
                observed_value=observed_value,
                difference=difference,
                reviewer_status=reviewer_status,
                decision=ReviewDecision(_normalize_decision(decision)),
                note=note,
                actor=actor,
                created_at=created_at or datetime.now(timezone.utc),
            )
        )

    def list_events(
        self,
        limit: int = 200,
        *,
        case_id: str | None = None,
        document_id: str | None = None,
    ) -> list[AuditEvent]:
        """List audit history newest-first, optionally scoped to a document."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = "SELECT * FROM audit_events"
        clauses: list[str] = []
        values: list[str | int] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            values.append(_required_text(case_id, "case_id"))
        if document_id is not None:
            clauses.append("document_id = ?")
            values.append(_required_text(document_id, "document_id"))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        values.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [_row_to_entry(row) for row in rows]

    def latest_decision(
        self,
        case_id: str,
        field: str,
        *,
        document_id: str | None = None,
    ) -> AuditEvent | None:
        """Return the newest decision for a case/field pair, if one exists."""

        clauses = ["case_id = ?", "field = ?"]
        values = [
            _required_text(case_id, "case_id"),
            _required_text(field, "field"),
        ]
        if document_id is not None:
            clauses.append("document_id = ?")
            values.append(_required_text(document_id, "document_id"))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT 1",
                tuple(values),
            ).fetchone()
        return _row_to_entry(row) if row is not None else None


def record_decision(
    case_id: str,
    field: str,
    decision: str | Enum,
    *,
    document_id: str = "unspecified",
    source_document: str | None = None,
    source_location: str | None = None,
    expected_value: str | None = None,
    observed_value: str | None = None,
    difference: str | None = None,
    reviewer_status: str | None = None,
    note: str | None = None,
    actor: str = "Reviewer",
    created_at: datetime | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> AuditEvent:
    """Convenience wrapper for recording a decision with the default store."""

    return AuditStore(db_path).record_decision(
        case_id,
        field,
        decision,
        document_id=document_id,
        source_document=source_document,
        source_location=source_location,
        expected_value=expected_value,
        observed_value=observed_value,
        difference=difference,
        reviewer_status=reviewer_status,
        note=note,
        actor=actor,
        created_at=created_at,
    )


def list_audit_events(
    *,
    case_id: str | None = None,
    document_id: str | None = None,
    limit: int = 200,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[AuditEvent]:
    """Convenience wrapper returning audit events newest-first."""

    return AuditStore(db_path).list_events(
        limit=limit,
        case_id=case_id,
        document_id=document_id,
    )


def get_latest_decision(
    case_id: str,
    field: str,
    *,
    document_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> AuditEvent | None:
    """Convenience wrapper returning the latest decision for a case/field."""

    return AuditStore(db_path).latest_decision(
        case_id,
        field,
        document_id=document_id,
    )


def _normalize_decision(decision: str | Enum) -> str:
    raw_value = decision.value if isinstance(decision, Enum) else decision
    normalized = str(raw_value).strip().upper().replace("-", "_").replace(" ", "_")
    if normalized not in VALID_DECISIONS:
        choices = ", ".join(sorted(VALID_DECISIONS))
        raise ValueError(f"decision must be one of: {choices}")
    return normalized


def _required_text(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_to_entry(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=int(row["id"]),
        case_id=str(row["case_id"]),
        document_id=str(row["document_id"]),
        source_document=(
            str(row["source_document"])
            if row["source_document"] is not None
            else None
        ),
        source_location=(
            str(row["source_location"])
            if row["source_location"] is not None
            else None
        ),
        field=str(row["field"]),
        expected_value=(
            str(row["expected_value"])
            if row["expected_value"] is not None
            else None
        ),
        observed_value=(
            str(row["observed_value"])
            if row["observed_value"] is not None
            else None
        ),
        difference=(
            str(row["difference"])
            if row["difference"] is not None
            else None
        ),
        reviewer_status=(
            str(row["reviewer_status"])
            if row["reviewer_status"] is not None
            else None
        ),
        decision=ReviewDecision(str(row["decision"])),
        created_at=_as_utc(datetime.fromisoformat(str(row["created_at"]))),
        note=str(row["note"]) if row["note"] is not None else None,
        actor=str(row["actor"]),
    )
