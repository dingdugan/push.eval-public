"""Day 8 curate 32 视频, 落 data/videos.jsonl. 2026-05-30 抽样重构后版本.

设计 (取代旧 8垂类×2时段×2view档 32格):
  - 唯一 spread 维度 = 8 个 YouTube 官方分类 (schemas.CATEGORY_ID)
  - 取数 = 每类 5 个真人感 query (category_queries.py) 各跑一次 + videoCategoryId 硬过滤, 合并去重
    (实测: q 的 | OR 是词级, 拼不了多词短语; 故 5 query 分跑再 union, 不 OR 成一串)
  - 时间 = 最近窗口 (默认 ~90 天; 不足放宽)
  - 时长 = 后验过滤, 下限可调 (DURATION_MIN_SEC, 默认 181=>3min 排除 Shorts; 上限 480=8min)
  - 播放量 = 各类 top N (按 view), 不分档, 记成连续协变量
  - 每类取 4 个 → 32 视频

为什么这么改: 见 notes/worklog.md 2026-05-30.
  - 旧 view 档是后验筛, 稀有档会"筛到天荒地老" → 砍掉, view 退成连续记录
  - 旧老/新分层跟 view 混淆, 污染因子无法干净识别 → 砍掉, 全取最近窗口(按构造消除污染)
  - videoCategoryId 是过滤器不是查询, 必须配 q 才返回 (实测); q 的 | OR 是词级拼不了短语

两阶段:
  A. stage_a_search_candidates(): 每类 5 query 各跑 → union → videos.list 拿 view/duration
     → 后验过滤 (时长 [MIN,MAX] + 英语) → 落 data/video_candidates.jsonl (每类 top N 候选)
  B. finalize(): 各类取 top VIDEOS_PER_CATEGORY (或人工 video_picks.tsv 指定) → 拉完整 metadata + 评论
     → data/videos.jsonl (32 条)

CLI:
  python src/curate_videos.py a                # stage A, 默认参数
  python src/curate_videos.py a --min-sec 120  # 放宽时长下限到 2min
  python src/curate_videos.py a --window-days 90
  python src/curate_videos.py b

环境: 需 YOUTUBE_API_KEY 在 .env.
配额: 8 类 × 5 query × 1 页 × 100 = 4,000 units (search) + videos.list ~少量. 单日 10k 够.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from category_queries import CATEGORY_QUERIES
from schemas import CATEGORY_ID, Category, VideoRecord

# ─────────────────────────────────────────────────────────
# 抽样参数 (2026-05-30 重构; 默认值, CLI 可覆盖)
# ─────────────────────────────────────────────────────────
VIDEOS_PER_CATEGORY = 4              # 每类最终取 4 → 8 类 = 32 视频
CANDIDATES_PER_CATEGORY = 30         # 每类落盘 top N 候选供人工挑/兜底/时长分布诊断
DEFAULT_DURATION_MIN_SEC = 181       # >3min: Shorts 上限是 3min, 故 >3min 铁定非 Short
DEFAULT_DURATION_MAX_SEC = 480       # 8min 上限 (控 var D 成本 + 贴短视频形态)
DEFAULT_WINDOW_DAYS = 90             # 最近窗口默认 90 天 (新视频 = 按构造消除训练污染)

# 节流 + 429 retry
SEARCH_THROTTLE_SEC = 5
QUOTA_429_BACKOFF = 60


# ─────────────────────────────────────────────────────────
# YouTube client
# ─────────────────────────────────────────────────────────

def get_client():
    load_dotenv()
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError(".env 缺 YOUTUBE_API_KEY (复制 .env.example → .env 并填入)")
    return build("youtube", "v3", developerKey=key)


def _exec_with_retry(request, max_attempts: int = 3):
    for attempt in range(1, max_attempts + 1):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 429:
                print(f"    [429 限流] {attempt}/{max_attempts}, 等 {QUOTA_429_BACKOFF}s")
                time.sleep(QUOTA_429_BACKOFF)
                continue
            raise
    raise RuntimeError(f"撞 429 限流 {max_attempts} 次仍未通过, 稍后再跑")


# ─────────────────────────────────────────────────────────
# 时长解析
# ─────────────────────────────────────────────────────────
_ISO_DUR_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def iso8601_to_seconds(iso: str) -> int:
    m = _ISO_DUR_RE.match(iso)
    if not m:
        return 0
    h, mm, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mm * 60 + s


def _published_after(days: int) -> str:
    # 取当下真实 UTC 时间往前推 days 天 (系统时钟 = 2026-05-30, 已 date 双验).
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────
# Stage A: 每类搜候选
# ─────────────────────────────────────────────────────────

def search_one_category(
    client,
    cat: Category,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_sec: int = DEFAULT_DURATION_MIN_SEC,
    max_sec: int = DEFAULT_DURATION_MAX_SEC,
) -> list[dict]:
    """搜单个官方分类候选: 5 个真人 query 各跑一次 + videoCategoryId 硬过滤, 合并去重.

    为什么 5 query 分跑而非 OR 成一串 (实测, notes 2026-05-30):
      - videoCategoryId 单用返回 0; 必须配 q 关键词.
      - q 的 | OR 是词级, 拼不了多词短语 ("a b c|d e f" 被解析成 "a b (c OR d) e f" → 命中近 0).
      - 故 5 个真人短语 query 各跑一次 search, 再 union → 既保短语语义又扩召回.

    步骤: 5× search.list(videoCategoryId, q=query_i, order=viewCount, publishedAfter)
         → union video_ids → videos.list 拿 view/duration → 后验过滤 [min_sec, max_sec] + 英语.
    """
    cat_id = CATEGORY_ID[cat]
    published_after = _published_after(window_days)
    queries = CATEGORY_QUERIES[cat]

    # 5 query 各跑一次, union (保序: 先出现的排前, 后面按 view 重排)
    seen: set[str] = set()
    uniq: list[str] = []
    for q in queries:
        time.sleep(SEARCH_THROTTLE_SEC)
        resp = _exec_with_retry(client.search().list(
            part="id",
            type="video",
            videoCategoryId=cat_id,
            q=q,
            order="viewCount",
            maxResults=50,
            relevanceLanguage="en",
            regionCode="US",
            publishedAfter=published_after,
        ))
        for it in resp.get("items", []):
            vid = it["id"]["videoId"]
            if vid not in seen:
                seen.add(vid)
                uniq.append(vid)

    # 批量详情
    details = []
    for i in range(0, len(uniq), 50):
        resp = client.videos().list(
            id=",".join(uniq[i:i+50]), part="snippet,contentDetails,statistics"
        ).execute()
        details.extend(resp.get("items", []))

    # 后验过滤: 时长 [min_sec, max_sec] + 英语
    cands = []
    for it in details:
        try:
            dur = iso8601_to_seconds(it["contentDetails"]["duration"])
            views = int(it["statistics"].get("viewCount", 0))
            sn = it["snippet"]
            lang = sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "en"
        except (KeyError, ValueError):
            continue
        if not (min_sec <= dur <= max_sec):
            continue
        if not lang.startswith("en"):
            continue
        cands.append({
            "category": cat.value,
            "category_id": cat_id,
            "youtube_id": it["id"],
            "title": sn["title"],
            "channel": sn["channelTitle"],
            "publish_date": sn["publishedAt"],
            "duration_sec": dur,
            "view_count": views,
            "language": lang,
            "window_days": window_days,
            "youtube_url": f"https://www.youtube.com/watch?v={it['id']}",
        })

    cands.sort(key=lambda x: -x["view_count"])
    return cands[:CANDIDATES_PER_CATEGORY]


def stage_a_search_candidates(
    out_path: Path = Path("data/video_candidates.jsonl"),
    done_path: Path = Path("data/curate_done_cats.txt"),
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_sec: int = DEFAULT_DURATION_MIN_SEC,
    max_sec: int = DEFAULT_DURATION_MAX_SEC,
) -> None:
    """8 类各搜候选 (每类 5 query 合并). 断点续跑: 已完成的类跳过, 每类跑完立刻 flush.

    时长下限 min_sec 可调: 默认 181 (>3min 排除 Shorts); 候选不足时调到 120/90 放宽.
    """
    client = get_client()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[params] window={window_days}天 duration=[{min_sec},{max_sec}]秒 "
          f"每类目标≥{VIDEOS_PER_CATEGORY} 落盘top{CANDIDATES_PER_CATEGORY}")

    done = set()
    if done_path.exists():
        done = {l.strip() for l in done_path.open(encoding="utf-8") if l.strip()}
        print(f"[resume] 跳过 {len(done)} 个已完成分类")

    with out_path.open("a", encoding="utf-8") as f_data, \
         done_path.open("a", encoding="utf-8") as f_done:
        for cat in Category:
            if cat.value in done:
                print(f"  {cat.value:<24} → skip (已完成)")
                continue
            try:
                cands = search_one_category(client, cat, window_days, min_sec, max_sec)
            except RuntimeError as e:
                print(f"\n[FATAL] {cat.value}: {e}\n        过几分钟再跑, 会从此续")
                return
            for c in cands:
                f_data.write(json.dumps(c, ensure_ascii=False) + "\n")
            f_data.flush()
            f_done.write(cat.value + "\n")
            f_done.flush()
            short = "  ⚠️不足" if len(cands) < VIDEOS_PER_CATEGORY else ""
            print(f"  {cat.value:<24} → {len(cands)} 候选{short}")

    print(
        f"\n[OK] stage A 完成 → {out_path}"
        f"\n     候选不足的类: 重跑并加 --min-sec 120 (放宽时长) 放大候选池"
        f"\n     下一步: 各类取 top {VIDEOS_PER_CATEGORY} → `python src/curate_videos.py b`"
        f"\n     (想人工指定: 写 data/video_picks.tsv, 每行 category<TAB>youtube_id)"
    )


# ─────────────────────────────────────────────────────────
# Stage B: 各类取 top N → 拉完整 metadata + 评论
# ─────────────────────────────────────────────────────────

def fetch_top_comments(client, youtube_id: str, n: int = 10) -> list[str]:
    try:
        resp = client.commentThreads().list(
            videoId=youtube_id, part="snippet", order="relevance",
            maxResults=n, textFormat="plainText",
        ).execute()
    except Exception as e:
        print(f"  [WARN] {youtube_id} 评论拉失败 ({e}); top_comments=[]")
        return []
    return [
        it["snippet"]["topLevelComment"]["snippet"]["textDisplay"]
        for it in resp.get("items", [])
    ]


def _load_picks(picks_path: Path, cands_by_cat: dict) -> list[tuple[str, str]]:
    """优先用人工 picks.tsv; 没有就各类自动取 top VIDEOS_PER_CATEGORY."""
    if picks_path.exists():
        picks = []
        for ln, line in enumerate(picks_path.open(encoding="utf-8"), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise ValueError(f"{picks_path}:{ln} 格式错, 期望 category<TAB>youtube_id")
            picks.append((parts[0], parts[1]))
        print(f"[picks] 用人工指定 {len(picks)} 条")
        return picks
    # 自动: 各类 top N (候选已按 view 倒序)
    picks = []
    for cat in Category:
        for c in cands_by_cat.get(cat.value, [])[:VIDEOS_PER_CATEGORY]:
            picks.append((cat.value, c["youtube_id"]))
    print(f"[picks] 无 picks.tsv, 各类自动取 top {VIDEOS_PER_CATEGORY} = {len(picks)} 条")
    return picks


def finalize(
    candidates_path: Path = Path("data/video_candidates.jsonl"),
    picks_path: Path = Path("data/video_picks.tsv"),
    out_path: Path = Path("data/videos.jsonl"),
) -> None:
    """从候选取最终 32 条 → 拉完整 metadata + 评论 → data/videos.jsonl."""
    # 加载候选 (按类分组, 保序=view 倒序)
    cands_by_cat: dict[str, list[dict]] = {}
    cands_by_key: dict[tuple[str, str], dict] = {}
    for line in candidates_path.open(encoding="utf-8"):
        c = json.loads(line)
        cands_by_cat.setdefault(c["category"], []).append(c)
        cands_by_key[(c["category"], c["youtube_id"])] = c

    picks = _load_picks(picks_path, cands_by_cat)
    client = get_client()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for cat_str, yt_id in picks:
            base = cands_by_key.get((cat_str, yt_id))
            if base is None:
                print(f"  [WARN] ({cat_str}, {yt_id}) 不在候选, 跳过")
                continue
            resp = client.videos().list(
                id=yt_id, part="snippet,contentDetails,statistics"
            ).execute()
            if not resp.get("items"):
                print(f"  [WARN] {yt_id} 详情空, 跳过")
                continue
            item = resp["items"][0]
            sn = item["snippet"]
            comments = fetch_top_comments(client, yt_id, n=10)

            n += 1
            video = VideoRecord(
                video_id=f"V{n:02d}",
                youtube_id=yt_id,
                youtube_url=base["youtube_url"],
                channel=sn["channelTitle"],
                category=Category(cat_str),
                category_id=CATEGORY_ID[Category(cat_str)],
                publish_date=sn["publishedAt"],
                view_count=int(item["statistics"].get("viewCount", base["view_count"])),
                duration_sec=base["duration_sec"],
                language=base["language"],
                video_title=sn["title"],
                video_description=sn.get("description", ""),
                video_tags=sn.get("tags", []),
                top_comments=comments,
            )
            f.write(video.model_dump_json() + "\n")
            print(f"  [{n:02d}] {video.video_id} [{cat_str}] {video.channel} — {video.video_title[:45]}")

    print(f"\n[OK] {n} 视频 → {out_path}")


# ─────────────────────────────────────────────────────────
# Stage B 兜底版: yt-dlp 抓 metadata + 评论 (零 Data API 配额)
# ─────────────────────────────────────────────────────────

def fetch_metadata_ytdlp(youtube_id: str, n_comments: int = 10) -> dict | None:
    """用 yt-dlp 抓单视频 metadata + top 评论, 不耗 Data API 配额.

    实测字段 (见 /tmp/ytdlp_fields.json): title/channel/view_count/duration/
    upload_date(YYYYMMDD)/tags/language/description/comments[].{text,like_count}.
    返回 None 表示抓取失败 (视频被删/私有/网络).
    """
    import subprocess

    cmd = [
        "yt-dlp", "--quiet", "--no-warnings", "--skip-download",
        "--dump-single-json", "--write-comments",
        "--extractor-args",
        f"youtube:max_comments={max(n_comments * 3, 30)};comment_sort=top",
        f"https://www.youtube.com/watch?v={youtube_id}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(f"  [WARN] yt-dlp {youtube_id} 退出码 {proc.returncode}: {proc.stderr[:80]}")
            return None
        d = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"  [WARN] yt-dlp {youtube_id} 失败: {type(e).__name__}")
        return None

    # 评论按点赞降序取 top n
    comments = sorted(
        d.get("comments") or [],
        key=lambda c: c.get("like_count") or 0,
        reverse=True,
    )
    top_comments = [c.get("text", "") for c in comments[:n_comments]]

    # upload_date YYYYMMDD → ISO (跟 Data API 的 publishedAt 对齐到日)
    ud = d.get("upload_date") or ""
    publish_date = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}T00:00:00Z" if len(ud) == 8 else ""

    return {
        "channel": d.get("channel") or d.get("uploader") or "",
        "view_count": d.get("view_count") or 0,
        "duration_sec": d.get("duration") or 0,
        "publish_date": publish_date,
        "language": (d.get("language") or "en"),
        "title": d.get("title") or "",
        "description": d.get("description") or "",
        "tags": d.get("tags") or [],
        "top_comments": top_comments,
    }


def finalize_ytdlp(
    candidates_path: Path = Path("data/video_candidates.jsonl"),
    picks_path: Path = Path("data/video_picks.tsv"),
    out_path: Path = Path("data/videos.jsonl"),
) -> None:
    """finalize() 的零配额版: 数据源换成 yt-dlp, 不调 Data API.

    跟 finalize() 输出同构 (VideoRecord → videos.jsonl). 配额耗尽时用这个.
    stage A 的候选 (video_candidates.jsonl) 仍来自 Data API search — 但那只 ~4k units,
    真正费的详情+评论这步改 yt-dlp, 故整体配额压力几乎归零.
    """
    cands_by_cat: dict[str, list[dict]] = {}
    cands_by_key: dict[tuple[str, str], dict] = {}
    for line in candidates_path.open(encoding="utf-8"):
        c = json.loads(line)
        cands_by_cat.setdefault(c["category"], []).append(c)
        cands_by_key[(c["category"], c["youtube_id"])] = c

    picks = _load_picks(picks_path, cands_by_cat)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for cat_str, yt_id in picks:
            base = cands_by_key.get((cat_str, yt_id))
            if base is None:
                print(f"  [WARN] ({cat_str}, {yt_id}) 不在候选, 跳过")
                continue
            meta = fetch_metadata_ytdlp(yt_id, n_comments=10)
            if meta is None:
                continue
            n += 1
            video = VideoRecord(
                video_id=f"V{n:02d}",
                youtube_id=yt_id,
                youtube_url=base["youtube_url"],
                channel=meta["channel"] or base["channel"],
                category=Category(cat_str),
                category_id=CATEGORY_ID[Category(cat_str)],
                publish_date=meta["publish_date"] or base["publish_date"],
                view_count=meta["view_count"] or base["view_count"],
                duration_sec=meta["duration_sec"] or base["duration_sec"],
                language=meta["language"] or base["language"],
                video_title=meta["title"],
                video_description=meta["description"],
                video_tags=meta["tags"],
                top_comments=meta["top_comments"],
            )
            f.write(video.model_dump_json() + "\n")
            print(f"  [{n:02d}] {video.video_id} [{cat_str}] {video.channel} — {video.video_title[:45]}")

    print(f"\n[OK] {n} 视频 (yt-dlp 零配额) → {out_path}")


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Day 8 curate 32 videos (8 官方分类 × 4)")
    parser.add_argument("stage", choices=["a", "b"],
                        help="a = 各类搜候选, b = 各类取 top4 拉 metadata")
    parser.add_argument("--min-sec", type=int, default=DEFAULT_DURATION_MIN_SEC,
                        help=f"时长下限秒 (默认 {DEFAULT_DURATION_MIN_SEC}=>3min 排 Shorts; 候选不足调 120/90)")
    parser.add_argument("--max-sec", type=int, default=DEFAULT_DURATION_MAX_SEC,
                        help=f"时长上限秒 (默认 {DEFAULT_DURATION_MAX_SEC}=8min)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help=f"最近发布窗口天数 (默认 {DEFAULT_WINDOW_DAYS})")
    parser.add_argument("--source", choices=["api", "ytdlp"], default="api",
                        help="stage b 的 metadata 源: api=Data API (耗配额) / ytdlp=零配额兜底")
    args = parser.parse_args()

    if args.stage == "a":
        stage_a_search_candidates(
            window_days=args.window_days, min_sec=args.min_sec, max_sec=args.max_sec
        )
    elif args.source == "ytdlp":
        finalize_ytdlp()
    else:
        finalize()
