"""W3: Doubao (火山方舟 Ark) 连通性 probe — 第 1 步只验 key + 文本通路 + 发现可用 model id.

背景: design-doc 写 "Doubao Seed 2.0 Pro" 但实测查不到该模型 (W1 未来化嫌疑); 真实可用 =
doubao-seed-1.6 / 1.6-vision. judge 需视频理解 → vision 变体. 但 model id 必须实测确认,
不凭记忆/不凭 doc 编 (grounding 纪律).

本脚本只做连通性: (1) 列 key 能访问的 model; (2) 候选 model id 各跑一句文本, 看哪个 auth 通.
视频通路 + judge JSON 留第 2 步 (确认 model 通了再验视频).

CLI: PYTHONPATH=src .venv/bin/python src/doubao_probe.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# 候选 (实测 model id 用连字符; models.list 已证目录里这些都在, 验哪些"已开通"可调用).
# 优先 doc 指定的 Seed 2.0 Pro; 再退 vision 专用 / 2.0 其他档 / 1.6 系.
CANDIDATES = [
    "doubao-seed-2-0-pro-260215",       # doc 指定 (Doubao Seed 2.0 Pro), 最强
    "doubao-seed-1-6-vision-250815",    # 视频专用 vision 变体
    "doubao-seed-2-0-lite-260428",
    "doubao-seed-2-0-mini-260428",
    "doubao-seed-1-6-251015",
    "doubao-seed-1-6-250615",
]


def main():
    key = os.environ.get("DOUBAO_API_KEY", "")
    print(f"key 前缀: {key[:8]}... (len={len(key)})")
    client = OpenAI(api_key=key, base_url=BASE_URL)

    print("\n[1] 列出 key 可访问的 model (client.models.list) ...")
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f"    可访问 {len(ids)} 个: {ids}")
    except Exception as ex:
        print(f"    models.list 不支持/失败: {type(ex).__name__}: {str(ex)[:200]}")

    print("\n[2] 候选 model 各跑一句文本验 auth ...")
    ok_models = []
    for m in CANDIDATES:
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=2000,
            )
            txt = resp.choices[0].message.content
            mv = getattr(resp, "model", m)
            print(f"    ✓ {m:34} → {txt!r} (resp.model={mv}, tin={resp.usage.prompt_tokens})")
            ok_models.append(m)
        except Exception as ex:
            print(f"    ✗ {m:34} → {type(ex).__name__}: {str(ex)[:160]}")

    print(f"\n结论: auth 通的 model = {ok_models or '无 (key/endpoint/model 有问题, 看上面报错)'}")
    if ok_models:
        print("→ 下一步: 对通的 model 验视频通路 + judge JSON (doubao_judge_probe).")


if __name__ == "__main__":
    main()
