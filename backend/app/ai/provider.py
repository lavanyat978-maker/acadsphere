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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError


class LLMError(AppError):
    """The AI provider failed. A 502 with a readable message, not an opaque 500."""

    def __init__(self, message: str, code: str = "AI_PROVIDER_ERROR") -> None:
        super().__init__(code, message, 502)


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


def _question_items(data: Any, what: str) -> list[dict[str, Any]]:
    """Normalise a model reply into a list of question dicts.

    The exam prompt asks for one bare question object; the practice prompt asks for
    {"questions": [...]}. Models also wrap or unwrap these on their own, so accept
    a list, a {"questions": [...]} object, or a single question object.
    """
    if isinstance(data, dict):
        if isinstance(data.get("questions"), list):
            data = data["questions"]
        elif "question_text" in data:
            data = [data]
        else:
            raise LLMError(f"The AI reply did not contain {what}.")
    if not isinstance(data, list):
        raise LLMError(f"Expected a JSON list of {what}.")
    return [item for item in data if isinstance(item, dict)]


class OpenAIProvider(LLMProvider):
    name = "openai-compatible"
    mode = "REAL_AI"
    # Seconds to wait before each retry of a timed-out, rate-limited or failing call.
    RETRY_DELAYS = (3, 9, 20)

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
        last_error = "no response"
        for attempt, delay in enumerate((*self.RETRY_DELAYS, None)):
            wait = delay
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
            except httpx.TimeoutException:
                last_error = f"timed out after {settings.LLM_TIMEOUT_SECONDS}s"
            except httpx.HTTPError as exc:
                last_error = f"network error ({type(exc).__name__})"
            else:
                status = response.status_code
                if status == 429 or status >= 500:
                    last_error = f"HTTP {status}" + (" (rate limited)" if status == 429 else "")
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after.isdigit() and delay is not None:
                        wait = min(int(retry_after), 30)
                elif status >= 400:
                    # A rejected request (bad key, unknown model) will not improve on retry.
                    raise LLMError(f"The AI provider rejected the request (HTTP {status}): {self._error_text(response)}")
                else:
                    try:
                        return response.json()["choices"][0]["message"]["content"]
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError("The AI provider returned a response in an unexpected format.") from exc
            if wait is not None:
                time.sleep(wait)
        raise LLMError(f"The AI provider did not answer successfully ({last_error}). Try again in a minute.")

    @staticmethod
    def _error_text(response: "httpx.Response") -> str:
        try:
            body = response.json()
            if isinstance(body, list) and body:
                body = body[0]
            message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        except ValueError:
            message = None
        return str(message or response.text or "no detail")[:240]

    def generate_questions(
        self, *, spec: dict[str, Any], context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from app.ai.prompts import build_generation_messages

        system, user = build_generation_messages(spec, context)
        raw = self._chat(system, user)
        return _question_items(extract_json(raw), "questions")

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
        return _question_items(extract_json(raw), "practice questions")


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

    @staticmethod
    def _stable(text: str) -> int:
        """Process-independent hash; Python's hash() changes on every restart."""
        import hashlib

        return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")

    def _anchor(self, topic: str, context: list[dict[str, Any]], variant: str = "") -> str:
        sentences = self._sentences(context)
        if sentences:
            return sentences[self._stable(f"{topic}|{variant}" if variant else topic) % len(sentences)]
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
        # A retry carries a variation hint; anchor it on a different syllabus
        # sentence so it is not rejected as a near-copy of the last attempt.
        hint = spec.get("variation_hint") or ""
        anchor = self._anchor(topic, context, f"{hint}|{spec.get('slot_index', 0)}" if hint else "")
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

    def _stem(self, subject: str, topic: str, anchor: str, index: int) -> str:
        """A question stem naming the concept the anchor sentence is about.

        Stems must differ: practice tests drop questions whose text repeats.
        """
        focus = (self._keywords(anchor, 1) or [topic.lower()])[0]
        templates = [
            "In {topic}, which statement about {focus} is correct?",
            "Which of the following correctly describes {focus} ({topic})?",
            "According to the {subject} syllabus, what is true of {focus}?",
            "Select the accurate statement about {focus} in {topic}.",
        ]
        return templates[index % len(templates)].format(topic=topic, focus=focus, subject=subject)

    @staticmethod
    def _swap_term(sentence: str, term: str, replacement: str) -> str:
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)

        def keep_case(match: re.Match[str]) -> str:
            return replacement[:1].upper() + replacement[1:] if match.group(0)[:1].isupper() else replacement

        return pattern.sub(keep_case, sentence, count=1)

    def generate_practice_questions(
        self,
        *,
        subject: str,
        topics: list[str],
        count: int,
        difficulty: str,
        context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Single-choice questions built from real syllabus sentences.

        The correct option is a sentence taken from the retrieved material.
        Distractors are the same sentence with one key term swapped for a term
        from elsewhere in the syllabus, so they read plausibly but are false.
        The correct option's position is spread across a-d.
        """
        sentences = [s for s in self._sentences(context) if 50 <= len(s) <= 260]
        vocabulary = sorted({word for sentence in sentences for word in self._keywords(sentence, 12)})
        questions: list[dict[str, Any]] = []
        # Walk every sentence once, in a stable topic-dependent order, so the
        # requested count is reached whenever the material allows it.
        candidates = [
            (topics[index % len(topics)] if topics else subject, sentence)
            for index, sentence in enumerate(
                sorted(sentences, key=lambda item: self._stable(f"{'|'.join(topics)}|{item}"))
            )
        ]
        for topic, anchor in candidates:
            if len(questions) >= count:
                break
            terms = [term for term in self._keywords(anchor, 6) if term.lower() not in topic.lower()]
            alternatives = [word for word in vocabulary if word not in anchor.lower()]
            distractors: list[str] = []
            for offset, term in enumerate(terms):
                if len(distractors) == 3 or not alternatives:
                    break
                replacement = alternatives[self._stable(f"{anchor}|{term}|{offset}") % len(alternatives)]
                altered = self._swap_term(anchor, term, replacement)
                if altered != anchor and altered not in distractors:
                    distractors.append(altered)
            if len(distractors) < 3:
                continue
            correct_slot = self._stable(f"{anchor}|slot") % 4
            texts = distractors[:]
            texts.insert(correct_slot, anchor)
            option_ids = ["a", "b", "c", "d"]
            questions.append(
                {
                    "topic": topic,
                    "question_text": self._stem(subject, topic, anchor, len(questions)),
                    "question_type": "SINGLE_CHOICE",
                    "difficulty": difficulty,
                    "bloom_level": "REMEMBER" if difficulty == "EASY" else "UNDERSTAND",
                    "options": [{"id": option_ids[i], "text": text} for i, text in enumerate(texts)],
                    "correct_answers": [option_ids[correct_slot]],
                    "explanation": f"The syllabus states: {anchor}",
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
