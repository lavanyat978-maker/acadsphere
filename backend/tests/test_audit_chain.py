from __future__ import annotations

from sqlalchemy import select

from app.db.session import session_scope
from app.models.tables import AuditLog, User
from app.services.audit_service import audit_summary, record_audit, verify_chain


def test_audit_chain_verifies_and_detects_tampering(client, seeded):
    with session_scope() as db:
        actor = db.execute(select(User).where(User.email == "teacher@test.demo")).scalar_one()
        record_audit(
            db,
            event="TEST_EVENT_ONE",
            actor=actor,
            resource_type="test",
            resource_id="one",
            metadata={"note": "first"},
        )
        record_audit(
            db,
            event="TEST_EVENT_TWO",
            actor=actor,
            resource_type="test",
            resource_id="two",
            metadata={"note": "second", "password": "must-be-redacted"},
        )
        db.commit()

    with session_scope() as db:
        report = verify_chain(db)
        assert report["verified"] is True
        assert report["records_checked"] >= 2

    with session_scope() as db:
        target = db.execute(
            select(AuditLog).where(AuditLog.event == "TEST_EVENT_ONE").order_by(AuditLog.seq.desc())
        ).scalars().first()
        assert target is not None
        original_event = target.event
        target.event = "TAMPERED_EVENT"
        db.commit()
        target_seq = target.seq

    with session_scope() as db:
        report = verify_chain(db)
        assert report["verified"] is False
        assert report["broken_at_seq"] == target_seq

    with session_scope() as db:
        target = db.get(AuditLog, target_seq)
        target.event = original_event
        db.commit()

    with session_scope() as db:
        assert verify_chain(db)["verified"] is True


def test_audit_metadata_redacts_secrets(client, seeded):
    with session_scope() as db:
        actor = db.execute(select(User).where(User.email == "teacher@test.demo")).scalar_one()
        record_audit(
            db,
            event="TEST_SECRET_REDACTION",
            actor=actor,
            resource_type="test",
            metadata={"password": "secret", "release_token": "tok", "safe": "visible"},
        )
        db.commit()
        row = db.execute(
            select(AuditLog).where(AuditLog.event == "TEST_SECRET_REDACTION")
        ).scalars().first()
        assert row is not None
        assert row.metadata_json["password"] == "[redacted]"
        assert row.metadata_json["release_token"] == "[redacted]"
        assert row.metadata_json["safe"] == "visible"
        assert "secret" not in str(row.metadata_json)


def test_audit_summary_counts_events(client, seeded):
    with session_scope() as db:
        summary = audit_summary(db, days=30)
        assert summary["total_events"] >= 2
        assert isinstance(summary["top_events"], list)
