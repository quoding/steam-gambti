"""카탈로그 정책 v1.3.1: G11만 소6·중15·대3, 나머지 칸과 판정 버전은 그대로."""
from gembti.collector.build_catalog import CATALOG_POLICY, QUOTAS
from gembti.policy import get_policy

V13 = {"G01": (9, 18, 9, 18), "G02": (9, 18, 9, 18), "G03": (9, 18, 9, 18), "G04": (8, 16, 8, 16),
       "G05": (10, 20, 10, 20), "G06": (7, 14, 7, 14), "G07": (10, 20, 10, 20), "G08": (7, 14, 7, 14),
       "G11": (6, 12, 6, 12)}


def test_only_g11_changed():
    assert CATALOG_POLICY["catalog_policy_version"] == "v1.3.2"
    assert QUOTAS["G11"] == (6, 15, 3, 12)
    assert {g: q for g, q in QUOTAS.items() if g != "G11"} == {g: q for g, q in V13.items() if g != "G11"}


def test_totals():
    assert sum(q[0] for q in QUOTAS.values()) == 75
    assert sum(q[1] for q in QUOTAS.values()) == 153
    assert sum(q[2] for q in QUOTAS.values()) == 72
    assert sum(sum(q[:3]) for q in QUOTAS.values()) == 300 == CATALOG_POLICY["total"]
    assert sum(QUOTAS["G11"][:3]) == 24
    assert sum(q[3] for q in QUOTAS.values()) == 150


def test_other_rules_unchanged():
    assert (CATALOG_POLICY["min_coop"], CATALOG_POLICY["min_pvp"], CATALOG_POLICY["max_series"],
            CATALOG_POLICY["max_developer"]) == (40, 40, 2, 6)
    from gembti.collector.normalize import MIN_POSITIVE, MIN_REVIEWS
    assert (MIN_REVIEWS, MIN_POSITIVE) == (500, 75)


def test_judgement_versions_unchanged():
    v = get_policy().versions
    assert (v["questionnaire_version"], v["scoring_version"], v["type_mapping_version"],
            v["tie_policy_version"]) == ("v1.2", "v1.2", "v1.3", "v1.0")
    assert v["algorithm_version"] == "v1.3"  # 개인화 자격 W≥0.3
    assert v["catalog_policy_version"] == "v1.3.2"  # 성적 콘텐츠 적격성
