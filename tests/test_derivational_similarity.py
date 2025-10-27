import pytest

from word_cli.similarity import pick_distractors


def _pick(target, cands, translations, desired=3):
    return pick_distractors(target, cands, translations, desired=desired, pool_size=50)


def test_adj_to_adv_equivalence():
    target = "possible"
    cands = ["possibly", "impossible", "possibility", "positive"]
    translations = {
        "possibly": "可能地",
        "impossible": "不可能的",
        "possibility": "可能性",
        "positive": "积极的",
    }
    out = _pick(target, cands, translations, desired=2)
    # 'possibly' should be filtered as trivial adj->adv
    assert "可能地" not in out


def test_y_to_ily_equivalence():
    target = "happy"
    cands = ["happily", "happiness", "happen"]
    translations = {
        "happily": "高兴地",
        "happiness": "幸福",
        "happen": "发生",
    }
    out = _pick(target, cands, translations, desired=2)
    assert "高兴地" not in out


def test_verb_to_noun_tion_equivalence():
    target = "operate"
    cands = ["operation", "operator", "cooperate"]
    translations = {
        "operation": "操作/手术",
        "operator": "操作员",
        "cooperate": "合作",
    }
    out = _pick(target, cands, translations, desired=2)
    assert "操作/手术" not in out


def test_te_to_tion_equivalence():
    target = "create"
    cands = ["creation", "creative", "creature"]
    translations = {
        "creation": "创造物",
        "creative": "有创造力的",
        "creature": "生物",
    }
    out = _pick(target, cands, translations, desired=2)
    assert "创造物" not in out


def test_sion_patterns_equivalence():
    samples = [
        ("decide", "decision", "决定"),
        ("collide", "collision", "碰撞"),
        ("explode", "explosion", "爆炸"),
        ("conclude", "conclusion", "结论"),
        ("confuse", "confusion", "混乱"),
        ("permit", "permission", "许可"),
    ]
    for verb, noun, zh in samples:
        out = _pick(verb, [noun], {noun: zh}, desired=1)
        assert zh not in out


def test_adj_to_noun_ance_ence_equivalence():
    pairs = [
        ("dependent", "dependence", "依赖"),
        ("different", "difference", "差异"),
        ("resistant", "resistance", "抵抗"),
        ("important", "importance", "重要性"),
    ]
    for adj, noun, zh in pairs:
        out = _pick(adj, [noun], {noun: zh}, desired=1)
        assert zh not in out
