import pytest

from word_cli.similarity import pick_distractors


def test_pick_distractors_filters_inflections_and_short_tokens():
    target = "Apple"
    candidates = [
        "apple",  # same normalized
        "apples",  # plural
        "apply",  # similar but not inflection
        "app",  # too short after normalization
        "banana",
        "pineapple",
        "appeal",
        "maple",
        "a",  # single letter
    ]
    translations = {
        "apple": "苹果",
        "apples": "苹果（复数）",
        "apply": "应用",
        "app": "应用程序",
        "banana": "香蕉",
        "pineapple": "菠萝",
        "appeal": "上诉/吸引力",
        "maple": "枫树",
        "a": "一个",
    }

    # We want 3 distractors. They should not include apple/apples/a.
    out = pick_distractors(target, candidates, translations, desired=3, pool_size=10)
    assert len(out) == 3
    forbidden = {"苹果", "苹果（复数）", "一个"}
    assert all(x not in forbidden for x in out)


def test_pick_distractors_returns_unique_and_sufficient_when_possible():
    target = "play"
    candidates = ["plays", "played", "player", "pray", "ploy", "plow", "plate", "plan"]
    translations = {
        "plays": "玩（第三人称）",
        "played": "玩过",
        "player": "玩家",
        "pray": "祈祷",
        "ploy": "策略",
        "plow": "犁",
        "plate": "盘子",
        "plan": "计划",
    }
    out = pick_distractors(target, candidates, translations, desired=3, pool_size=8)
    assert len(out) == 3
    assert len(set(out)) == 3
