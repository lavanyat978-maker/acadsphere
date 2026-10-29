"""Versioned prompt templates (prompt_version: v1)."""
from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "v1"

GENERATION_SYSTEM = """You are an examination question author for a university course.
Rules:
1. Write exactly ONE question for the requested slot.
2. Ground the question in the supplied syllabus excerpts. Never invent citations.
3. Match the requested topic, marks, difficulty, Bloom level and question type.
4. The question must be self-contained, unambiguous and answerable on paper.
5. Do not include answers inside the question text. No numbering prefixes.
6. Avoid duplicate or near-duplicate wording of the listed existing questions.
7. Return strict JSON: {"question_text": str, "expected_answer": str, "source_ref": str}
"""

JUDGE_SYSTEM = """You are a strict academic quality reviewer for examination questions.
Assess the candidate question against the syllabus context and metadata.
Return strict JSON with numeric scores from 0 to 1:
{"syllabus_relevance": n, "factual_confidence": n, "clarity": n, "difficulty_match": n,
 "bloom_match": n, "answerability": n, "overall": n, "issues": [str]}
Be conservative. If you cannot verify factual correctness from the context, lower factual_confidence.
"""

PRACTICE_SYSTEM = """You write multiple-choice practice questions for a student practice platform.
Return strict JSON: {"questions": [{"question_text": str, "topic": str, "difficulty": "EASY|MEDIUM|HARD",
"bloom_level": "REMEMBER|UNDERSTAND|APPLY|ANALYZE", "options": [{"id": "a", "text": str}, ...],
"correct_answers": ["a"], "explanation": str, "marks": 1}]}
Every question must have exactly four options and exactly one correct option.
Ground content in the supplied excerpts. Do not include duplicate questions.
"""


def _context_block(context: list[dict[str, Any]]) -> str:
    if not context:
        return "(no syllabus excerpts available - keep the question generic and syllabus-aligned)"
    blocks = []
    for index, chunk in enumerate(context[:8], start=1):
        blocks.append(
            f"[S{index}] source={chunk.get('document_name', 'syllabus')} score={chunk.get('score', 0):.2f}\n"
            f"{chunk.get('content', '')[:1600]}"
        )
    return "\n\n".join(blocks)


def build_generation_messages(spec: dict[str, Any], context: list[dict[str, Any]]) -> tuple[str, str]:
    user = f"""Examination slot:
{json.dumps({k: spec.get(k) for k in (
    'subject_name','topic_name','unit','marks','difficulty','bloom_level','question_type','slot_index'
)}, indent=2)}

Existing question stems (avoid duplicating these):
{json.dumps(spec.get('existing_stems', [])[:12], indent=2)}

Syllabus excerpts:
{_context_block(context)}

Write the question now."""
    return GENERATION_SYSTEM, user


def build_judge_messages(question: dict[str, Any], context_text: str) -> tuple[str, str]:
    user = f"""Candidate question:
{json.dumps({k: question.get(k) for k in ('question_text','marks','difficulty','bloom_level','question_type','topic')}, indent=2)}

Syllabus context:
{context_text[:4000]}

Return the JSON assessment."""
    return JUDGE_SYSTEM, user


def build_practice_messages(
    subject: str,
    topics: list[str],
    count: int,
    difficulty: str,
    context: list[dict[str, Any]],
) -> tuple[str, str]:
    user = f"""Subject: {subject}
Topics: {", ".join(topics) or subject}
Count: {count}
Difficulty: {difficulty}

Excerpts:
{_context_block(context)}

Return exactly {count} questions as JSON."""
    return PRACTICE_SYSTEM, user
