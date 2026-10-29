"""LLM provider abstraction.

Two implementations ship with the platform:

* `OpenAIProvider` - any OpenAI-compatible chat completions endpoint. This is the
  only mode used for REAL_AI generation.
* `DemoProvider` - deterministic, template-based generator used when
  DEMO_MODE=true and no API key exists. It is grounded in the retrieved syllabus
  chunks but never claims to be a real model. Every API response that touches it
  is labelled DEMO.

The factory refuses to silently fall back to demo questions when DEMO_MODE is
false: AI generation then fails loudly instead of contaminating a real exam.
"""
from __future__ import annotations

import json
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


class LLMError(RuntimeError):
    pass


@dataclass
class ProviderInfo:
    name: str
    mode: str
    model: str
    grounded: bool


class LLMProvider(ABC):
    name: str = "base"
    mode: str = "DEMO"
    model: str = ""

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(self.name, self.mode, self.model, grounded=True)

    @abstractmethod
    def generate_questions(
        self, *, spec: dict[str, Any], context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return candidate question dicts for one slot specification."""

    @abstractmethod
    def judge_question(
        self, *, question: dict[str, Any], context_text: str
    ) -> dict[str, Any]:
        """Level-3 semantic evaluation."""

    @abstractmethod
    def generate_practice_questions(
        self,
        *,
        subject: str,
        topics: list[str],
        count: int,
        difficulty: str,
        context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """MCQ generation for the online practice module."""


def extract_json(text: str) -> Any:
    """Pull the first JSON array/object out of a model response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("Model did not return parseable JSON")


class OpenAIProvider(LLMProvider):
    name = "openai-compatible"
    mode = "REAL_AI"

    def __init__(self) -> None:
        if not settings.LLM_API_KEY:
            raise LLMError(
                "REAL_AI mode requires LLM_API_KEY. Set DEMO_MODE=true to use the demo provider."
            )
        self.model = settings.LLM_MODEL
        self.base_url = settings.LLM_BASE_URL.rstrip("/")

    def _chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": settings.AI_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.LLM_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    if response.status_code >= 500 or response.status_code == 429:
                        raise LLMError(f"Provider returned {response.status_code}")
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"]
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        raise LLMError(f"LLM request failed after retries: {last_error}")

    def generate_questions(
        self, *, spec: dict[str, Any], context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from app.ai.prompts import build_generation_messages

        system, user = build_generation_messages(spec, context)
        raw = self._chat(system, user)
        data = extract_json(raw)
        if isinstance(data, dict):
            data = data.get("questions", [])
        if not isinstance(data, list):
            raise LLMError("Expected a JSON array of questions")
        return [item for item in data if isinstance(item, dict)]

    def judge_question(self, *, question: dict[str, Any], context_text: str) -> dict[str, Any]:
        from app.ai.prompts import build_judge_messages

        system, user = build_judge_messages(question, context_text)
        raw = self._chat(system, user)
        data = extract_json(raw)
        return data if isinstance(data, dict) else {}

    def generate_practice_questions(
        self,
        *,
        subject: str,
        topics: list[str],
        count: int,
        difficulty: str,
        context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from app.ai.prompts import build_practice_messages

        system, user = build_practice_messages(subject, topics, count, difficulty, context)
        raw = self._chat(system, user)
        data = extract_json(raw)
        if isinstance(data, dict):
            data = data.get("questions", [])
        if not isinstance(data, list):
            raise LLMError("Expected a JSON array of practice questions")
        return [item for item in data if isinstance(item, dict)]


class DemoProvider(LLMProvider):
    """Deterministic demo generator.

    Produces grammatical, syllabus-grounded, rubric-aligned questions from the
    retrieved chunks so the whole pipeline (retrieval -> generation -> validation
    -> assembly -> encryption -> release) is demonstrable offline. Content is
    intentionally simple and must not be used for a real examination.
    """

    name = "praxis-demo-bank"
    mode = "DEMO"
    model = "template-demo-v1"

    PROHIBITED = ("ignore previous", "as an ai", "lorem ipsum", "{{")

    def _sentences(self, context: list[dict[str, Any]]) -> list[str]:
        sentences: list[str] = []
        for chunk in context:
            text = re.sub(r"\s+", " ", chunk.get("content", ""))
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                sentence = sentence.strip()
                if 45 <= len(sentence) <= 320 and "?" not in sentence:
                    sentences.append(sentence)
        return sentences

    def _anchor(self, topic: str, context: list[dict[str, Any]]) -> str:
        sentences = self._sentences(context)
        if sentences:
            seed = abs(hash(topic)) % len(sentences)
            return sentences[seed]
        return f"{topic} is a core concept in this subject."

    @staticmethod
    def _keywords(text: str, limit: int = 4) -> list[str]:
        stop = {
            "this", "that", "with", "from", "which", "their", "there", "these", "those",
            "have", "will", "shall", "into", "also", "such", "when", "where", "while",
            "each", "more", "most", "than", "then", "them", "they", "been", "being",
            "used", "using", "uses", "within", "about", "other", "because", "between",
        }
        words = [
            word.strip(".,;:()\"'").lower()
            for word in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text)
        ]
        seen: list[str] = []
        for word in words:
            if word in stop or word in seen:
                continue
            seen.append(word)
            if len(seen) >= limit:
                break
        return seen

    def generate_questions(
        self, *, spec: dict[str, Any], context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        topic = spec["topic_name"]
        marks = int(spec["marks"])
        difficulty = spec["difficulty"]
        bloom = spec["bloom_level"]
        subject = spec["subject_name"]
        anchor = self._anchor(topic, context)
        anchor_short = anchor[:220].rstrip(".")
        rng = random.Random(f"{topic}|{marks}|{difficulty}|{bloom}|{spec.get('slot_index', 0)}")
        keywords = self._keywords(anchor, 3) or [topic.lower()]
        focus = keywords[0]
        secondary = keywords[1] if len(keywords) > 1 else topic.lower()
        scenarios = [
            "a university registration system",
            "an online retail checkout flow",
            "a hospital records platform",
            "a campus library service",
            "a public transport booking system",
            "a cloud file-sharing service",
        ]
        scenario = scenarios[rng.randrange(len(scenarios))]
        styles = {
            2: [
                f"With reference to {focus}, define {topic} and state two properties that distinguish it within {subject}.",
                f"Explain how {focus} relates to {topic} and list two characteristics that matter in {subject} practice.",
                f"Define {topic} in the context of {secondary} and give one example drawn from {scenario}.",
            ],
            5: [
                f"Explain {topic} with a suitable example. Discuss how {focus} influences design decisions in {scenario}.",
                f"Compare two common approaches associated with {topic} and justify which is preferable when handling {focus}.",
                f"Describe how {topic} is applied in {scenario}. Support your answer using the role of {focus}.",
            ],
            10: [
                f"Analyse a realistic {subject} scenario in {scenario} that requires {topic}. Design a solution, justify your design choices, and evaluate two trade-offs involving {focus}.",
                f"Discuss {topic} in depth. Illustrate the underlying mechanism, apply it to {scenario}, and evaluate the limitations that arise when {focus} changes.",
                f"Design an approach for {scenario} that relies on {topic}. Explain the mechanism, justify the design against {secondary}, and evaluate two limitations.",
            ],
        }
        options = styles.get(marks, [f"Discuss {topic} in the context of {subject} with suitable justification."])
        text = rng.choice(options)
        source_sentence = anchor_short if anchor_short else topic
        return [
            {
                "question_text": text,
                "question_type": spec.get("question_type", "SHORT_ANSWER"),
                "marks": marks,
                "difficulty": difficulty,
                "bloom_level": bloom,
                "topic": topic,
                "expected_answer": (
                    f"Expected coverage: {source_sentence}. A complete answer should define the concept, "
                    f"apply it to the scenario and justify the reasoning at the {bloom.lower()} level."
                ),
                "source_ref": spec.get("source_ref") or (context[0].get("document_name", "") if context else ""),
            }
        ]

    def judge_question(self, *, question: dict[str, Any], context_text: str) -> dict[str, Any]:
        text = (question.get("question_text") or "").lower()
        context_words = {word for word in re.findall(r"[a-z]{4,}", context_text.lower())}
        question_words = [word for word in re.findall(r"[a-z]{4,}", text) if len(word) > 3]
        overlap = len([w for w in set(question_words) if w in context_words])
        grounding = min(1.0, 0.35 + overlap / max(6, len(set(question_words))))
        clarity = 0.9 if 25 <= len(text) <= 320 else 0.55
        answerable = 0.92 if question.get("marks", 2) <= 10 else 0.6
        difficulty = 0.85 if question.get("difficulty") else 0.5
        bloom = 0.85 if question.get("bloom_level") else 0.5
        overall = round((grounding * 0.35 + clarity * 0.25 + answerable * 0.2 + difficulty * 0.1 + bloom * 0.1), 3)
        return {
            "syllabus_relevance": round(grounding, 3),
            "factual_confidence": round(min(0.95, grounding + 0.1), 3),
            "clarity": clarity,
            "difficulty_match": difficulty,
            "bloom_match": bloom,
            "answerability": answerable,
            "overall": overall,
            "issues": [] if overall >= 0.6 else ["Low grounding in retrieved syllabus context"],
            "mode": "DEMO",
            "note": "Heuristic demo assessment - not a real model judgement.",
        }

    def generate_practice_questions(
        self,
        *,
        subject: str,
        topics: list[str],
        count: int,
        difficulty: str,
        context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        questions: list[dict[str, Any]] = []
        for index in range(count):
            topic = topics[index % len(topics)] if topics else subject
            anchor = self._anchor(topic, context)
            keywords = self._keywords(anchor, 3) or [topic.lower()]
            focus = keywords[index % len(keywords)]
            prompt = (
                f"In the context of {topic}, which statement about {focus} is most accurate?"
                if index % 3 != 2
                else f"A student is analysing {focus} while studying {topic}. Which approach is most appropriate?"
            )
            questions.append(
                {
                    "topic": topic,
                    "question_text": prompt,
                    "question_type": "SINGLE_CHOICE",
                    "difficulty": difficulty,
                    "bloom_level": "UNDERSTAND",
                    "options": [
                        {"id": "a", "text": f"It is governed by the rules and structure described for {topic}."},
                        {"id": "b", "text": "It applies only to systems that are no longer maintained."},
                        {"id": "c", "text": "It is unrelated to any topic in the syllabus."},
                        {"id": "d", "text": "It behaves identically in every possible scenario."},
                    ],
                    "correct_answers": ["a"],
                    "explanation": anchor[:200],
                    "marks": 1,
                }
            )
        return questions


_provider: LLMProvider | None = None


def build_provider() -> LLMProvider:
    global _provider
    if _provider is not None:
        return _provider
    choice = settings.effective_llm_provider
    if choice == "openai":
        _provider = OpenAIProvider()
    elif settings.DEMO_MODE:
        _provider = DemoProvider()
    else:
        raise LLMError(
            "No real LLM provider is configured and DEMO_MODE is disabled. "
            "Set LLM_API_KEY (and LLM_PROVIDER=openai) to generate questions."
        )
    return _provider


def set_provider(provider: LLMProvider | None) -> None:  # test helper
    global _provider
    _provider = provider


def provider_status() -> dict[str, Any]:
    try:
        provider = build_provider()
        info = provider.info
        return {
            "name": info.name,
            "mode": info.mode,
            "model": info.model,
            "demo_mode": settings.DEMO_MODE,
            "llm_provider_setting": settings.LLM_PROVIDER,
            "embedding_provider": settings.effective_embedding_provider,
            "warning": (
                "DEMO mode: questions are template-based, grounded in uploaded documents, "
                "and must not be used for real examinations."
                if info.mode == "DEMO"
                else "REAL_AI mode: questions are model-generated and still require validation and human release control."
            ),
        }
    except LLMError as exc:
        return {
            "name": "unconfigured",
            "mode": "REAL_AI",
            "model": "",
            "demo_mode": settings.DEMO_MODE,
            "llm_provider_setting": settings.LLM_PROVIDER,
            "embedding_provider": settings.effective_embedding_provider,
            "error": str(exc),
            "warning": "AI generation is not configured.",
        }
