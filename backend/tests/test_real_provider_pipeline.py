"""Exam generation through OpenAIProvider with model replies shaped like a real LLM's.

Every other generation test uses the demo provider, which returns Python lists. A real model
returns JSON text, and the exam prompt asks for one bare question object. This drives the same
pipeline (generate, validate, judge, assemble, encrypt) with only the HTTP call replaced.
"""
from __future__ import annotations

import json
import re

import pytest

from app.ai import embedder as embedder_module
from app.ai.prompts import GENERATION_SYSTEM, JUDGE_SYSTEM
from app.ai.provider import LLMError, OpenAIProvider, _question_items, set_provider
from app.core.config import settings
from app.services.generation_service import run_generation
from tests.conftest import exam_payload


def test_question_items_accepts_every_shape_a_model_uses():
    one = {"question_text": "What is atomicity?"}
    assert _question_items(one, "q") == [one]
    assert _question_items({"questions": [one, one]}, "q") == [one, one]
    assert _question_items([one, "junk"], "q") == [one]
    with pytest.raises(LLMError):
        _question_items({"unexpected": 1}, "q")
    with pytest.raises(LLMError):
        _question_items("text", "q")


def _fake_chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
    if system == GENERATION_SYSTEM:
        slot = json.loads(user.split("Examination slot:\n", 1)[1].split("\n\nExisting", 1)[0])
        excerpts = user.split("Syllabus excerpts:\n", 1)[1]
        body = re.sub(r"\[S\d+\][^\n]*\n", "", excerpts)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if 45 <= len(s.strip()) <= 200]
        sentence = sentences[slot["slot_index"] % len(sentences)] if sentences else slot["topic_name"]
        text = (
            f"Discuss the statement \"{sentence.rstrip('.')}\" in the context of {slot['topic_name']}, "
            f"and justify your answer with one example."
        )
        # A real model returns the single object the prompt asks for, as JSON text.
        return json.dumps({"question_text": text, "expected_answer": sentence, "source_ref": "syllabus"})
    if system == JUDGE_SYSTEM:
        return json.dumps(
            {
                "syllabus_relevance": 0.9, "factual_confidence": 0.9, "clarity": 0.9,
                "difficulty_match": 0.85, "bloom_match": 0.85, "answerability": 0.9,
                "overall": 0.88, "issues": [],
            }
        )
    raise AssertionError("unexpected prompt")


def test_exam_generation_works_with_a_real_style_provider(client, tokens, seeded, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
    embedder_module.reset_embedder()
    monkeypatch.setattr(OpenAIProvider, "_chat", _fake_chat)
    set_provider(OpenAIProvider())
    try:
        response = client.post(
            "/api/exams", headers=tokens["teacher"], json=exam_payload(seeded["subject_id"], seeded["topic_ids"])
        )
        assert response.status_code == 201, response.text
        exam_id = response.json()["id"]
        assert client.post(f"/api/exams/{exam_id}/submit", headers=tokens["teacher"]).status_code == 200
        queued = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["teacher"])
        assert queued.status_code == 200 and queued.json()["mode"] == "REAL_AI"
        run_generation(queued.json()["request_id"])

        detail = client.get(f"/api/exams/{exam_id}", headers=tokens["teacher"]).json()
        generation = client.get(f"/api/exams/{exam_id}/generation", headers=tokens["teacher"]).json()
        assert detail["status"] == "SECURED", generation
        assert detail["paper_state"] == "STORED"
    finally:
        set_provider(None)
        embedder_module.reset_embedder()
