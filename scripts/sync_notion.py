#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion「📋 栽培記録」DB → data.json 生成スクリプト

環境変数:
  NOTION_TOKEN        Notionインテグレーションのトークン（GitHub Secretsから渡す）
  NOTION_DATABASE_ID  栽培記録DBのID
  OUTPUT_PATH         出力先（省略時 data.json）
  RECORD_LIMIT        recordsに含める件数（省略時 60）
  ARCHIVE_DAYS        この日数以上記録がない株を「過去の株」とする（省略時 30）
  CRITICAL_LIMIT      「すぐに確認してください」に一度に並べる件数（省略時 3）

依存ライブラリなし（標準ライブラリのみ）
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "").strip()
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "data.json")
RECORD_LIMIT = int(os.environ.get("RECORD_LIMIT", "60"))
ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "30"))
CRITICAL_LIMIT = int(os.environ.get("CRITICAL_LIMIT", "3"))

NOTION_VERSION = "2022-06-28"
API_URL = "https://api.notion.com/v1/databases/{}/query"

# ---------------------------------------------------------------------------
# 表記ゆれの正規化テーブル
#   Notion側に重複した選択肢が残っているため、集計前にここで統合する
# ---------------------------------------------------------------------------
PLANT_ALIAS = {
    "すいか": "🍉すいか",
    "スイカ": "🍉すいか",
    "トマト": "🍅トマト",
    "ナス": "🍆ナス",
    "大葉": "🌿大葉",
    "バジル": "🌱バジル",
    "パキラ": "🌴パキラ",
    "子宝草": "🌵子宝草",
    "ゴムの木": "🪴ゴムの木",
}

PRIORITY_ALIAS = {
    "高": "🔴高",
    "中": "🟡中",
    "低": "🟢低",
}

WEATHER_ALIAS = {
    "晴れ": "☀️晴れ",
    "快晴": "☀️晴れ",
    "曇り": "🌤くもり",
    "くもり": "🌤くもり",
    "やや曇り": "🌤くもり",
    "曇り時々晴れ": "🌤くもり",
    "雨": "🌧雨",
    "霧雨": "🌧雨",
    "強風": "🌤くもり",
}


def normalize(value, table):
    """表記ゆれを統合する。'曇り 15°C' のような温度付き文字列も先頭語で判定"""
    if not value:
        return None
    if value in table:
        return table[value]
    head = value.split(" ")[0].split("　")[0]
    return table.get(head, value)


# ---------------------------------------------------------------------------
# Notionプロパティの取り出しヘルパー
# ---------------------------------------------------------------------------
def p_title(props, name):
    arr = (props.get(name) or {}).get("title") or []
    return "".join(t.get("plain_text", "") for t in arr).strip() or None


def p_text(props, name):
    arr = (props.get(name) or {}).get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in arr).strip() or None


def p_select(props, name):
    sel = (props.get(name) or {}).get("select")
    return sel.get("name") if sel else None


def p_multi(props, name):
    arr = (props.get(name) or {}).get("multi_select") or []
    return [x.get("name") for x in arr if x.get("name")]


def p_number(props, name):
    return (props.get(name) or {}).get("number")


def p_date(props, name):
    d = (props.get(name) or {}).get("date")
    if not d or not d.get("start"):
        return None
    return d["start"][:10]


def p_files(props, name):
    return len((props.get(name) or {}).get("files") or [])


def to_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Notion API
# ---------------------------------------------------------------------------
def fetch_all_pages():
    """DB全件をページネーションしながら取得（日付降順）"""
    results = []
    cursor = None
    url = API_URL.format(DATABASE_ID)

    while True:
        payload = {
            "page_size": 100,
            "sorts": [{"property": "日付", "direction": "descending"}],
        }
        if cursor:
            payload["start_cursor"] = cursor

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + NOTION_TOKEN,
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print("Notion APIエラー: HTTP {}\n{}".format(e.code, body), file=sys.stderr)
            raise

        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return results


# ---------------------------------------------------------------------------
# 変換
# ---------------------------------------------------------------------------
def build_record(page):
    props = page.get("properties", {})
    plant = normalize(p_select(props, "植物名"), PLANT_ALIAS)

    return {
        "id": page.get("id"),
        "date": p_date(props, "日付"),
        "created": (page.get("created_time") or "")[:19],
        "title": p_title(props, "名前"),
        "plant": plant,
        "phase": p_select(props, "フェーズ"),
        "health": p_select(props, "健康状態"),
        "watering": p_select(props, "水やり判定"),
        "harvest": p_select(props, "収穫判定"),
        "weather": normalize(p_select(props, "天候"), WEATHER_ALIAS),
        "temp": p_number(props, "気温"),
        "priority": normalize(p_select(props, "優先度"), PRIORITY_ALIAS),
        "status": p_select(props, "対応ステータス"),
        "due": p_date(props, "対応期限"),
        "last_watered": p_date(props, "最終水やり日"),
        "ai_comment": p_text(props, "AI診断"),
        "action": p_text(props, "今日のアクション"),
        "works": p_multi(props, "実施した作業") or p_multi(props, "作業内容"),
        "by": p_select(props, "実施者"),
        "has_photo": p_files(props, "写真") > 0,
    }


def days_between(from_date, today):
    d = to_date(from_date)
    if not d:
        return None
    return (today - d).days


def classify(plant, today):
    """株ごとに追加タグと緊急度を決める。数値が小さいほど急ぎ。"""
    flags = []
    urgency = 5

    due = to_date(plant["due"])
    if plant["health"] == "異常":
        urgency = 0
    if due and plant["status"] == "未対応" and due <= today:
        flags.append("期限")
        urgency = min(urgency, 1)
    if plant["harvest"] == "急ぎ":
        flags.append("収穫")
        urgency = min(urgency, 2)
    if plant["watering"] == "必要":
        flags.append("水やり")
        urgency = min(urgency, 3)
    if plant["health"] == "注意":
        urgency = min(urgency, 4)

    plant["flags"] = flags
    plant["urgency"] = urgency
    return plant


def build_output(records):
    today = datetime.now(JST).date()
    week_ago = today - timedelta(days=7)

    # --- 株ごとの最新状態 ---------------------------------------------------
    plants = {}
    for r in records:
        name = r["plant"]
        if not name or name in plants:
            continue  # 日付降順なので最初に出たものが最新
        gap = days_between(r["date"], today)
        plants[name] = {
            "name": name,
            # ダッシュボードから作業を申告するとき、この記録を更新する
            "record_id": r["id"],
            "works": r["works"],
            "phase": r["phase"],
            "health": r["health"],
            "watering": r["watering"],
            "harvest": r["harvest"],
            "status": r["status"],
            "due": r["due"],
            "priority": r["priority"],
            "last_watered": r["last_watered"],
            "days_since_water": days_between(r["last_watered"], today),
            "last_record": r["date"],
            "days_since_record": gap,
            "ai_comment": r["ai_comment"],
            "action": r["action"],
            # 一定期間記録がなければ「過去の株」とみなす
            "active": (gap is None) or (gap < ARCHIVE_DAYS),
        }

    for p in plants.values():
        classify(p, today)

    active = sorted(
        [p for p in plants.values() if p["active"]],
        key=lambda p: (p["urgency"], p["name"]),
    )
    archived = sorted(
        [p for p in plants.values() if not p["active"]],
        key=lambda p: p["last_record"] or "",
        reverse=True,
    )
    archived_names = [p["name"] for p in archived]

    # --- 直近7日の集計（栽培中の株のみ） ------------------------------------
    recent = [
        r for r in records
        if to_date(r["date"]) and to_date(r["date"]) >= week_ago
        and r["plant"] not in archived_names
    ]

    stats = {
        "plants_active": len(active),
        "plants_archived": len(archived),
        "needs_attention": sum(1 for p in active if p["urgency"] <= 3),
        "records_7d": len(recent),
        "watering_7d": sum(1 for r in recent if any("水やり" in w for w in (r["works"] or []))),
        "harvest_7d": sum(1 for r in recent if any("収穫" in w for w in (r["works"] or []))),
        "total_records": len(records),
    }

    # --- 最上部に出す重大な報せ ---------------------------------------------
    #   載せる条件は「異常、または対応期限が来ている」かつ「まだ未対応」。
    #   ダッシュボードで「確認した」を押すと対応ステータスが「対応不要」になり、
    #   この欄から外れる。カード自体は今までどおり残る。
    def critical_line(p):
        if p["health"] == "異常":
            level = "異常"
            why = "健康状態が「異常」と判定されています。"
        else:
            level = "期限"
            d = to_date(p["due"])
            if d and d < today:
                why = "対応期限（{}）を過ぎています。".format(p["due"])
            else:
                why = "対応期限は今日です。"
        action = (p["action"] or "").strip()
        return {
            "plant": p["name"],
            "record_id": p.get("record_id"),
            "level": level,
            "why": why,
            "action": action,
            # 「確認した」を押したとき、これらを送り返して現状を保つ
            # （送らないとNotion側で空になってしまう）
            "works": p["works"] or [],
            "last_watered": p["last_watered"] or "",
            # 旧い画面でも読めるように、理由と提案をつないだ文も残す
            "message": (why + action).strip(),
        }

    # 上限は画面側で適用する。表示しきれない分もここに含めて渡すことで、
    # ダッシュボードで「確認した」を押したとき、その場で次の株を繰り上げられる。
    urgent = [p for p in active if p["urgency"] <= 1 and p["status"] == "未対応"]
    critical = [critical_line(p) for p in urgent]

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "archive_days": ARCHIVE_DAYS,
        "stats": stats,
        "critical": critical,
        "critical_limit": CRITICAL_LIMIT,
        "plants": active,
        "archived": archived,
        "archived_names": archived_names,
        "records": records[:RECORD_LIMIT],
    }


def main():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("NOTION_TOKEN と NOTION_DATABASE_ID を設定してください", file=sys.stderr)
        sys.exit(1)

    pages = fetch_all_pages()
    print("取得件数: {}".format(len(pages)))

    records = [build_record(p) for p in pages]
    records.sort(key=lambda r: r["date"] or "0000-00-00", reverse=True)

    output = build_output(records)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("出力: {} （栽培中 {} / 過去 {} / 要対応 {}）".format(
        OUTPUT_PATH,
        output["stats"]["plants_active"],
        output["stats"]["plants_archived"],
        output["stats"]["needs_attention"]))
    if output["archived_names"]:
        print("過去の株: " + "、".join(output["archived_names"]))


if __name__ == "__main__":
    main()
