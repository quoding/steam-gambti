"""리뷰 요청 직렬화·요약 파싱·캐시 키 (네트워크 없음)."""
import json

from gembti.collector.fetch_reviews import params_hash, request_body, serialize, summarize


def test_enums_are_numeric_and_languages_all():
    sent = json.loads(serialize(request_body(730, 0)))
    assert sent["purchase_type"] == 0 and sent["review_type"] == 0 and sent["languages"] == ["all"]
    assert isinstance(sent["purchase_type"], int) and isinstance(sent["review_type"], int)
    assert "language" not in sent and "cursor" not in sent and sent["num_per_page"] == 1
    assert json.loads(serialize(request_body(730, 1)))["purchase_type"] == 1


def test_cache_key_covers_aggregation_params():
    assert params_hash(0) != params_hash(1) != params_hash(2)
    assert params_hash(0) == params_hash(0)


def test_summary_missing_not_zero():
    assert summarize({"response": {}}) is None
    assert summarize({"response": {"query_summary": {"total_reviews": 10}}}) is None  # positive/negative 없음
    s = summarize({"response": {"query_summary": {"total_reviews": 0, "total_positive": 0, "total_negative": 0,
                                                   "review_score": 0, "review_score_desc": "No user reviews"},
                                "total_matching": 0, "reviews": [{"review": "본문", "author": {"steamid": "1"}}]}})
    assert s == {"review_score": 0, "review_score_desc": "No user reviews", "total_positive": 0,
                 "total_negative": 0, "total_reviews": 0, "total_matching": 0}  # 명시적 0은 0, 본문·작성자 없음
