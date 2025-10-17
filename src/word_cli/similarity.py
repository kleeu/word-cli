from __future__ import annotations

import random
import re
from typing import Dict, Iterable, List

try:
    from rapidfuzz import fuzz, process  # type: ignore[import]
except ImportError:  # pragma: no cover - fallback path when dependency missing.
    fuzz = None  # type: ignore[assignment]
    process = None  # type: ignore[assignment]


_NON_ALPHA_RE = re.compile(r"[^a-z]+")


def _normalize(word: str) -> str:
    """Lowercase and strip non-letters for rough comparison.

    If stripping removes everything (e.g., non-latin), fallback to lower().
    """
    lowered = word.strip().lower()
    letters = _NON_ALPHA_RE.sub("", lowered)
    return letters or lowered


def _simple_inflection_equiv(a: str, b: str) -> bool:
    """Heuristic check: treat trivial case/inflection variants as equivalent.

    Covers: case-only, common suffixes (s/es/ed/ing/er/est), simple y->ies, e-drop.
    Symmetric: returns True if either could be an inflectional form of the other.
    """
    na, nb = _normalize(a), _normalize(b)
    if len(na) <= 1 or len(nb) <= 1:
        # keep ultra-short out of consideration but mark as equivalent to filter out
        return True
    if na == nb:
        return True

    def forms(base: str) -> set[str]:
        f = {base}
        # base + common suffixes
        for suf in ("s", "es", "ed", "ing", "er", "est"):
            f.add(base + suf)
        # e-drop before ing/ed
        if base.endswith("e") and len(base) > 1:
            stem = base[:-1]
            f.add(stem + "ing")
            f.add(stem + "ed")
        # y -> ies / ier / iest
        if base.endswith("y") and len(base) > 1:
            stem = base[:-1]
            f.add(stem + "ies")
            f.add(stem + "ier")
            f.add(stem + "iest")
        return f

    fa = forms(na)
    fb = forms(nb)
    return (na in fb) or (nb in fa) or bool(fa & fb)


def pick_distractors(
    target: str,
    candidates: Iterable[str],
    translations: Dict[str, str],
    desired: int = 3,
    pool_size: int = 20,
) -> List[str]:
    # Build pool excluding the exact string target first; additional filtering later
    pool = [word for word in candidates if word != target]
    if not pool:
        return []

    distractors: List[str] = []

    def consider(headword: str) -> bool:
        # filter trivial variants and very short tokens
        if _simple_inflection_equiv(target, headword):
            return False
        norm_len = len(_normalize(headword))
        if norm_len <= 1:
            return False
        tr = translations.get(headword)
        if not tr or tr in distractors:
            return False
        distractors.append(tr)
        return True

    seen_top: set[str] = set()
    if process and fuzz:  # Use rapidfuzz when available
        # 1) 取与 target 最相近的前 pool_size 个 headword（统一用小写进行评分）
        extracted = process.extract(
            target,
            pool,
            scorer=fuzz.QRatio,
            processor=str.lower,
            limit=min(pool_size, len(pool)),
        )
        # 2) 在这前 N 个中随机挑选，取其翻译作为干扰项
        top_matches = [match for match, _score, _ in extracted]
        seen_top.update(top_matches)
        random.shuffle(top_matches)
        for match in top_matches:
            if len(distractors) >= desired:
                break
            consider(match)

    # 3) 兜底：如果前 N 个里因为过滤/译文重复等原因不够，才从其余候选里补齐
    if len(distractors) < desired:
        tail_pool = [w for w in pool if w not in seen_top] if (process and fuzz) else pool
        random.shuffle(tail_pool)
        for match in tail_pool:
            if len(distractors) >= desired:
                break
            consider(match)

    return distractors[:desired]
