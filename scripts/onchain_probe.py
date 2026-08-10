#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔬 SOLIDUS 온체인 대체 소스 실측 프로브 v1.0.0
────────────────────────────────────────────────────────────────
목적:
  기존 온체인 소스(coinmetrics/data GitHub CSV)가 2026-05-24 커밋을 마지막으로
  영구 중단됨. SOLIDUS가 필요로 하는 9개 온체인 지표를 공급할 수 있는
  대체 소스 후보들을 GitHub Actions 러너(무제한 인터넷)에서 실측한다.

실행:
  python scripts/onchain_probe.py

출력:
  - 콘솔: 소스별 · 지표별 최신일 / 값 / 지연일수 매트릭스
  - 파일: probe_result.json  (Claude 전달용 요약)

주의: 이 스크립트는 읽기 전용이다. solidus_daily.csv 를 절대 건드리지 않는다.
"""
import json
import sys
import time
import traceback
from datetime import datetime, timezone, date

import requests

TODAY = datetime.now(timezone.utc).date()
UA = {"User-Agent": "SOLIDUS-onchain-probe/1.0 (+github-actions)"}
TIMEOUT = 45

# SOLIDUS 엔진이 실제로 소비하는 9개 온체인 지표
REQUIRED = [
    "CapMVRVCur",      # 🔴 최우선 — WINTER 판정 핵심 입력
    "CapMrktCurUSD",   # 시가총액
    "AdrActCnt",       # 활성 주소수
    "AdrBalCnt",       # 잔고 보유 주소수
    "HashRate",        # 해시레이트 (리본 계산)
    "TxCnt",           # 트랜잭션 수
    "SplyCur",         # 유통 공급량
    "FlowInExUSD",     # 거래소 유입
    "FlowOutExUSD",    # 거래소 유출
]

RESULT = {
    "probe_version": "1.0.0",
    "run_utc": datetime.now(timezone.utc).isoformat(),
    "today_utc": str(TODAY),
    "sources": {},
}


def lag_days(d):
    """최신일이 오늘로부터 며칠 뒤처졌는지."""
    if d is None:
        return None
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except Exception:
            return None
    return (TODAY - d).days


def record(name, ok, detail):
    RESULT["sources"][name] = {"ok": ok, **detail}
    mark = "✅" if ok else "❌"
    print(f"\n{mark} [{name}]")
    for k, v in detail.items():
        if k == "metrics":
            for mk, mv in v.items():
                print(f"     · {mk:<16} 최신={mv.get('latest_date')}  "
                      f"지연={mv.get('lag_days')}일  값={mv.get('latest_value')}")
        else:
            print(f"   {k}: {v}")


def safe(name, fn):
    """개별 소스 실패가 전체 프로브를 죽이지 않도록 격리."""
    t0 = time.time()
    try:
        fn()
    except Exception as e:
        record(name, False, {
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "elapsed_s": round(time.time() - t0, 1),
        })
        print("   " + traceback.format_exc(limit=1).splitlines()[-1][:160])


# ══════════════════════════════════════════════════════════════
# [S0] 대조군 — 현행 소스 (죽었음을 재확인)
# ══════════════════════════════════════════════════════════════
def s0_current_github_csv():
    import pandas as pd
    from io import StringIO
    url = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
    r = requests.get(url, timeout=180, headers=UA)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text), low_memory=False)
    df["time"] = pd.to_datetime(df["time"]).dt.date
    metrics = {}
    for m in REQUIRED:
        if m in df.columns:
            s = df[["time", m]].dropna()
            ld = s["time"].max() if len(s) else None
            metrics[m] = {"latest_date": str(ld), "lag_days": lag_days(ld),
                          "latest_value": float(s[m].iloc[-1]) if len(s) else None}
        else:
            metrics[m] = {"latest_date": None, "lag_days": None, "latest_value": None,
                          "note": "컬럼 없음"}
    record("S0_coinmetrics_github_csv(대조군)", True,
           {"url": url, "rows": len(df), "metrics": metrics})


# ══════════════════════════════════════════════════════════════
# [S1] CoinMetrics Community API v4 — 동일 지표명, 무료 · 키 불필요
#      ★ 최유력 후보 (drop-in 교체 가능성)
# ══════════════════════════════════════════════════════════════
def s1_cm_community_api():
    base = "https://community-api.coinmetrics.io/v4"

    # (1) 카탈로그: 커뮤니티 티어에서 어떤 지표가 열려있는지
    cat_url = f"{base}/catalog-v2/asset-metrics?assets=btc&page_size=10000"
    rc = requests.get(cat_url, timeout=TIMEOUT, headers=UA)
    available = set()
    cat_status = rc.status_code
    if rc.status_code == 200:
        try:
            for entry in rc.json().get("data", []):
                for mm in entry.get("metrics", []):
                    available.add(mm.get("metric"))
        except Exception:
            pass

    # (2) 실제 시계열 요청 — 지표를 하나씩 개별 요청 (일괄 요청 시 1개만 막혀도 전체 400)
    metrics = {}
    for m in REQUIRED + ["CapRealUSD"]:   # CapRealUSD = MVRV 자체계산용 대안
        u = (f"{base}/timeseries/asset-metrics?assets=btc&metrics={m}"
             f"&frequency=1d&start_time={TODAY.year}-01-01&page_size=10000")
        try:
            r = requests.get(u, timeout=TIMEOUT, headers=UA)
            if r.status_code != 200:
                metrics[m] = {"http": r.status_code, "latest_date": None,
                              "lag_days": None, "latest_value": None,
                              "body": r.text[:120]}
                continue
            rows = r.json().get("data", [])
            rows = [x for x in rows if x.get(m) not in (None, "")]
            if not rows:
                metrics[m] = {"http": 200, "latest_date": None, "lag_days": None,
                              "latest_value": None, "note": "데이터 0행"}
                continue
            last = rows[-1]
            ld = last["time"][:10]
            metrics[m] = {"http": 200, "latest_date": ld, "lag_days": lag_days(ld),
                          "latest_value": float(last[m]), "rows": len(rows)}
        except Exception as e:
            metrics[m] = {"http": None, "latest_date": None, "lag_days": None,
                          "latest_value": None, "error": str(e)[:120]}

    ok = any(v.get("latest_date") for v in metrics.values())
    record("S1_coinmetrics_community_api_v4", ok, {
        "base": base,
        "catalog_http": cat_status,
        "catalog_metric_count": len(available),
        "required_in_catalog": {m: (m in available) for m in REQUIRED},
        "metrics": metrics,
    })


# ══════════════════════════════════════════════════════════════
# [S2] blockchain.info charts API — HashRate / 주소 / TxCnt / 시총 / 공급량
# ══════════════════════════════════════════════════════════════
def s2_blockchain_info():
    charts = {
        "HashRate": "hash-rate",
        "AdrActCnt": "n-unique-addresses",
        "TxCnt": "n-transactions",
        "CapMrktCurUSD": "market-cap",
        "SplyCur": "total-bitcoins",
    }
    metrics = {}
    for label, chart in charts.items():
        u = f"https://api.blockchain.info/charts/{chart}?timespan=30days&format=json&cors=true"
        try:
            r = requests.get(u, timeout=TIMEOUT, headers=UA)
            if r.status_code != 200:
                metrics[label] = {"http": r.status_code, "latest_date": None, "lag_days": None}
                continue
            vals = r.json().get("values", [])
            if not vals:
                metrics[label] = {"http": 200, "latest_date": None, "lag_days": None,
                                  "note": "values 0"}
                continue
            last = vals[-1]
            ld = datetime.fromtimestamp(last["x"], tz=timezone.utc).date()
            metrics[label] = {"http": 200, "latest_date": str(ld),
                              "lag_days": lag_days(ld), "latest_value": last["y"],
                              "chart": chart}
        except Exception as e:
            metrics[label] = {"http": None, "error": str(e)[:120],
                              "latest_date": None, "lag_days": None}
    ok = any(v.get("latest_date") for v in metrics.values())
    record("S2_blockchain_info_charts", ok,
           {"note": "MVRV 미제공 — 보조 소스", "metrics": metrics})


# ══════════════════════════════════════════════════════════════
# [S3] bitcoin-data.com — MVRV / NUPL / Realized Cap 직접 제공 후보
# ══════════════════════════════════════════════════════════════
def s3_bitcoin_data_com():
    eps = {
        "CapMVRVCur": "https://bitcoin-data.com/v1/mvrv",
        "NUPL":       "https://bitcoin-data.com/v1/nupl",
        "CapRealUSD": "https://bitcoin-data.com/v1/realized-cap",
    }
    metrics = {}
    for label, u in eps.items():
        try:
            r = requests.get(u, timeout=TIMEOUT, headers=UA)
            if r.status_code != 200:
                metrics[label] = {"http": r.status_code, "latest_date": None,
                                  "lag_days": None, "body": r.text[:120]}
                continue
            js = r.json()
            rows = js if isinstance(js, list) else js.get("data", [])
            if not rows:
                metrics[label] = {"http": 200, "latest_date": None, "lag_days": None}
                continue
            last = rows[-1]
            dk = next((k for k in last if "date" in k.lower() or k.lower() == "d"), None)
            vk = next((k for k in last if k != dk), None)
            ld = str(last.get(dk))[:10] if dk else None
            metrics[label] = {"http": 200, "latest_date": ld, "lag_days": lag_days(ld),
                              "latest_value": last.get(vk), "keys": list(last.keys())[:6]}
        except Exception as e:
            metrics[label] = {"http": None, "error": str(e)[:120],
                              "latest_date": None, "lag_days": None}
    ok = any(v.get("latest_date") for v in metrics.values())
    record("S3_bitcoin_data_com", ok, {"note": "MVRV 직접 제공 후보", "metrics": metrics})


# ══════════════════════════════════════════════════════════════
# [S4] mempool.space — HashRate 백업
# ══════════════════════════════════════════════════════════════
def s4_mempool_space():
    u = "https://mempool.space/api/v1/mining/hashrate/1m"
    r = requests.get(u, timeout=TIMEOUT, headers=UA)
    metrics = {}
    if r.status_code == 200:
        js = r.json()
        hr = js.get("hashrates", [])
        if hr:
            last = hr[-1]
            ld = datetime.fromtimestamp(last["timestamp"], tz=timezone.utc).date()
            metrics["HashRate"] = {"http": 200, "latest_date": str(ld),
                                   "lag_days": lag_days(ld),
                                   "latest_value": last.get("avgHashrate")}
    else:
        metrics["HashRate"] = {"http": r.status_code, "latest_date": None, "lag_days": None}
    record("S4_mempool_space", bool(metrics.get("HashRate", {}).get("latest_date")),
           {"note": "HashRate 전용 백업", "metrics": metrics})


# ══════════════════════════════════════════════════════════════
# [S5] blockchair — 통합 통계 백업
# ══════════════════════════════════════════════════════════════
def s5_blockchair():
    u = "https://api.blockchair.com/bitcoin/stats"
    r = requests.get(u, timeout=TIMEOUT, headers=UA)
    d = r.json().get("data", {}) if r.status_code == 200 else {}
    record("S5_blockchair_stats", r.status_code == 200, {
        "http": r.status_code,
        "metrics": {
            "HashRate":      {"latest_date": str(TODAY), "lag_days": 0,
                              "latest_value": d.get("hashrate_24h")},
            "TxCnt":         {"latest_date": str(TODAY), "lag_days": 0,
                              "latest_value": d.get("transactions_24h")},
            "CapMrktCurUSD": {"latest_date": str(TODAY), "lag_days": 0,
                              "latest_value": d.get("market_cap_usd")},
            "SplyCur":       {"latest_date": str(TODAY), "lag_days": 0,
                              "latest_value": d.get("circulation")},
        },
        "note": "스냅샷형(시계열 아님) — 최후 백업",
    })


# ══════════════════════════════════════════════════════════════
# [S6] CoinGecko — 시총 시계열 백업
# ══════════════════════════════════════════════════════════════
def s6_coingecko():
    u = ("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
         "?vs_currency=usd&days=30&interval=daily")
    r = requests.get(u, timeout=TIMEOUT, headers=UA)
    metrics = {}
    if r.status_code == 200:
        mc = r.json().get("market_caps", [])
        if mc:
            last = mc[-1]
            ld = datetime.fromtimestamp(last[0] / 1000, tz=timezone.utc).date()
            metrics["CapMrktCurUSD"] = {"http": 200, "latest_date": str(ld),
                                        "lag_days": lag_days(ld), "latest_value": last[1]}
    else:
        metrics["CapMrktCurUSD"] = {"http": r.status_code, "latest_date": None,
                                    "lag_days": None, "body": r.text[:120]}
    record("S6_coingecko", bool(metrics.get("CapMrktCurUSD", {}).get("latest_date")),
           {"note": "시총 백업", "metrics": metrics})


# ══════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════
print("=" * 68)
print("🔬 SOLIDUS 온체인 대체 소스 실측 프로브 v1.0.0")
print(f"   실행(UTC): {RESULT['run_utc']}")
print(f"   기준일   : {TODAY}")
print(f"   필수지표 : {len(REQUIRED)}종")
print("=" * 68)

safe("S0_coinmetrics_github_csv(대조군)", s0_current_github_csv)
safe("S1_coinmetrics_community_api_v4", s1_cm_community_api)
safe("S2_blockchain_info_charts", s2_blockchain_info)
safe("S3_bitcoin_data_com", s3_bitcoin_data_com)
safe("S4_mempool_space", s4_mempool_space)
safe("S5_blockchair_stats", s5_blockchair)
safe("S6_coingecko", s6_coingecko)

# ── 종합 판정: 지표별로 "지연 3일 이내"인 소스가 하나라도 있는가 ──
print("\n" + "=" * 68)
print("📊 종합 — 지표별 사용가능 소스 (지연 ≤ 3일)")
print("=" * 68)
coverage = {}
for m in REQUIRED:
    winners = []
    for sname, sdata in RESULT["sources"].items():
        if sname.startswith("S0"):
            continue  # 대조군 제외
        mv = (sdata.get("metrics") or {}).get(m)
        if mv and mv.get("lag_days") is not None and mv["lag_days"] <= 3:
            winners.append(f"{sname.split('_')[0]}({mv['lag_days']}d)")
    coverage[m] = winners
    flag = "✅" if winners else "🔴"
    print(f"  {flag} {m:<16} → {', '.join(winners) if winners else '사용가능 소스 없음'}")

RESULT["coverage"] = coverage
RESULT["mvrv_covered"] = bool(coverage.get("CapMVRVCur"))
RESULT["uncovered"] = [m for m, w in coverage.items() if not w]

with open("probe_result.json", "w", encoding="utf-8") as f:
    json.dump(RESULT, f, indent=2, ensure_ascii=False, default=str)

print("\n" + "=" * 68)
print(f"🔴 MVRV(CapMVRVCur) 커버: {'예' if RESULT['mvrv_covered'] else '아니오'}")
print(f"미커버 지표: {RESULT['uncovered'] if RESULT['uncovered'] else '없음'}")
print("✅ probe_result.json 저장 완료 — 이 파일 + 위 로그 전문을 Claude에 전달")
print("=" * 68)
