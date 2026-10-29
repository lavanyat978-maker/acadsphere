from __future__ import annotations

import pytest

from app.core.config import settings
from app.encryption.service import (
    KeyManagementError,
    decrypt_paper,
    encrypt_paper,
    paper_aad,
)
from app.validation.pipeline import (
    normalize_for_duplicate,
    validate_language,
    validate_paper,
    validate_rules,
)

PAYLOAD = {
    "schema": "praxis.paper.v1",
    "exam": {"title": "Encryption Test"},
    "sections": [
        {
            "label": "Section A",
            "questions": [
                {
                    "question_text": "Explain how write-ahead logging supports durability.",
                    "marks": 5,
                    "topic": "Transactions",
                    "topic_id": "t1",
                    "difficulty": "MEDIUM",
                    "bloom_level": "UNDERSTAND",
                    "validation_status": "PASSED",
                }
            ],
        }
    ],
}


def _roundtrip_result():
    return encrypt_paper(paper_id="paper-1", exam_id="exam-1", version=1, payload=PAYLOAD)


def test_aes_gcm_roundtrip_and_hashes():
    result = _roundtrip_result()
    assert result.algorithm == "AES-256-GCM"
    assert result.key_id == settings.MASTER_KEY_ID
    assert result.ciphertext.startswith(b"") and len(result.ciphertext) > 32
    assert result.plaintext_sha256 != result.ciphertext_sha256
    assert result.aad == paper_aad("paper-1", 1, "exam-1").decode()

    payload, verified = decrypt_paper(
        paper_id="paper-1",
        exam_id="exam-1",
        version=1,
        ciphertext=result.ciphertext,
        key_id=result.key_id,
        wrapped_dek=result.wrapped_dek,
        payload_nonce=result.payload_nonce,
        expected_sha256=result.ciphertext_sha256,
    )
    assert verified is True
    assert payload == PAYLOAD


def test_ciphertext_tamper_is_rejected():
    result = _roundtrip_result()
    tampered = bytearray(result.ciphertext)
    tampered[-1] ^= 0x01
    with pytest.raises(KeyManagementError):
        decrypt_paper(
            paper_id="paper-1",
            exam_id="exam-1",
            version=1,
            ciphertext=bytes(tampered),
            key_id=result.key_id,
            wrapped_dek=result.wrapped_dek,
            payload_nonce=result.payload_nonce,
            expected_sha256=result.ciphertext_sha256,
        )


def test_ciphertext_is_bound_to_paper_and_exam():
    result = _roundtrip_result()
    with pytest.raises(Exception):
        decrypt_paper(
            paper_id="paper-1",
            exam_id="exam-2",
            version=1,
            ciphertext=result.ciphertext,
            key_id=result.key_id,
            wrapped_dek=result.wrapped_dek,
            payload_nonce=result.payload_nonce,
            expected_sha256=result.ciphertext_sha256,
        )
    with pytest.raises(Exception):
        decrypt_paper(
            paper_id="paper-2",
            exam_id="exam-1",
            version=1,
            ciphertext=result.ciphertext,
            key_id=result.key_id,
            wrapped_dek=result.wrapped_dek,
            payload_nonce=result.payload_nonce,
            expected_sha256=result.ciphertext_sha256,
        )


def test_each_encryption_uses_fresh_key_and_nonce():
    first = _roundtrip_result()
    second = _roundtrip_result()
    assert first.wrapped_dek != second.wrapped_dek
    assert first.payload_nonce != second.payload_nonce
    assert first.ciphertext != second.ciphertext


def test_rules_validator_accepts_a_good_question():
    result = validate_rules(
        {
            "question_text": "Explain how write-ahead logging supports durability after a crash.",
            "marks": 5,
            "difficulty": "MEDIUM",
            "bloom_level": "UNDERSTAND",
            "question_type": "SHORT_ANSWER",
            "topic": "Transactions",
            "expected_answer": "Undo/redo records are flushed before commit.",
        },
        {"marks": 5, "difficulty": "MEDIUM", "bloom_level": "UNDERSTAND", "topic_name": "Transactions"},
    )
    assert result.passed, result.issues
    assert result.score > 0.9


def test_rules_validator_rejects_mismatched_slot_and_bad_format():
    result = validate_rules(
        {
            "question_text": "Define DBMS.",
            "marks": 10,
            "difficulty": "EASY",
            "bloom_level": "REMEMBER",
            "question_type": "SHORT_ANSWER",
            "topic": "Scheduling",
        },
        {"marks": 5, "difficulty": "HARD", "bloom_level": "ANALYZE", "topic_name": "Transactions"},
    )
    assert not result.passed
    codes = {issue.code for issue in result.issues}
    assert {"MARKS_MISMATCH", "DIFFICULTY_MISMATCH", "BLOOM_MISMATCH", "TOPIC_MISMATCH"} <= codes


def test_language_validator_keeps_technical_terms():
    result = validate_language(
        {
            "question_text": "Explain BCNF and 3NF decomposition with a lossless join guarantee.",
            "marks": 5,
        }
    )
    assert not any(issue.code == "SPELLING" for issue in result.issues)


def test_paper_validator_detects_distribution_and_duplicate_failures():
    questions = [
        {
            "question_text": "Explain normalization and dependency preservation carefully.",
            "marks": 5,
            "topic": "Normalization",
            "topic_id": "t1",
            "difficulty": "EASY",
            "bloom_level": "REMEMBER",
            "validation_status": "PASSED",
        },
        {
            "question_text": "Explain normalization and dependency preservation carefully.",
            "marks": 5,
            "topic": "Normalization",
            "topic_id": "t1",
            "difficulty": "EASY",
            "bloom_level": "REMEMBER",
            "validation_status": "PASSED",
        },
    ]
    paper = {
        "questions": questions,
        "sections": [{"label": "Section A", "questions": questions}],
    }
    config = {
        "total_marks": 20,
        "pattern": [{"marks": 5, "count": 4}],
        "difficulty_distribution": {"EASY": 25, "MEDIUM": 50, "HARD": 25},
        "bloom_distribution": {"REMEMBER": 25, "UNDERSTAND": 25, "APPLY": 25, "ANALYZE": 25},
        "topic_distribution": {"t1": 50, "t2": 50},
        "topic_names": {"t1": "Normalization", "t2": "Transactions"},
    }
    result = validate_paper(paper, config)
    assert result["passed"] is False
    codes = {issue["code"] for issue in result["issues"]}
    assert "TOTAL_MARKS" in codes
    assert "PAPER_DUPLICATE" in codes
    assert "TOPIC_DISTRIBUTION" in codes


def test_duplicate_normalization_ignores_punctuation():
    assert normalize_for_duplicate("Explain B+ tree indexing!") == normalize_for_duplicate(
        "explain b+ tree indexing"
    )
