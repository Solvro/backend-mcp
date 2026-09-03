from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    if va.shape != vb.shape:
        raise ValueError(f"vector length mismatch: {va.shape} != {vb.shape}")
    norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if norm == 0.0:
        return 0.0
    return float(np.dot(va, vb) / norm)


def cosine_similarities(
    query: Sequence[float], candidates: Sequence[Sequence[float]]
) -> NDArray[np.float64]:
    q = np.asarray(query, dtype=np.float64)
    matrix = np.asarray(candidates, dtype=np.float64)
    if matrix.size == 0:
        return np.empty(0, dtype=np.float64)
    if matrix.shape[1] != q.shape[0]:
        raise ValueError(f"dimension mismatch: query {q.shape[0]} vs rows {matrix.shape[1]}")

    q_norm = float(np.linalg.norm(q))
    row_norms = np.linalg.norm(matrix, axis=1)
    denom = row_norms * q_norm
    dots = matrix @ q
    return np.divide(dots, denom, out=np.zeros_like(dots), where=denom != 0.0)


def most_similar(
    query: Sequence[float], candidates: Sequence[Sequence[float]]
) -> tuple[int, float]:
    sims = cosine_similarities(query, candidates)
    if sims.size == 0:
        return -1, 0.0
    best = int(np.argmax(sims))
    return best, float(sims[best])
