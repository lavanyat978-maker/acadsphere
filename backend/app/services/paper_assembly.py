"""Paper construction engine.

Candidate questions are not taken in generation order. A deterministic
backtracking search selects questions that jointly satisfy: marks pattern,
topic weights, difficulty distribution, Bloom distribution, section structure and
duplicate avoidance. Distributions are applied to question counts and allow a
10 percentage-point tolerance (enforced again by Level-5 validation).
"""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

MAX_EXPANSIONS = 20000
TOLERANCE = 0.10


def _deterministic_tiebreak(key: str) -> float:
    digest = hashlib.blake2b(key.encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") / 2**32


def target_counts(distribution: dict[str, float], total: int) -> dict[str, int]:
    """Largest-remainder rounding so targets sum exactly to `total`."""
    if not distribution:
        return {}
    raw = {key: total * float(weight) / 100.0 for key, weight in distribution.items()}
    floors = {key: int(value) for key, value in raw.items()}
    remainder = total - sum(floors.values())
    order = sorted(raw, key=lambda key: (raw[key] - floors[key]), reverse=True)
    for key in order[: max(0, remainder)]:
        floors[key] += 1
    return floors


def build_slots(pattern: list[dict]) -> list[dict]:
    slots: list[dict] = []
    for group_index, item in enumerate(pattern):
        for index in range(int(item["count"])):
            slots.append(
                {
                    "slot_index": len(slots),
                    "group_index": group_index,
                    "marks": int(item["marks"]),
                    "question_type": item.get("question_type", "SHORT_ANSWER"),
                    "label": item.get("label", ""),
                }
            )
    # Highest marks first: the hardest slots are placed while the pool is widest.
    slots.sort(key=lambda slot: (-slot["marks"], slot["slot_index"]))
    return slots


def _candidate_score(
    candidate: dict[str, Any],
    slot: dict[str, Any],
    topic_need: Counter,
    difficulty_need: Counter,
    bloom_need: Counter,
) -> float:
    quality = float(candidate.get("quality_score", 0.6))
    score = quality * 1.0
    score += 1.4 * topic_need[candidate["topic_name"]]
    score += 0.9 * difficulty_need[candidate["difficulty"]]
    score += 0.6 * bloom_need[candidate["bloom_level"]]
    if candidate.get("question_type") != slot["question_type"]:
        score -= 0.05
    score += 0.05 * _deterministic_tiebreak(f"{candidate['id']}:{slot['slot_index']}")
    return score


def _text_key(candidate: dict[str, Any]) -> str:
    return " ".join(str(candidate.get("question_text", "")).lower().split())


def _within_tolerance(actual: Counter, targets: dict[str, int], total: int) -> bool:
    for key, target in targets.items():
        share = 100.0 * actual.get(key, 0) / total if total else 0.0
        wanted = 100.0 * target / total if total else 0.0
        if abs(share - wanted) > TOLERANCE * 100 + 0.01:
            return False
    return True


def assemble(
    candidates: list[dict[str, Any]],
    *,
    pattern: list[dict],
    topic_distribution: dict[str, float],
    difficulty_distribution: dict[str, float],
    bloom_distribution: dict[str, float],
    topic_names: dict[str, str],
) -> dict[str, Any] | None:
    slots = build_slots(pattern)
    total_slots = len(slots)
    if not total_slots:
        return None

    topic_targets = {
        topic_names.get(topic_id, topic_id): count
        for topic_id, count in target_counts(topic_distribution, total_slots).items()
    }
    difficulty_targets = target_counts(difficulty_distribution, total_slots)
    bloom_targets = target_counts(bloom_distribution, total_slots)

    by_marks: dict[int, list[dict]] = {}
    for candidate in candidates:
        if candidate.get("validation_status") != "PASSED":
            continue
        by_marks.setdefault(int(candidate["marks"]), []).append(candidate)

    topic_assigned: Counter = Counter()
    difficulty_assigned: Counter = Counter()
    bloom_assigned: Counter = Counter()
    chosen: list[dict] = []
    used_ids: set[str] = set()
    used_text: set[str] = set()
    expansions = 0

    def needs() -> tuple[Counter, Counter, Counter]:
        return (
            Counter({name: max(0, target - topic_assigned[name]) for name, target in topic_targets.items()}),
            Counter(
                {level: max(0, target - difficulty_assigned[level]) for level, target in difficulty_targets.items()}
            ),
            Counter({level: max(0, target - bloom_assigned[level]) for level, target in bloom_targets.items()}),
        )

    def search(position: int) -> bool:
        nonlocal expansions
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return False
        if position == total_slots:
            return (
                _within_tolerance(topic_assigned, topic_targets, total_slots)
                and _within_tolerance(difficulty_assigned, difficulty_targets, total_slots)
                and _within_tolerance(bloom_assigned, bloom_targets, total_slots)
            )
        slot = slots[position]
        pool = by_marks.get(slot["marks"], [])
        topic_need, difficulty_need, bloom_need = needs()
        ranked = sorted(
            (c for c in pool if c["id"] not in used_ids and _text_key(c) not in used_text),
            key=lambda candidate: _candidate_score(candidate, slot, topic_need, difficulty_need, bloom_need),
            reverse=True,
        )
        # Prune obviously unusable candidates: if every remaining slot must fill a
        # deficient topic/difficulty, prefer those first. Explore a bounded fan-out.
        for candidate in ranked[:10]:
            chosen.append(candidate)
            used_ids.add(candidate["id"])
            used_text.add(_text_key(candidate))
            topic_assigned[candidate["topic_name"]] += 1
            difficulty_assigned[candidate["difficulty"]] += 1
            bloom_assigned[candidate["bloom_level"]] += 1
            if search(position + 1):
                return True
            chosen.pop()
            used_ids.discard(candidate["id"])
            used_text.discard(_text_key(candidate))
            topic_assigned[candidate["topic_name"]] -= 1
            difficulty_assigned[candidate["difficulty"]] -= 1
            bloom_assigned[candidate["bloom_level"]] -= 1
        return False

    if not search(0):
        return None

    chosen.sort(key=lambda candidate: (-int(candidate["marks"]), candidate["topic_name"]))
    sections = build_sections(chosen)
    return {
        "questions": chosen,
        "sections": sections,
        "metrics": {
            "topic_counts": dict(topic_assigned),
            "difficulty_counts": dict(difficulty_assigned),
            "bloom_counts": dict(bloom_assigned),
            "total_marks": sum(int(c["marks"]) for c in chosen),
            "question_count": len(chosen),
            "expansions": expansions,
        },
    }


def build_sections(chosen: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for candidate in chosen:
        grouped.setdefault(int(candidate["marks"]), []).append(candidate)
    sections: list[dict] = []
    ordinal = 0
    for marks in sorted(grouped, reverse=True):
        items = grouped[marks]
        ordinal += 1
        label = f"Section {chr(64 + ordinal)}"
        sections.append(
            {
                "label": label,
                "instructions": (
                    f"Answer all {len(items)} question(s). Each question carries {marks} mark(s)."
                ),
                "marks_per_question": marks,
                "question_ids": [item["id"] for item in items],
                "count": len(items),
                "subtotal": marks * len(items),
            }
        )
    return sections


def render_paper_payload(
    *,
    exam: dict[str, Any],
    sections: list[dict],
    questions: list[dict],
    institution: str = "Praxis Institute of Technology",
) -> dict[str, Any]:
    """Canonical plaintext payload that gets encrypted."""
    lookup = {question["id"]: question for question in questions}
    rendered_sections = []
    counter = 0
    for section in sections:
        rendered_questions = []
        for question_id in section["question_ids"]:
            question = lookup[question_id]
            counter += 1
            rendered_questions.append(
                {
                    "number": counter,
                    "question_text": question["question_text"],
                    "marks": int(question["marks"]),
                    "topic": question["topic_name"],
                    "topic_id": question.get("topic_id", ""),
                    "difficulty": question["difficulty"],
                    "bloom_level": question["bloom_level"],
                    "validation_status": question.get("validation_status", "PASSED"),
                    "expected_answer": question.get("expected_answer", ""),
                }
            )
        rendered_sections.append(
            {
                "label": section["label"],
                "instructions": section["instructions"],
                "marks_per_question": section["marks_per_question"],
                "subtotal": section["subtotal"],
                "questions": rendered_questions,
            }
        )
    return {
        "schema": "praxis.paper.v1",
        "institution": institution,
        "exam": exam,
        "sections": rendered_sections,
        "total_marks": sum(section["subtotal"] for section in rendered_sections),
        "question_count": counter,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }
