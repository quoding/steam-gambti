"""GetItems 원본 → 정규화 게임 레코드와 8.1 포함 기준 판정.

가격 필드(best_purchase_option 등)는 정규화하지 않는다. KR 상점 접근 가능 여부만 bool로 남긴다.
unknown은 true/false로 바꾸지 않는다.
"""
from __future__ import annotations

import re
import unicodedata

KOREAN_ELANG = 4
ENGLISH_ELANG = 0
# Steam content descriptor: 3=Adult Only Sexual Content, 4=Frequent Nudity or Sexual Content
ADULT_ONLY_DESCRIPTORS = {3, 4}
ASSET_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/"
HANGUL = re.compile(r"[가-힣]")

MIN_REVIEWS = 500
MIN_POSITIVE = 75


def _lang_status(langs: list | None, elang: int) -> str:
    if not langs:
        return "unknown"
    for entry in langs:
        if entry.get("elanguage") == elang:
            return "true" if entry.get("supported") else "false"
    return "false"


def _platform(platforms: dict | None, key: str) -> str:
    if platforms is None:
        return "unknown"
    return "true" if platforms.get(key) else "false"


def asset_url(item: dict, key: str) -> str | None:
    a = item.get("assets") or {}
    fmt, fn = a.get("asset_url_format"), a.get(key)
    if not fmt or not fn:
        return None
    return ASSET_BASE + fmt.replace("${FILENAME}", fn)


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = re.sub(r"[™®©]", "", s)
    s = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|gmbh|s\.a|sa|ab|oy|studios?|games?|entertainment|interactive)\b\.?", " ", s)
    s = re.sub(r"[^0-9a-z가-힣぀-ヿ一-鿿]+", "-", s).strip("-")
    return s or "unknown"


def normalize_item(item: dict, fetched_at: str) -> dict:
    basic = item.get("basic_info") or {}
    reviews = (item.get("reviews") or {}).get("summary_filtered") or {}
    release = item.get("release") or {}
    plats = item.get("platforms")
    tags = item.get("tags") or []
    devs = basic.get("developers") or []
    franchises = basic.get("franchises") or []
    desc = basic.get("short_description") or ""
    dev0 = devs[0] if devs else {}
    return {
        "appid": item.get("appid") or item.get("id"),
        "success": item.get("success"),
        "visible": bool(item.get("visible")),
        "item_type": item.get("type"),
        "name": item.get("name"),
        "short_description": desc,
        "short_description_lang": ("ko" if HANGUL.search(desc) else "other") if desc else None,
        "header_image": asset_url(item, "header"),
        "capsule_image": asset_url(item, "main_capsule"),
        "store_url": f"https://store.steampowered.com/app/{item.get('appid') or item.get('id')}/",
        "tags": [{"tagid": t["tagid"], "weight": t.get("weight"), "rank": i + 1} for i, t in enumerate(tags)],
        "windows": _platform(plats, "windows"),
        "mac": _platform(plats, "mac"),
        "linux": _platform(plats, "steamos_linux"),
        "vr_only": bool(((plats or {}).get("vr_support") or {}).get("vrhmd_only")),
        "korean": _lang_status(item.get("supported_languages"), KOREAN_ELANG),
        "english": _lang_status(item.get("supported_languages"), ENGLISH_ELANG),
        "review_count": reviews.get("review_count"),
        "percent_positive": reviews.get("percent_positive"),
        "release_date": release.get("steam_release_date") or release.get("original_release_date"),
        "is_early_access": bool(release.get("is_early_access")),
        "is_coming_soon": bool(release.get("is_coming_soon")),
        "content_descriptorids": item.get("content_descriptorids") or [],
        "purchasable_kr": bool(item.get("best_purchase_option")) or bool(item.get("is_free")),
        "is_free": bool(item.get("is_free")),
        "developers": [d.get("name") for d in devs],
        "developer_key": (f"clan:{dev0['creator_clan_account_id']}" if dev0.get("creator_clan_account_id")
                          else f"name:{norm_key(dev0.get('name', ''))}") if dev0 else "unknown",
        "franchise": franchises[0]["name"] if franchises else None,
        "fetched_at": fetched_at,
    }


def exclusion_reasons(g: dict, target_tagids: set[int], now_ts: int) -> list[str]:
    r = []
    if g["success"] != 1 or not g["visible"]:
        r.append("kr_store_not_visible")
    if g["item_type"] != 0:
        r.append("not_base_game")  # 0=게임. DLC·데모·사운드트랙·소프트웨어 등은 다른 type
    if not g["purchasable_kr"]:
        r.append("not_purchasable_kr")
    if g["windows"] != "true":
        r.append("windows_not_confirmed")
    if g["vr_only"]:
        r.append("vr_only")
    if g["is_early_access"]:
        r.append("early_access")
    if g["is_coming_soon"] or not g["release_date"] or g["release_date"] > now_ts:
        r.append("unreleased")
    if ADULT_ONLY_DESCRIPTORS & set(g["content_descriptorids"]):
        r.append("adult_only")
    if (g["review_count"] or 0) < MIN_REVIEWS:
        r.append("reviews_lt_500")
    if (g["percent_positive"] or 0) < MIN_POSITIVE:
        r.append("positive_lt_75")
    if g["korean"] != "true" and g["english"] != "true":
        r.append("no_ko_or_en")
    if g["korean"] == "unknown":
        r.append("korean_status_unknown")
    if not any(t["tagid"] in target_tagids for t in g["tags"]):
        r.append("no_target_tag")
    if not g["tags"]:
        r.append("tags_missing")
    return r


def review_size(count: int) -> str:
    if count >= 50000:
        return "large"
    if count >= 5000:
        return "mid"
    return "small"


def tag_W(g: dict) -> dict[int, float]:
    """W(g,t)=weight/최대 weight. weight가 모두 없으면 1/rank (fallback 기록은 호출자)."""
    ws = [t["weight"] for t in g["tags"] if t.get("weight")]
    if ws:
        m = max(ws)
        return {t["tagid"]: (t["weight"] or 0) / m for t in g["tags"]}
    return {t["tagid"]: 1 / t["rank"] for t in g["tags"]}


def assign_bucket(g: dict, group_of: dict[int, str], social: set[str]) -> str | None:
    """8.3-4: 비사회 그룹 중 실제 일치 태그 weight 최대가 가장 큰 그룹. 동점은 그룹 ID 오름차순.
    weight가 없으면 태그 순위가 앞선 그룹."""
    best: dict[str, int] = {}
    has_w = any(t.get("weight") for t in g["tags"])
    for t in g["tags"]:
        gid = group_of.get(t["tagid"])
        if gid is None or gid in social:
            continue
        key = (t["weight"] or 0) if has_w else -t["rank"]
        if gid not in best or key > best[gid]:
            best[gid] = key
    if not best:
        return None
    return min(best.items(), key=lambda kv: (-kv[1], kv[0]))[0]
