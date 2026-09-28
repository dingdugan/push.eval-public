"""一次性探针: 实测 YouTube Data API v3 的取数边界, 回答"调请求能不能解决样本问题".

不是生产代码, 是 investigation. 用真实 API 调用回答几个问题:
  Q1. search.list 到底能翻多少页? (传说中的 ~500 结果硬上限存在吗)
  Q2. order=viewCount 翻页, 对热门类型, 能翻到"中等播放量(10万-100万)"那一段吗?
  Q3. order=date (按时间) 翻页, 能不能天然覆盖中等播放量?
  Q4. order=relevance (相关性) 呢?
  各方式的 Shorts(≤60s) 污染有多重?

配额: 每次 search.list = 100 units. 本探针约 8-10 calls = 800-1000 units.
"""

from __future__ import annotations

import time

from curate_videos import get_client, iso8601_to_seconds

THROTTLE = 5
KEYWORD = "cooking recipe"     # 热门类型(食物)代表 — 最容易"前排全是爆款"
PUB_AFTER = "2024-01-01T00:00:00Z"
PUB_BEFORE = "2024-12-31T23:59:59Z"


def fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def paginate_search(client, order: str, max_pages: int) -> list[str]:
    """按 order 翻 max_pages 页(或到 pageToken 耗尽), 返回 video_id 列表 + 实际页数."""
    ids: list[str] = []
    page_token = None
    pages_done = 0
    for _ in range(max_pages):
        time.sleep(THROTTLE)
        req = client.search().list(
            q=KEYWORD,
            type="video",
            part="id",
            maxResults=50,
            order=order,
            relevanceLanguage="en",
            regionCode="US",
            publishedAfter=PUB_AFTER,
            publishedBefore=PUB_BEFORE,
            pageToken=page_token,
        )
        resp = req.execute()
        ids.extend(it["id"]["videoId"] for it in resp.get("items", []))
        pages_done += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"  [{order:9s}] 翻了 {pages_done} 页, 拿到 {len(ids)} 条 "
          f"(pageToken {'耗尽 → 撞上限' if not page_token else '还有, 是我主动停的'})")
    return ids


def get_details(client, ids: list[str]) -> list[dict]:
    """videos.list 批量拿 viewCount + duration (1 unit/call, 50 id/call)."""
    out = []
    seen = set()
    uniq = [i for i in ids if not (i in seen or seen.add(i))]
    for i in range(0, len(uniq), 50):
        batch = uniq[i:i+50]
        resp = client.videos().list(
            id=",".join(batch), part="contentDetails,statistics,snippet"
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
    mid = [d for d in details if 100_000 <= d["view"] < 1_000_000]   # 中等档
    in_range = [d for d in details if 60 <= d["dur"] <= 300]          # 1-5 min
    shorts = [d for d in details if d["dur"] <= 60]                   # Shorts
    mid_and_range = [d for d in details if 100_000 <= d["view"] < 1_000_000 and 60 <= d["dur"] <= 300]
    print(f"\n── {label} (n={n}) ──")
    print(f"   播放量分布: min={fmt(views[0])}  p25={fmt(views[n//4])}  "
          f"中位={fmt(views[n//2])}  p75={fmt(views[3*n//4])}  max={fmt(views[-1])}")
    print(f"   落在【中等档 10万-100万】: {len(mid)} 条 ({100*len(mid)//n}%)")
    print(f"   落在【1-5分钟】: {len(in_range)} 条  |  Shorts(≤60s): {len(shorts)} 条 ({100*len(shorts)//n}%)")
    print(f"   ★ 同时满足【中等档 + 1-5分钟】(我们真正要的): {len(mid_and_range)} 条")


def main() -> None:
    client = get_client()
    print(f"探针关键词: '{KEYWORD}' / 窗口: 2024 全年 / 热门类型代表\n")

    print("Q1+Q2: order=viewCount 翻页, 看能不能翻到中等档 ↓")
    vc_ids = paginate_search(client, "viewCount", max_pages=6)
    report("order=viewCount (翻6页)", get_details(client, vc_ids))

    print("\nQ3: order=date 翻页, 看按时间取是否天然覆盖中等档 ↓")
    dt_ids = paginate_search(client, "date", max_pages=4)
    report("order=date (翻4页)", get_details(client, dt_ids))

    print("\nQ4: order=relevance 翻页 ↓")
    rv_ids = paginate_search(client, "relevance", max_pages=2)
    report("order=relevance (翻2页)", get_details(client, rv_ids))


if __name__ == "__main__":
    main()
