"""查火山引擎账户余额 (跑 judge / baseline 前确认够不够, 免得中途欠费断).

用 .env 的 VOLC_ACCESS_KEY/SECRET (账号级 AK/SK, 当初传 TOS 用的) 调 billing OpenAPI
QueryBalanceAcct。Doubao 推理 API 本身不返回余额, 只能走财务 OpenAPI。

CLI: PYTHONPATH=src python src/check_balance.py [--need-usd 10]
"""
from __future__ import annotations
import os, json, argparse
from pathlib import Path

from volcengine.billing.BillingService import BillingService
from volcengine.ApiInfo import ApiInfo
from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
USD_CNY = 7.2   # 粗汇率, 仅用于"够不够"判断


def query_balance() -> dict:
    svc = BillingService()
    svc.set_ak(os.environ["VOLC_ACCESS_KEY"])
    svc.set_sk(os.environ["VOLC_SECRET_KEY"])
    # 源码只预置账单类 Action, 余额类手动注册
    svc.api_info["QueryBalanceAcct"] = ApiInfo(
        "GET", "/", {"Action": "QueryBalanceAcct", "Version": "2022-01-01"}, {}, {})
    return json.loads(svc.get("QueryBalanceAcct", {}))["Result"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--need-usd", type=float, default=10.0, help="预估要花多少美金 (默认 baseline 的 $10)")
    a = ap.parse_args()
    r = query_balance()
    avail = float(r["AvailableBalance"])
    print(f"火山账户 {r['AccountID']} | 可用 ¥{avail:.2f} "
          f"(现金 ¥{r['CashBalance']} · 欠费 ¥{r['ArrearsBalance']} · 冻结 ¥{r['FreezeAmount']})")
    need_cny = a.need_usd * USD_CNY
    gap = need_cny - avail
    verdict = "✅ 够" if gap <= 0 else f"❌ 缺 ~¥{gap:.0f} (建议充到 ¥{need_cny*1.2:.0f} 留余量)"
    print(f"预估花费 ${a.need_usd:.0f} ≈ ¥{need_cny:.0f} → {verdict}")


if __name__ == "__main__":
    main()
