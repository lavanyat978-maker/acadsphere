"""Multi-stage question quality validation.

Level 1 rules -> Level 2 language -> Level 3 semantic (LLM judge)
-> Level 4 duplicate detection -> Level 5 whole-paper validation.

Issue messages never contain question text so that validation telemetry can be
shown to teachers without leaking candidate content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from spellchecker import SpellChecker

from app.ai.embedder import cosine, get_embedder
from app.core.config import settings
from app.models.enums import BloomLevel, Difficulty, QuestionType

PROHIBITED_PATTERNS = [
    r"\bas an ai\b",
    r"\blorem ipsum\b",
    r"\bignore (the )?(previous|above) instructions\b",
    r"\{\{.*?\}\}",
    r"\[insert",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bquestion\s*\d+\s*[:.)]",
    r"https?://",
    r"\b(?:kill|bomb|terrorist attack)\b",
]

ACADEMIC_DICTIONARY = {
    # Program / exam language
    "syllabus", "rubric", "rubrics", "marks", "marking", "question", "questions",
    "examination", "exam", "assessment", "semester", "module", "unit", "units",
    "bloom", "taxonomy", "subtopic", "subtopics", "prerequisite", "prerequisites",
    "coursework", "viva", "invigilator", "proctor", "proctored", "handwritten",
    # Computer science / DBMS
    "dbms", "database", "databases", "schema", "schemas", "normalization",
    "denormalization", "normalized", "denormalized", "bcnf", "3nf", "2nf", "1nf",
    "lossless", "lossy", "decomposition", "determinant", "determinants", "superkey",
    "4nf", "5nf", "acid", "transaction", "transactions", "transact", "rollback",
    "rollbacks", "commit", "commits", "atomicity", "consistency", "durability",
    "isolation", "serializability", "deadlock", "deadlocks", "index", "indexes",
    "indices", "primary", "foreign", "tuple", "tuples", "attribute", "attributes",
    "relation", "relations", "relational", "cardinality", "entity", "entities",
    "sql", "nosql", "postgresql", "mysql", "sqlite", "mongodb", "redis", "kafka",
    "er", "erd", "ddl", "dml", "dcl", "tcl", "view", "views", "join", "joins",
    "query", "queries", "trigger", "triggers", "stored", "procedure", "procedures",
    # Networks / security
    "tcp", "udp", "ip", "http", "https", "dns", "dhcp", "lan", "wan", "tls", "ssl",
    "oauth", "openid", "jwt", "csrf", "xss", "sql injection", "firewall", "vpn",
    "aes", "rsa", "sha", "gcm", "cipher", "ciphertext", "plaintext", "encryption",
    "decryption", "authentication", "authorization", "rbac", "mfa", "totp", "hmac",
    # OS / systems
    "kernel", "syscall", "syscalls", "paging", "paged", "thrashing", "mutex",
    "semaphore", "semaphores", "deadlocked", "preemptive", "preemption", "virtual",
    "hypervisor", "container", "containers", "docker", "kubernetes", "linux",
    # Programming / SE
    "polymorphism", "inheritance", "encapsulation", "abstraction", "interface",
    "interfaces", "api", "apis", "rest", "restful", "microservice", "microservices",
    "monolith", "refactor", "refactoring", "asynchronous", "async", "await",
    "runtime", "compiler", "interpreter", "garbage", "recursion", "recursive",
    "algorithm", "algorithms", "complexity", "big-o", "heuristic", "deterministic",
    "idempotent", "idempotency", "serialization", "deserialization", "payload",
    "payloads", "middleware", "endpoint", "endpoints", "webhook", "webhooks",
    # ML / data
    "machine", "learning", "tensorflow", "pytorch", "numpy", "pandas", "sklearn",
    "embedding", "embeddings", "vector", "vectors", "rag", "llm", "llms", "nlp",
    "classifier", "regression", "logistic", "clustering", "overfitting",
    "underfitting", "regularization", "gradient", "hyperparameter", "epoch",
    "epochs", "dataset", "datasets", "feature", "features", "inference",
    "precision", "recall", "f1", "roc", "auc", "transformer", "transformers",
    "attention", "tokenization", "tokenizer", "prompt", "prompts", "hallucination",
    # General academic verbs/nouns
    "analyse", "analyze", "analyse", "evaluate", "justify", "illustrate",
    "describe", "discuss", "compare", "contrast", "demonstrate", "derive",
    "implement", "design", "explain", "compute", "calculate", "identify",
    "critically", "suitable", "trade-offs", "tradeoff", "tradeoffs", "scenario",
    "scenarios", "stakeholder", "stakeholders", "requirement", "requirements",
    "constraint", "constraints", "behaviour", "behavior", "modelling", "modeling",
    "optimisation", "optimization", "minimisation", "maximisation", "utilise",
    "utilize", "summarise", "summarize", "characterise", "characterize",
    "e-g", "i-e", "etc", "vs", "per", "via", "among", "within", "across",
}


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "error"  # error | warning

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class LevelResult:
    level: int
    validator: str
    passed: bool
    score: float
    issues: list[Issue] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "validator": self.validator,
            "passed": self.passed,
            "score": round(self.score, 3),
            "issues": [issue.as_dict() for issue in self.issues],
            "detail": self.detail,
        }


@dataclass
class ValidationReport:
    passed: bool
    quality_score: float
    levels: list[LevelResult]
    embedding: list[float]
    issues: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "quality_score": round(self.quality_score, 3),
            "levels": [level.as_dict() for level in self.levels],
            "issue_codes": self.issues,
        }


_spell = SpellChecker()
_spell.word_frequency.load_words(ACADEMIC_DICTIONARY)
_PLACEHOLDER_VOWEL = re.compile(r"[aeiouy]", re.I)


def normalize_for_duplicate(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(word for word in text.split() if len(word) > 2)


def _looks_technical(token: str) -> bool:
    if any(char.isdigit() for char in token):
        return True
    if token.isupper() and len(token) >= 2:
        return True
    if re.search(r"[A-Z].*[A-Z]", token):
        return True
    if re.search(r"[a-z][A-Z]", token) or re.search(r"[A-Z][a-z]+[A-Z]", token):
        return True
    if token.endswith(("()", "[]")) or "." in token or "_" in token or "/" in token:
        return True
    return False


def validate_rules(question: dict[str, Any], spec: dict[str, Any]) -> LevelResult:
    issues: list[Issue] = []
    text = str(question.get("question_text") or "").strip()
    if not text:
        issues.append(Issue("EMPTY_TEXT", "Question text is empty."))
    if len(text) < 15:
        issues.append(Issue("TEXT_TOO_SHORT", "Question text is too short to be answerable."))
    if len(text) > 1500:
        issues.append(Issue("TEXT_TOO_LONG", "Question text exceeds the 1500 character limit."))

    try:
        difficulty = Difficulty(question.get("difficulty"))
    except ValueError:
        issues.append(Issue("INVALID_DIFFICULTY", "Difficulty is not a recognised level."))
        difficulty = None
    try:
        bloom = BloomLevel(question.get("bloom_level"))
    except ValueError:
        issues.append(Issue("INVALID_BLOOM", "Bloom level is not recognised."))
        bloom = None
    try:
        qtype = QuestionType(question.get("question_type", QuestionType.SHORT_ANSWER))
    except ValueError:
        issues.append(Issue("INVALID_QUESTION_TYPE", "Question type is not recognised."))
        qtype = None

    marks = question.get("marks")
    if not isinstance(marks, int) or marks <= 0:
        issues.append(Issue("INVALID_MARKS", "Marks must be a positive integer."))
    elif spec.get("marks") and marks != spec["marks"]:
        issues.append(Issue("MARKS_MISMATCH", "Question marks do not match the requested slot."))

    if difficulty and spec.get("difficulty") and difficulty != spec["difficulty"]:
        issues.append(Issue("DIFFICULTY_MISMATCH", "Difficulty does not match the requested slot."))
    if bloom and spec.get("bloom_level") and bloom != spec["bloom_level"]:
        issues.append(Issue("BLOOM_MISMATCH", "Bloom level does not match the requested slot."))

    if spec.get("topic_name"):
        claimed = str(question.get("topic") or "").strip().lower()
        if claimed and claimed != spec["topic_name"].strip().lower():
            issues.append(Issue("TOPIC_MISMATCH", "Question topic does not match the selected topic."))

    if not str(question.get("expected_answer") or "").strip():
        issues.append(
            Issue("MISSING_EXPECTED_ANSWER", "A marking-guideline answer is required.", "warning")
        )

    lowered = text.lower()
    for pattern in PROHIBITED_PATTERNS:
        if re.search(pattern, lowered):
            issues.append(Issue("PROHIBITED_CONTENT", "Question contains prohibited content."))
            break

    if not text.rstrip().endswith((".", "?", ":")):
        issues.append(Issue("FORMATTING_TERMINAL", "Question must end with proper punctuation."))
    if "  " in text:
        issues.append(Issue("FORMATTING_SPACING", "Question contains repeated spaces."))
    if text.count("?") > 1:
        issues.append(Issue("MULTIPLE_QUESTIONS", "A question stem may contain only one question mark."))
    if text.isupper() and len(text) > 20:
        issues.append(Issue("SHOUTING_TEXT", "Question appears to be written in all capitals."))
    if re.search(r"\bthe the\b|\ba a\b|\bof of\b", lowered):
        issues.append(Issue("REPEATED_WORD", "Question contains a repeated word."))
    if qtype in {QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER} and marks:
        if marks >= 5 and len(text) < 30:
            issues.append(Issue("INSUFFICIENT_PROMPT", "High-mark questions need a fuller prompt."))

    errors = [issue for issue in issues if issue.severity == "error"]
    score = max(0.0, 1.0 - 0.25 * len(errors) - 0.05 * (len(issues) - len(errors)))
    return LevelResult(
        level=1,
        validator="rules",
        passed=not errors,
        score=score,
        issues=issues,
        detail={"checks": 14, "errors": len(errors), "warnings": len(issues) - len(errors)},
    )


def validate_language(question: dict[str, Any]) -> LevelResult:
    text = str(question.get("question_text") or "")
    issues: list[Issue] = []
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", text)
    candidates: list[str] = []
    for token in tokens:
        if _looks_technical(token):
            continue
        lowered = token.lower().strip("'-")
        if len(lowered) <= 3 or lowered in ACADEMIC_DICTIONARY:
            continue
        if not _PLACEHOLDER_VOWEL.search(lowered):
            continue
        candidates.append(lowered)
    unknown: set[str] = set()
    if candidates:
        for word in _spell.unknown(candidates):
            if word not in ACADEMIC_DICTIONARY:
                unknown.add(word)
    for word in sorted(unknown)[:8]:
        issues.append(Issue("SPELLING", f"Possible spelling issue: '{word}'.", "warning"))

    sentences = [part for part in re.split(r"(?<=[.?!])\s+", text.strip()) if part]
    for sentence in sentences:
        cleaned = sentence.strip()
        if not cleaned:
            continue
        if cleaned[0].islower():
            issues.append(Issue("GRAMMAR_CAPITALIZATION", "Sentence does not start with a capital letter."))
        if cleaned.count("(") != cleaned.count(")"):
            issues.append(Issue("GRAMMAR_BRACKETS", "Unbalanced parentheses detected."))
        if re.search(r"\b(\w+)\s+\1\b", cleaned, re.I):
            issues.append(Issue("GRAMMAR_REPEAT", "Repeated word detected in a sentence."))
        if len(cleaned.split()) > 55:
            issues.append(Issue("GRAMMAR_LONG_SENTENCE", "Sentence is very long; consider splitting it."))
        if re.search(r"\s[,.;:]", cleaned):
            issues.append(Issue("GRAMMAR_PUNCTUATION", "Space before punctuation detected."))

    errors = [issue for issue in issues if issue.severity == "error"]
    spelling_warnings = len({issue.message for issue in issues if issue.code == "SPELLING"})
    passed = not errors and spelling_warnings <= 2
    score = max(0.0, 1.0 - 0.12 * len(issues))
    return LevelResult(
        level=2,
        validator="language",
        passed=passed,
        score=score,
        issues=issues,
        detail={
            "words_checked": len(tokens),
            "technical_terms_skipped": len([t for t in tokens if _looks_technical(t)]),
            "unknown_words": sorted(unknown),
            "dictionary_size": len(ACADEMIC_DICTIONARY),
        },
    )


def validate_semantics(
    question: dict[str, Any], context_text: str, provider
) -> LevelResult:
    try:
        assessment = provider.judge_question(question=question, context_text=context_text)
    except Exception as exc:  # noqa: BLE001
        return LevelResult(
            level=3,
            validator="semantic",
            passed=False,
            score=0.0,
            issues=[Issue("SEMANTIC_UNAVAILABLE", "Semantic validator could not be reached.")],
            detail={"error": type(exc).__name__},
        )
    thresholds = {
        "overall": 0.60,
        "syllabus_relevance": 0.50,
        "factual_confidence": 0.50,
        "clarity": 0.50,
        "answerability": 0.50,
    }
    issues: list[Issue] = []
    for key, floor in thresholds.items():
        try:
            value = float(assessment.get(key, 0))
        except (TypeError, ValueError):
            value = 0.0
        if value < floor:
            issues.append(
                Issue(
                    f"SEMANTIC_{key.upper()}",
                    f"Semantic check '{key}' scored {value:.2f} (minimum {floor:.2f}).",
                )
            )
    for note in assessment.get("issues", [])[:4]:
        issues.append(Issue("SEMANTIC_NOTE", str(note)[:200], "warning"))
    passed = not [issue for issue in issues if issue.severity == "error"]
    score = float(assessment.get("overall", 0) or 0)
    return LevelResult(
        level=3,
        validator="semantic",
        passed=passed,
        score=score,
        issues=issues,
        detail={
            "assessment": {
                key: assessment.get(key)
                for key in (
                    "syllabus_relevance",
                    "factual_confidence",
                    "clarity",
                    "difficulty_match",
                    "bloom_match",
                    "answerability",
                    "overall",
                )
            },
            "mode": assessment.get("mode", provider.mode),
        },
    )


def validate_duplicates(
    question: dict[str, Any],
    embedding: list[float],
    existing_embeddings: list[list[float]],
    existing_normalized: set[str],
    threshold: float = 0.86,
) -> LevelResult:
    text = str(question.get("question_text") or "")
    normalized = normalize_for_duplicate(text)
    issues: list[Issue] = []
    highest = 0.0
    if normalized in existing_normalized:
        issues.append(Issue("DUPLICATE_EXACT", "An identical question already exists in this paper."))
        highest = 1.0
    for vector in existing_embeddings:
        similarity = cosine(embedding, vector)
        highest = max(highest, similarity)
        if similarity >= threshold:
            issues.append(
                Issue("DUPLICATE_SEMANTIC", "A semantically similar question already exists.")
            )
            break
    return LevelResult(
        level=4,
        validator="duplicates",
        passed=not issues,
        score=round(1.0 - highest, 3) if issues else 1.0,
        issues=issues,
        detail={"max_similarity": round(highest, 3), "threshold": threshold},
    )


def validate_candidate(
    *,
    question: dict[str, Any],
    spec: dict[str, Any],
    context_text: str,
    provider,
    existing_embeddings: list[list[float]],
    existing_normalized: set[str],
) -> ValidationReport:
    embedder = get_embedder()
    embedding = embedder.embed_one(str(question.get("question_text") or ""))
    levels = [
        validate_rules(question, spec),
        validate_language(question),
        validate_semantics(question, context_text, provider),
        validate_duplicates(question, embedding, existing_embeddings, existing_normalized),
    ]
    passed = all(level.passed for level in levels)
    weights = {1: 0.3, 2: 0.15, 3: 0.4, 4: 0.15}
    quality = sum(level.score * weights[level.level] for level in levels)
    issue_codes = [issue.code for level in levels for issue in level.issues if issue.severity == "error"]
    return ValidationReport(
        passed=passed,
        quality_score=round(quality, 3),
        levels=levels,
        embedding=embedding,
        issues=issue_codes,
    )


def validate_paper(paper: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Level 5: validate the assembled paper as a whole."""
    issues: list[Issue] = []
    questions = paper.get("questions")
    if questions is None:
        # The canonical rendered payload nests questions under sections.
        questions = [
            question
            for section in paper.get("sections", [])
            for question in section.get("questions", [])
        ]
    total_marks = sum(int(question.get("marks", 0)) for question in questions)
    expected_marks = int(config.get("total_marks", 0))
    if total_marks != expected_marks:
        issues.append(
            Issue("TOTAL_MARKS", f"Paper totals {total_marks} marks, expected {expected_marks}.")
        )
    pattern = config.get("pattern", [])
    expected_count = sum(int(item.get("count", 0)) for item in pattern)
    if len(questions) != expected_count:
        issues.append(
            Issue("QUESTION_COUNT", f"Paper has {len(questions)} questions, expected {expected_count}.")
        )
    by_marks: dict[int, int] = {}
    for question in questions:
        mark = int(question.get("marks", 0))
        by_marks[mark] = by_marks.get(mark, 0) + 1
    for item in pattern:
        if by_marks.get(int(item["marks"]), 0) != int(item["count"]):
            issues.append(
                Issue(
                    "PATTERN_MISMATCH",
                    f"Expected {item['count']} question(s) of {item['marks']} marks.",
                )
            )

    def distribution(actual: dict[str, int], expected: dict[str, float], label: str) -> None:
        total = sum(actual.values()) or 1
        for key, target in expected.items():
            share = 100.0 * actual.get(key, 0) / total
            if abs(share - float(target)) > 10.0:
                issues.append(
                    Issue(
                        f"{label.upper()}_DISTRIBUTION",
                        f"{label} '{key}' share is {share:.1f}% (target {target}%).",
                    )
                )

    difficulty_counts: dict[str, int] = {}
    bloom_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for question in questions:
        difficulty_counts[question["difficulty"]] = difficulty_counts.get(question["difficulty"], 0) + 1
        bloom_counts[question["bloom_level"]] = bloom_counts.get(question["bloom_level"], 0) + 1
        topic_key = question.get("topic_id") or question.get("topic_name") or question.get("topic", "")
        topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
    distribution(difficulty_counts, config.get("difficulty_distribution", {}), "difficulty")
    distribution(bloom_counts, config.get("bloom_distribution", {}), "bloom")
    topic_names = config.get("topic_names", {})
    topic_share = {
        topic_names.get(name, name): round(100.0 * count / (len(questions) or 1), 2)
        for name, count in topic_counts.items()
    }
    distribution(topic_counts, config.get("topic_distribution", {}), "topic")

    normalized_seen: set[str] = set()
    for question in questions:
        key = normalize_for_duplicate(question.get("question_text", ""))
        if key in normalized_seen:
            issues.append(Issue("PAPER_DUPLICATE", "Paper contains duplicate questions."))
            break
        normalized_seen.add(key)
        if question.get("validation_status", "PASSED") not in {"PASSED", "SELECTED"}:
            issues.append(Issue("UNVALIDATED_QUESTION", "Paper contains a question that did not pass validation."))
            break

    if not paper.get("sections"):
        issues.append(Issue("MISSING_SECTIONS", "Paper has no section structure."))

    errors = [issue for issue in issues if issue.severity == "error"]
    score = max(0.0, 1.0 - 0.08 * len(errors))
    return {
        "passed": not errors,
        "score": round(score, 3),
        "issues": [issue.as_dict() for issue in issues],
        "metrics": {
            "total_marks": total_marks,
            "question_count": len(questions),
            "topic_share": topic_share,
            "difficulty_counts": difficulty_counts,
            "bloom_counts": bloom_counts,
            "pattern_counts_by_marks": by_marks,
        },
    }


def min_quality_score() -> float:
    return 0.6 if settings.DEMO_MODE else 0.7
