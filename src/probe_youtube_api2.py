"""探针第二轮: 测"强制中等时长(videoDuration=medium, 排除 Shorts)"能否让中等播放量档浮出来.

medium = 4-20 分钟 → 物理上排除所有 ≤60s Shorts. 再 post-filter ≤300s = 拿到干净的 4-5 分钟正经视频.
同时测 热门关键词 vs 长尾关键词 的差异.

配额: ~6 calls = 600 units.
"""

from __future__ import annotations

import time

from curate_videos import get_client, iso8601_to_seconds

THROTTLE = 5
PUB_AFTER = "2024-01-01T00:00:00Z"
PUB_BEFORE = "2024-12-31T23:59:59Z"


def fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def search_medium(client, keyword: str, order: str, max_pages: int) -> list[str]:
    ids, page_token = [], None
    for _ in range(max_pages):
        time.sleep(THROTTLE)
        resp = client.search().list(
            q=keyword, type="video", part="id", maxResults=50, order=order,
            relevanceLanguage="en", regionCode="US",
            publishedAfter=PUB_AFTER, publishedBefore=PUB_BEFORE,
            videoDuration="medium",   # ← 关键: 4-20min, 物理排除 Shorts
            pageToken=page_token,
        ).execute()
        ids.extend(it["id"]["videoId"] for it in resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def get_details(client, ids: list[str]) -> list[dict]:
    out, seen = [], set()
    uniq = [i for i in ids if not (i in seen or seen.add(i))]
    for i in range(0, len(uniq), 50):
        resp = client.videos().list(
            id=",".join(uniq[i:i+50]), part="contentDetails,statistics"
        ).execute()
        for it in resp.get("items", []):
            try:
                out.append({
                    "view": int(it["statistics"].get("viewCount", 0)),
                    "dur": iso8601_to_seconds(it["contentDetails"]["duration"]),
                })
            except (KeyError, ValueError):
                pass
    return out


def report(label: str, details: list[dict]) -> None:
    if not details:
        print(f"\n── {label}: 无数据"); return
    views = sorted(d["view"] for d in details)
    n = len(views)
    mid_range = [d for d in details if 100_000 <= d["view"] < 1_000_000 and 60 <= d["dur"] <= 300]
    in_range = [d for d in details if 60 <= d["dur"] <= 300]
    shorts = [d for d in details if d["dur"] <= 60]
    print(f"\n── {label} (n={n}) ──")
    print(f"   播放量: min={fmt(views[0])} p25={fmt(views[n//4])} 中位={fmt(views[n//2])} "
          f"p75={fmt(views[3*n//4])} max={fmt(views[-1])}")
    print(f"   1-5分钟: {len(in_range)} 条 | Shorts: {len(shorts)} 条 | "
          f"★中等档+1-5分钟: {len(mid_range)} 条")


def main() -> None:
    client = get_client()
    print("强制 videoDuration=medium(4-20min, 排除 Shorts), 看中等播放量档能否浮出\n")

    for kw, tag in [("cooking recipe", "热门宽词"), ("sourdough bread tutorial", "长尾窄词")]:
        print(f"=== 关键词: '{kw}' ({tag}) ===")
        vc = search_medium(client, kw, "viewCount", 2)
        report(f"'{kw}' medium+viewCount", get_details(client, vc))
        dt = search_medium(client, kw, "date", 2)
        report(f"'{kw}' medium+date", get_details(client, dt))
        print()


if __name__ == "__main__":
    main()
