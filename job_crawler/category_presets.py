from __future__ import annotations

from typing import Any


ZHAOPIN_CATEGORY_PRESETS: dict[str, dict[str, list[str]]] = {
    "直播/影视/传媒": {
        "影视制作": [
            "视频剪辑师",
            "音频编辑",
            "摄影/摄像",
            "编导",
            "导演",
            "编剧",
            "制片人",
            "美术指导",
            "录音/音效",
            "影视制作",
            "影视策划",
            "影视发行",
        ],
        "采编/写作/出版": [
            "编辑",
            "美编",
            "文案编辑",
            "主编/副主编",
            "总编/副总编",
            "采编",
            "记者",
            "撰稿人",
            "校对录入",
            "排版设计",
            "印刷排版",
            "出版发行",
            "发行管理",
        ],
        "主播/演艺人员/经纪人": [
            "主播",
            "带货主播",
            "中控/场控/助播",
            "演员/模特",
            "服装/试衣模特",
            "水族馆表演演员",
            "经纪人",
            "主持人",
            "配音",
            "DJ",
        ],
        "广告": [
            "广告文案",
            "广告设计",
            "广告创意设计",
            "创意总监",
            "广告制作",
            "广告协调",
            "广告美术指导",
            "广告审核",
        ],
        "公关媒介": [
            "品牌公关",
            "商务公关",
            "活动公关",
            "公关专员",
            "公关经理/主管",
            "公关总监",
            "媒介投放",
            "媒介商务BD",
            "媒介专员",
            "媒介经理/总监",
        ],
        "舞美设计": [
            "化妆师",
            "灯光师",
            "造型师",
            "舞美设计",
            "舞台艺术指导",
            "服装道具",
        ],
        "场务/剧务": [
            "放映员",
            "剧务",
            "摄影助理",
            "化妆助理",
            "导演助理",
            "艺人助理",
            "群演/跟组演员",
        ],
    }
}


def normalize_category_keyword(keyword: str) -> str:
    return str(keyword or "").strip().replace("／", "/").replace(" ", "")


def resolve_zhaopin_category(keyword: str) -> str | None:
    normalized = normalize_category_keyword(keyword)
    for canonical in ZHAOPIN_CATEGORY_PRESETS:
        if normalized in {canonical, canonical.replace("/", "")}:
            return canonical
    return None


def expand_zhaopin_keyword_groups(keywords: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for raw_keyword in keywords:
        canonical = resolve_zhaopin_category(raw_keyword)
        if not canonical:
            groups.append(
                {
                    "label": raw_keyword,
                    "searches": [
                        {
                            "search_keyword": raw_keyword,
                            "primary_category": raw_keyword,
                            "secondary_category": "",
                        }
                    ],
                }
            )
            continue

        searches: list[dict[str, str]] = []
        for secondary_category, terms in ZHAOPIN_CATEGORY_PRESETS[canonical].items():
            for term in terms:
                searches.append(
                    {
                        "search_keyword": term,
                        "primary_category": canonical,
                        "secondary_category": secondary_category,
                    }
                )
        groups.append({"label": canonical, "searches": searches})
    return groups
