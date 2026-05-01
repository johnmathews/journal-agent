"""Reranker Protocol and adapters.

The reranker is the L2 stage of the hybrid search pipeline. After L1
retrieval (BM25 + dense) and RRF fusion produce a fan-out of candidate
entries, the reranker scores them against the query and returns a
trimmed top-K list ordered by post-rerank score.

Adapters:
- `NoopReranker` — passes candidates through in input order, scoring
  by reverse position. Used when `HYBRID_RERANKER=none` and as the
  default in unit tests that don't care about rerank behaviour.
- `AnthropicReranker` — listwise rerank via Claude (Haiku by default).
  Issues a single JSON-output prompt with the query plus all
  candidates, gets back ranked indices with one-line reasons. The
  system prompt is marked `cache_control` so repeated rerank calls
  share the static portion across requests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import anthropic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankCandidate:
    """One entry to rerank.

    `id` is opaque to the reranker — it's whatever identifier the
    caller wants echoed back in `RerankResult.id`. In the hybrid
    pipeline this is the entry id (as a string).

    `text` is the candidate's representative text. The reranker reads
    only this — it does not have access to the parent entry, the
    matching chunks, or any metadata. The caller is responsible for
    truncating to a reasonable length before passing in.
    """

    id: str
    text: str


@dataclass(frozen=True)
class RerankResult:
    """One reranked candidate with its post-rerank score and reason.

    `score` is in [0.0, 1.0] where higher is more relevant. The exact
    distribution depends on the adapter; only the ordering is
    contract.

    `reason` is a one-line natural-language justification. Optional —
    the Noop adapter leaves it as None. The Anthropic adapter populates
    it from the model's output for debugging and "why this matched"
    UX.
    """

    id: str
    score: float
    reason: str | None = None


@runtime_checkable
class Reranker(Protocol):
    """Protocol for query-time rerankers."""

    def rerank(
        self, query: str, candidates: list[RerankCandidate], top_k: int
    ) -> list[RerankResult]: ...


class NoopReranker:
    """Identity reranker — preserves input order, assigns descending scores.

    Useful when:
    - `HYBRID_RERANKER=none` (skip the L2 stage entirely).
    - Unit tests want to exercise the rest of the pipeline without
      mocking an LLM call.
    - The fusion-only baseline is the desired behaviour.

    Scores decay linearly from 1.0 down to 0.0 across the input list.
    They carry no semantic meaning beyond preserving order.
    """

    def rerank(
        self, query: str, candidates: list[RerankCandidate], top_k: int
    ) -> list[RerankResult]:
        if not candidates:
            return []
        n = len(candidates)
        out: list[RerankResult] = []
        for i, cand in enumerate(candidates[:top_k]):
            score = 1.0 - (i / max(n - 1, 1)) if n > 1 else 1.0
            out.append(RerankResult(id=cand.id, score=score, reason=None))
        return out


_SYSTEM_PROMPT = (
    "You are a relevance ranker for a personal journal search tool. "
    "You will be given a user's search query and a numbered list of candidate "
    "journal-entry passages that were retrieved by a hybrid keyword-and-semantic "
    "search system. Your job is to rank the candidates by how well each one "
    "answers the query, from most relevant to least relevant.\n\n"
    "Output a single JSON object with the shape:\n"
    "  {\n"
    '    "ranking": [\n'
    '      {"index": <int>, "score": <float 0-1>, "reason": "<one short sentence>"},\n'
    "      ...\n"
    "    ]\n"
    "  }\n\n"
    "Where:\n"
    "- `index` is the candidate's 1-based position in the input list.\n"
    "- `score` is your confidence the candidate is relevant, in [0, 1].\n"
    "- `reason` is at most one short sentence explaining the relevance.\n\n"
    "Rules:\n"
    "- Include every candidate exactly once. Do not invent indices.\n"
    "- Order the ranking array from most relevant first to least relevant last.\n"
    "- A candidate that mentions a name, place, or specific term from the query "
    "should rank highly, but so should a candidate that paraphrases or expresses "
    "the underlying concept without using the same words.\n"
    "- A candidate that only superficially mentions a query term but is not "
    "really about it should rank lower.\n"
    "- Do not output anything outside the JSON object. No prose, no markdown."
)


# Truncate per-candidate text before sending to keep the prompt bounded
# even when callers forget to trim. 600 characters is roughly 150 tokens
# — enough for the model to assess relevance but small enough that 50
# candidates fit comfortably in Haiku's context.
_MAX_CANDIDATE_CHARS = 600


# Anthropic silently ignores cache_control on system blocks below ~1024
# tokens for Haiku (2048 for Sonnet/Opus). The prompt above is well
# under that, so caching is currently a no-op — the cache_control
# attribute is set anyway for forward compatibility (Anthropic has
# trended toward lowering minimums) and surfaces as a single warning
# in startup logs so the cost surprise is visible.
_CACHEABLE_MINIMUM_TOKENS = 1024


class AnthropicReranker:
    """Listwise reranker using an Anthropic Claude model.

    Sends one request per `rerank()` call: the system prompt (cached)
    + a user message containing the query and the numbered candidate
    list. Parses the JSON response into `RerankResult` objects.

    On any failure (network error, malformed JSON, missing indices),
    falls back to `NoopReranker` ordering — search must not 500 just
    because the rerank stage hiccuped. The fallback is logged at WARN
    level so the operator can see when it happens.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 4096,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._noop = NoopReranker()

    @property
    def model(self) -> str:
        return self._model

    def rerank(
        self, query: str, candidates: list[RerankCandidate], top_k: int
    ) -> list[RerankResult]:
        if not candidates:
            return []
        if top_k <= 0:
            return []

        user_message = self._format_user_message(query, candidates)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as e:
            logger.warning(
                "AnthropicReranker call failed (%s); falling back to noop", e
            )
            return self._noop.rerank(query, candidates, top_k)

        raw = response.content[0].text if response.content else ""
        parsed = self._parse_response(raw, len(candidates))
        if parsed is None:
            logger.warning(
                "AnthropicReranker returned malformed JSON; falling back to noop. "
                "Raw response (first 200 chars): %r",
                raw[:200],
            )
            return self._noop.rerank(query, candidates, top_k)

        results: list[RerankResult] = []
        seen: set[int] = set()
        for entry in parsed:
            idx = entry["index"] - 1  # API is 1-based, list is 0-based
            if idx < 0 or idx >= len(candidates) or idx in seen:
                continue
            seen.add(idx)
            cand = candidates[idx]
            results.append(
                RerankResult(
                    id=cand.id,
                    score=float(entry["score"]),
                    reason=entry.get("reason"),
                )
            )
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _format_user_message(
        query: str, candidates: list[RerankCandidate]
    ) -> str:
        lines = [f"Query: {query}", "", "Candidates:"]
        for i, cand in enumerate(candidates, start=1):
            text = cand.text
            if len(text) > _MAX_CANDIDATE_CHARS:
                text = text[: _MAX_CANDIDATE_CHARS - 1] + "…"
            lines.append(f"[{i}] {text}")
        lines.append("")
        lines.append("Output the JSON ranking now.")
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str, n_candidates: int) -> list[dict] | None:
        """Parse the model output, returning the ranking list or None on failure.

        The model is instructed to output a single JSON object with a
        `ranking` array, but in practice models occasionally wrap output
        in markdown fences or add prose. This parser is forgiving: it
        looks for the first `{` and last `}` and parses the substring
        between them. If that fails or the shape is wrong, returns None
        so the caller can fall back.
        """
        if not raw:
            return None
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        ranking = parsed.get("ranking")
        if not isinstance(ranking, list) or not ranking:
            return None
        # Validate each entry has the expected keys before returning so
        # callers don't have to repeat the checks.
        cleaned: list[dict] = []
        for entry in ranking:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            score = entry.get("score")
            if not isinstance(idx, int) or not isinstance(score, (int, float)):
                continue
            if idx < 1 or idx > n_candidates:
                continue
            cleaned.append(entry)
        return cleaned or None


def build_reranker(
    name: str,
    *,
    anthropic_api_key: str = "",
    model: str = "claude-haiku-4-5",
) -> Reranker:
    """Build a reranker by name.

    Selects the adapter from a `HYBRID_RERANKER` env-var value. Unknown
    names raise — fail-fast at startup beats silently degrading to noop
    in production.
    """
    if name == "none" or name == "noop":
        return NoopReranker()
    if name == "anthropic":
        if not anthropic_api_key:
            raise ValueError(
                "AnthropicReranker requires ANTHROPIC_API_KEY to be set"
            )
        return AnthropicReranker(api_key=anthropic_api_key, model=model)
    raise ValueError(
        f"Unknown reranker {name!r} — must be 'anthropic' or 'none'"
    )
