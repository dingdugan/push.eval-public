"""push.eval 样本量成本模型 (甲方案: 8 官方类 × N/类, 最近窗口, medium≤8min).

所有假设显式标注, 可挑刺. 不是生产代码, 是决策辅助.
"""

# 固定参数
CATS = 8
PERSONAS = 13
REPS = 3

# 模型 (input/output 美金 per 1M token, design-doc § 4.2 pinned)
MODELS = {
    "Gemini3.5Flash": (1.50, 9.00),
    "GPT-5.5": (5.00, 30.00),
    "Kimi-K2.6": (1.00, 3.00),
    "DeepSeek-V4Pro": (0.145, 3.48),
}
VAR_D_MODELS = {"Gemini3.5Flash", "Kimi-K2.6"}  # 只有这俩支持原生视频

# 每次 generator 调用的 token 估计 (假设, 视频均 ~5min)
GEN_IN = {"A": 1500, "B": 5000, "C": 3000, "D": 81500}
GEN_OUT = 120

# judge: 每条 output 由 2 judge 评, 每 judge 看完整视频
JUDGE_IN = 86000
JUDGE_OUT = 200
JUDGE_IN_PRICE = 0.99    # Gemini/Doubao/Kimi 池均价 input
JUDGE_OUT_PRICE = 4.79
JUDGE_CACHE_FACTOR = 4   # 视频缓存后 input 便宜 ~4x

HUMAN_PCT = 0.025
HUMAN_MIN_PER_ITEM = 3
PREP_MIN_PER_VIDEO = 3


def gen_cost(cases, vars_list):
    total, calls = 0.0, 0
    for v in vars_list:
        models = VAR_D_MODELS if v == "D" else MODELS.keys()
        for m in models:
            inp, outp = MODELS[m]
            per = (GEN_IN[v] * inp + GEN_OUT * outp) / 1e6
            n = cases * REPS
            total += per * n
            calls += n
    return total, calls


def judge_cost(full_outputs, cached):
    calls = full_outputs * 2
    if cached:
        per = (80000 * (JUDGE_IN_PRICE / JUDGE_CACHE_FACTOR)
               + 6000 * JUDGE_IN_PRICE + JUDGE_OUT * JUDGE_OUT_PRICE) / 1e6
    else:
        per = (JUDGE_IN * JUDGE_IN_PRICE + JUDGE_OUT * JUDGE_OUT_PRICE) / 1e6
    return per * calls, calls


def main():
    print(f"{'N':>4} {'/类':>3} {'case':>5} | {'W2跑$':>6} {'W2调用':>7} | "
          f"{'全跑$':>6} {'全调用':>7} | {'judge无缓存$':>11} {'judge缓存$':>9} | "
          f"{'预处h':>5} {'抽检条':>6} {'抽检h':>5}")
    print("=" * 110)
    for per_cat in [3, 4, 6, 10]:
        N = CATS * per_cat
        cases = N * PERSONAS
        w2c, w2n = gen_cost(cases, ["A", "B"])
        fc, fn = gen_cost(cases, ["A", "B", "C", "D"])
        jnc, _ = judge_cost(fn, cached=False)
        jc, _ = judge_cost(fn, cached=True)
        prep_h = N * PREP_MIN_PER_VIDEO / 60
        items = HUMAN_PCT * fn
        human_h = items * HUMAN_MIN_PER_ITEM / 60
        print(f"{N:>4} {per_cat:>3} {cases:>5} | "
              f"${w2c:>5.0f} {w2n:>7} | "
              f"${fc:>5.0f} {fn:>7} | "
              f"${jnc:>10.0f} ${jc:>8.0f} | "
              f"{prep_h:>4.1f}h {int(items):>6} {human_h:>4.0f}h")
    print("=" * 110)


if __name__ == "__main__":
    main()
