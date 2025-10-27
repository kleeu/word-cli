from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Tuple

from .config import AppConfig
from .models import SessionState, WordState
from .storage import ProgressStore, WordRepository
import random


def _chunked_shuffle(items: list, chunk_size: int = 50) -> list:
    if not items:
        return items
    result: list = []
    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        random.shuffle(chunk)
        result.extend(chunk)
    return result


def _select_initial_review_candidates(candidates: list[tuple[str, WordState]], limit: int) -> list[str]:
    """Select initial review list using a level-aware, memory-curve based scorer.

    Scoring model (level > 4 is considered mastered and excluded upstream):
    - Candidates: words with 1 <= level <= 4 and not mastered.
    - Score components per word i with level l and days since last seen d:
        1) Level weight: lower level gets higher base weight.
            w_level = {1: 1.0, 2: 0.7, 3: 0.45, 4: 0.25}
        2) Due factor (smooth, continuous around a target interval s_l):
            Use a sigmoid f_sig = 1 / (1 + exp(-(d - s_l)/tau)) and a simple
            saturation f_sat = d / (s_l + d). Mix them as
                f_mix = alpha * f_sig + (1 - alpha) * f_sat
            Target intervals (days): s = {1:1, 2:2, 3:4, 4:7}, tau=1.0, alpha=0.7
            If d < min_gap_days[l], heavily downweight (set due ~ 0) to avoid too-soon repeats.
        3) Aging boost to reduce starvation: g_age = log(1 + d), weighted by beta.
           A persistent per-word bonus (bonus_i) could be added in the future with
           weight gamma; here we set bonus_i=0 as no persistence is wired yet.

        Final score: score = w_level[l] * f_mix + beta * g_age + gamma * bonus_i

    Selection:
    - Sort by score, take top_k = min(len(pool), limit * top_k_factor).
    - Apply softmax over scores/temperature (T=1.0) and sample without replacement
      until reaching 'limit' or pool exhausted. This introduces controlled randomness
      and helps prevent starvation.
    - If all scores are non-positive (e.g., all suppressed by min_gap), fall back
      to random choice.

    Notes:
    - last_seen is expected as ISO date (YYYY-MM-DD). Missing/invalid values are
      treated as very old (high d), making them prioritized as due.
    - min_per_level quotas can be added later if needed for stricter balancing.
    """
    if not candidates or limit <= 0:
        return []

    # Local hyper-params grouped for readability
    params = {
        "target_intervals": {1: 1, 2: 2, 3: 4, 4: 7},  # s_l
        "level_weights": {1: 1.0, 2: 0.7, 3: 0.45, 4: 0.25},
        "tau": 1.0,
        "alpha": 0.7,
        "beta": 0.2,
        "gamma": 0.0,  # no persistent bonus wired yet
        "top_k_factor": 5,
        "temperature": 1.0,
        "min_gap_days": {1: 1, 2: 1, 3: 2, 4: 3},
    }

    today = date.today()

    def _days_since(last_seen: str | None) -> int:
        if not last_seen:
            return 999
        try:
            d = datetime.fromisoformat(last_seen).date()
            return max((today - d).days, 0)
        except Exception:
            return 999

    def _sigmoid(x: float) -> float:
        # Small numerical safe sigmoid
        if x >= 0:
            ex = 1.0 + (x if x < 50 else 50)  # keep magnitude in check (approx)
            # A simple bound; we don't need exact exp here—use a polynomial approx fallback
        import math
        try:
            return 1.0 / (1.0 + math.exp(-x))
        except OverflowError:
            return 0.0 if x < 0 else 1.0

    # Prepare scored pool (filter to levels 1..4 just in case)
    scored: list[tuple[str, float, int]] = []  # (headword, score, level)
    for headword, st in candidates:
        l = st.level
        if l < 1 or l > 4:
            continue
        d = _days_since(st.last_seen)
        s_l = params["target_intervals"].get(l, 4)
        w_l = params["level_weights"].get(l, 0.1)
        min_gap = params["min_gap_days"].get(l, 1)

        # Due factors
        x = (d - s_l) / max(params["tau"], 1e-6)
        f_sig = _sigmoid(x)
        f_sat = d / (s_l + d) if (s_l + d) > 0 else 0.0
        f_mix = params["alpha"] * f_sig + (1 - params["alpha"]) * f_sat

        # Apply min-gap suppression
        if d < min_gap:
            f_mix *= 0.05  # strong downweight instead of hard drop

        # Aging boost and (placeholder) bonus
        import math
        g_age = math.log1p(d)
        bonus_i = 0.0

        score = w_l * f_mix + params["beta"] * g_age + params["gamma"] * bonus_i
        scored.append((headword, float(score), l))

    if not scored:
        return []

    # Sort by score desc and form top-k pool
    scored.sort(key=lambda t: t[1], reverse=True)
    top_k = min(len(scored), max(limit * params["top_k_factor"], limit))
    pool = scored[:top_k]

    # Softmax sampling without replacement
    import math

    def _softmax(weights: list[float], temperature: float) -> list[float]:
        if not weights:
            return []
        if temperature <= 0:
            # near-deterministic: pick argmax repeatedly
            out = [0.0] * len(weights)
            out[int(max(range(len(weights)), key=lambda i: weights[i]))] = 1.0
            return out
        m = max(weights)
        exps = [math.exp((w - m) / temperature) for w in weights]
        s = sum(exps)
        return [e / s for e in exps] if s > 0 else [1.0 / len(exps)] * len(exps)

    result: list[str] = []
    pool_items = list(pool)
    # If all scores <= 0, skip softmax and fall back to random top-k
    if all(s <= 0 for _, s, _ in pool_items):
        random.shuffle(pool_items)
        return [h for h, _s, _l in pool_items[:limit]]

    while pool_items and len(result) < limit:
        weights = [s for _h, s, _l in pool_items]
        probs = _softmax(weights, params["temperature"])
        # sample one index according to probs
        r = random.random()
        acc = 0.0
        chosen_idx = 0
        for i, p in enumerate(probs):
            acc += p
            if r <= acc:
                chosen_idx = i
                break
        h, _s, _l = pool_items.pop(chosen_idx)
        result.append(h)

    return result


def prepare_daily_session(
    word_repo: WordRepository,
    store: ProgressStore,
    config: AppConfig,
    today: date | None = None,
) -> SessionState:
    target_date = (today or date.today()).isoformat()
    existing = store.load_session(target_date)
    if existing:
        return existing

    # Ensure progress store has all repo words initialized to -2 on first run
    word_repo_path = getattr(config, "default_words_file", None)
    try:
        from pathlib import Path as _P
        store.ensure_all_words_initialized(word_repo, _P(word_repo_path) if word_repo_path else None)
    except Exception:
        # best-effort; don't block session creation
        store.ensure_all_words_initialized(word_repo)

    all_words = word_repo.all_words()
    new_candidates: List[str] = []
    review_candidates: List[Tuple[str, WordState]] = []

    for headword in all_words:
        state = store.get_word_state(headword)
        if state.mastered:
            continue
        if state.level == -2:
            new_candidates.append(headword)
        elif 1 <= state.level <= 5:
            review_candidates.append((headword, state))

    if getattr(config, "random_new_selection", True):
        random.shuffle(new_candidates)
    new_words = new_candidates[: config.daily_new_limit]
    review_words = _select_initial_review_candidates(review_candidates, config.daily_review_limit)

    session = SessionState(
        date=target_date,
        new_words=new_words,
        review_words=review_words,
        completed=[],
        remaining_successes={},  # deprecated in new queue model; retained for compatibility
    )
    store.upsert_session(session)
    store.save()
    return session
