#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🛡️ SOLIDUS 데이터 건전성 게이트 (Anti-Freeze Gate)  v1.0.0
────────────────────────────────────────────────────────────────
배경:
  2026-05-23 이후 79일간 온체인 9개 지표가 동일값으로 고착됐으나 아무도 몰랐다.
  기존 P1-2 게이트는 "결손(NaN)"만 검사했고, ffill 로 채워진 화석값은
  값이 존재하므로 정상 통과했다.
  → 결손 검사만으로는 부족하다. **고착(동일값 연속) 검사가 필요하다.**

3중 검사:
  A. 신선도  — CSV 최종일이 오늘로부터 MAX_LAG_DAYS 초과로 뒤처졌는가
  B. 고착    — 컬럼별 말단 동일값 연속일수가 임계를 넘었는가   ★ 신규
  C. 결손    — 필수 컬럼 최신행이 NaN 인가 (기존 P1-2 계승)

사용:
  python scripts/health_gate.py            # 판정 + latest.json 기록, 항상 exit 0
  python scripts/health_gate.py --enforce  # 위와 동일 + CRITICAL 시 exit 1

설계 원칙:
  데이터 파일을 수정하지 않는다. latest.json 의 data_health 블록만 기록한다.
  판정과 차단을 분리해, 데이터는 보존하면서 소비자 연쇄만 끊는다.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "solidus_daily.csv"
LATEST_PATH = ROOT / "latest.json"

ENFORCE = "--enforce" in sys.argv
TODAY = datetime.now(timezone.utc).date()

# ── A. 신선도 ──
MAX_LAG_DAYS = 3          # BTC는 24/7 — 완결일봉 기준 D-1 이 정상, 3일까지 허용

# ── C. 결손 (기존 P1-2 필수 컬럼 계승) ──
REQUIRED_COLS = ["BTC_Close", "BTC_100DMA", "BTC_RSI_14", "CapMVRVCur"]

# ── B. 고착 임계 (말단 동일값 연속 허용일수) ──
# CRITICAL 그룹: 매일 변동해야 정상인 지표. 고착 = 소스 사망 신호.
FREEZE_CRITICAL = {
    "BTC_Close": 3,
    "CapMVRVCur": 5, "CapMrktCurUSD": 5, "AdrActCnt": 5, "AdrBalCnt": 5,
    "HashRate": 5, "TxCnt": 5, "SplyCur": 5, "FlowInExUSD": 5, "FlowOutExUSD": 5,
}
# WARN 그룹: 주말·휴일 ffill 또는 주간/월간 발표 주기가 있어 평탄 구간이 정상일 수 있음.
FREEZE_WARN = {
    "VIX": 6, "VVIX": 6, "DXY": 6, "MOVE": 6, "Gold": 6, "SPY": 6, "QQQ": 6,
    "TLT": 6, "HYG": 6, "USD_KRW": 6, "WTI": 6, "Brent": 6, "FearGreed": 6,
    "DGS10": 8, "DGS2": 8, "FVX_5Y": 8, "IRX_3M": 8,
    "OAS_HY": 8, "OAS_IG": 8, "T5YIE": 8, "T10YIE": 8, "DFII10": 8,
    "WTREGEN": 8, "RRPONTSYD": 8,
    "NFCI": 14, "ICSA": 14, "WALCL": 14,
    "DFF": 60, "UMCSENT": 45, "UNRATE": 45, "CPI": 45, "PPI": 45, "INDPRO": 45,
}

report = {
    "gate_version": VERSION,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "status": "OK",
    "critical": [],
    "warn": [],
    "info": [],
}


def tail_run_length(s: pd.Series) -> int:
    """말단에서 동일값이 몇 행 연속되는지. 전부 NaN 이면 -1."""
    v = s.values
    if len(v) == 0:
        return -1
    last = v[-1]
    if pd.isna(last):
        return -1
    n = 0
    for x in v[::-1]:
        if pd.isna(x):
            break
        # 부동소수 동일성은 원본 표기 그대로 비교 (ffill 은 완전 동일값을 만든다)
        if x == last:
            n += 1
        else:
            break
    return n


print("=" * 66)
print(f"🛡️ SOLIDUS 데이터 건전성 게이트 v{VERSION}"
      f"{'  [ENFORCE]' if ENFORCE else '  [기록 모드]'}")
print(f"   기준일(UTC): {TODAY}")
print("=" * 66)

if not CSV_PATH.exists():
    print("🔴 CRITICAL: CSV 파일 없음")
    report["status"] = "CRITICAL"
    report["critical"].append({"check": "file", "detail": "solidus_daily.csv 없음"})
    sys.exit(1 if ENFORCE else 0)

df = pd.read_csv(CSV_PATH, parse_dates=["Date"])
df = df.sort_values("Date").reset_index(drop=True)
last_date = df["Date"].max().date()
lag = (TODAY - last_date).days
report["last_date"] = str(last_date)
report["lag_days"] = lag
report["rows"] = len(df)

# ══════════════════════════════════════════════════════════════
# A. 신선도
# ══════════════════════════════════════════════════════════════
print(f"\n[A] 신선도 — 최종일 {last_date} (지연 {lag}일 / 허용 {MAX_LAG_DAYS}일)")
if lag > MAX_LAG_DAYS:
    print(f"  🔴 CRITICAL: 데이터가 {lag}일 뒤처짐")
    report["critical"].append({"check": "freshness", "lag_days": lag,
                               "limit": MAX_LAG_DAYS})
elif lag < 0:
    # 미래 날짜 = 미완결 봉 유입 또는 소스 이상. 정상적으로 발생할 수 없다.
    print(f"  🔴 CRITICAL: 최종일이 미래({-lag}일 앞) — 미완결 봉 유입 의심")
    report["critical"].append({"check": "future_date", "lag_days": lag})
else:
    print("  ✅ 통과")

# ══════════════════════════════════════════════════════════════
# C. 결손 (기존 P1-2)
# ══════════════════════════════════════════════════════════════
print("\n[C] 결손 — 필수 컬럼 최신행")
last_row = df.iloc[-1]
for c in REQUIRED_COLS:
    if c not in df.columns:
        print(f"  🔴 CRITICAL: {c} 컬럼 자체 없음")
        report["critical"].append({"check": "missing_column", "column": c})
    elif pd.isna(last_row[c]):
        print(f"  🔴 CRITICAL: {c} 최신행 결손(NaN)")
        report["critical"].append({"check": "null_required", "column": c})
    else:
        print(f"  ✅ {c} = {last_row[c]}")

# ══════════════════════════════════════════════════════════════
# B. 고착 (★ 본 게이트의 핵심 — 79일 침묵의 재발 차단)
# ══════════════════════════════════════════════════════════════
print("\n[B] 고착 — 말단 동일값 연속일수")

def scan(limits, severity):
    hits = []
    for col, limit in limits.items():
        if col not in df.columns:
            continue
        s = df[col]
        if s.notna().sum() == 0:
            report["info"].append({"column": col, "note": "전 구간 미수집"})
            continue
        run = tail_run_length(s)
        if run == -1:
            # 최신행이 NaN — 필수 컬럼이면 [C]에서 이미 CRITICAL
            report["info"].append({"column": col, "note": "최신행 NaN"})
            continue
        if run > limit:
            frozen_since = str(df["Date"].iloc[len(df) - run].date())
            hits.append({"column": col, "run_days": run, "limit": limit,
                         "frozen_since": frozen_since,
                         "value": float(s.iloc[-1]) if np.isfinite(s.iloc[-1]) else None})
    for h in sorted(hits, key=lambda x: -x["run_days"]):
        mark = "🔴 CRITICAL" if severity == "critical" else "🟠 WARN"
        print(f"  {mark}: {h['column']:<16} {h['run_days']}일 연속 동일 "
              f"(허용 {h['limit']}일, {h['frozen_since']}부터) = {h['value']}")
        h["check"] = "frozen"
        report[severity].append(h)
    return hits

crit_hits = scan(FREEZE_CRITICAL, "critical")
warn_hits = scan(FREEZE_WARN, "warn")
if not crit_hits and not warn_hits:
    print("  ✅ 고착 없음")

# ══════════════════════════════════════════════════════════════
# 판정 + latest.json 기록
# ══════════════════════════════════════════════════════════════
if report["critical"]:
    report["status"] = "CRITICAL"
elif report["warn"]:
    report["status"] = "WARN"

try:
    latest = json.loads(LATEST_PATH.read_text(encoding="utf-8")) if LATEST_PATH.exists() else {}
    latest["data_health"] = report
    LATEST_PATH.write_text(json.dumps(latest, indent=2, ensure_ascii=False, default=str),
                           encoding="utf-8")
    print("\n📝 latest.json → data_health 기록 완료")
except Exception as e:
    print(f"\n⚠️ latest.json 기록 실패: {str(e)[:100]}")

print("=" * 66)
print(f"판정: {report['status']}  "
      f"(CRITICAL {len(report['critical'])} / WARN {len(report['warn'])})")
print("=" * 66)

if ENFORCE and report["status"] == "CRITICAL":
    print("\n🚨 CRITICAL — 소비자 연쇄를 차단한다.")
    print("   데이터 파일은 보존되며, 기존 브리핑 페이지가 유지된다.")
    print("   원인 해소 후 워크플로를 재실행할 것.")
    sys.exit(1)

sys.exit(0)
