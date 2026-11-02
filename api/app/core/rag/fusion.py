"""Shared, deterministic fusion for the serving path and offline evaluation."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class FusionConfig:
    method: str = "weighted"
    vector_weight: float = 0.6
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if self.method not in {"weighted", "rrf"}:
            raise ValueError("fusion method must be weighted or rrf")
        if not isfinite(self.vector_weight) or not 0 <= self.vector_weight <= 1:
            raise ValueError("vector_weight must be finite and between 0 and 1")
        if self.rrf_k < 1:
            raise ValueError("rrf_k must be positive")


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    if high - low < 1e-9:
        return {key: 1.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}


def fuse_scores(
    vector_scores: dict[str, float],
    bm25_scores: dict[str, float],
    config: FusionConfig,
) -> dict[str, float]:
    """A missing retrieval branch contributes zero, without mutating the other."""
    keys = vector_scores.keys() | bm25_scores.keys()
    if config.method == "rrf":
        vector_ranks = {
            key: rank for rank, key in enumerate(
                sorted(vector_scores, key=lambda k: (-vector_scores[k], k)), 1
            )
        }
        bm25_ranks = {
            key: rank for rank, key in enumerate(
                sorted(bm25_scores, key=lambda k: (-bm25_scores[k], k)), 1
            )
        }
        return {
            key: (
                (1 / (config.rrf_k + vector_ranks[key]) if key in vector_ranks else 0)
                + (1 / (config.rrf_k + bm25_ranks[key]) if key in bm25_ranks else 0)
            )
            for key in keys
        }
    vector = _normalize(vector_scores)
    bm25 = _normalize(bm25_scores)
    return {
        key: config.vector_weight * vector.get(key, 0)
        + (1 - config.vector_weight) * bm25.get(key, 0)
        for key in keys
    }
