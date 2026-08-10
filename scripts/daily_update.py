#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💰 SOLIDUS Daily Data Update  v2.0.0
매일 GHA에서 실행 → solidus_daily.csv 갱신 + latest.json 생성

═══════════════════════════════════════════════════════════════════
v2.0.0 변경 (2026-08-10) — 온체인 79일 정지 사고 근본 복구
─────────────────────────────────────────────────────────────────
[C1] 온체인 소스 교체 (근본)
     기존: raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv
           → 2026-05-24 커밋을 마지막으로 업스트림 미러 영구 폐기.
           79일간 ffill로 화석값이 살아있는 값으로 위장됨.
     신규: CoinMetrics Community API v4 (무료·키 불필요·지표명 동일)
           + blockchain.info 보조 폴백 (단위 정합 확인된 4종만)

[C2] 온체인 자가치유 재수집
     매 실행마다 최근 ONCHAIN_LOOKBACK_DAYS(400일)를 재수집하여 덮어쓴다.
     → 2026-05-24~08-09 화석 구간이 첫 실행에서 자동 교정된다.
     → 향후 어떤 소스 장애든 400일 이내면 복구 시 자동 메움.

[C3] bare except 제거 → 온체인 수집 실패는 전량 loud 기록
     ONCHAIN_STATUS 에 소스별 성패를 남기고 latest.json 에 노출.

[C4] 온체인 ffill 상한 (FFILL_LIMIT_FROM 이후 구간에만 적용)
     소스가 다시 죽으면 ONCHAIN_FFILL_LIMIT(7일) 뒤부터 NaN 이 되어
     P1-2 결손 게이트가 즉시 발동한다.
     ★ 이력 구간(FFILL_LIMIT_FROM 이전)은 무제한 ffill 유지 —
       백테스트 정합을 깨지 않기 위함.

[C5] 미완결 일봉 배제
     yfinance end 는 배타적이라 D-1 이 누락되는 경우가 있었다.
     end 를 D+1 로 넓혀 D-1 을 확보하고, 진행 중인 당일 UTC 봉은 명시적으로 제거.
     → 항상 "완결된 일봉만" 수록.
═══════════════════════════════════════════════════════════════════
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

VERSION = "2.0.0"

# ═══════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CSV_PATH = DATA_DIR / "solidus_daily.csv"
LATEST_PATH = ROOT / "latest.json"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# 최초 실행 시 이 날짜부터 수집
BACKFILL_START = "2016-01-01"

# ── v2.0.0 신규 상수 ──
CM_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
ONCHAIN_LOOKBACK_DAYS = 400          # 매 실행 시 온체인 재수집 구간 (자가치유 폭)
ONCHAIN_FFILL_LIMIT = 7              # 온체인 ffill 상한 (일)
FFILL_LIMIT_FROM = pd.Timestamp("2026-08-10")  # 이 날짜 이후 행에만 상한 적용
HTTP_UA = {"User-Agent": "SOLIDUS-daily-update/2.0.0 (+github-actions)"}

# SOLIDUS 엔진이 소비하는 온체인 9종 (CoinMetrics 지표명)
ONCHAIN_COLS = [
    "CapMVRVCur", "CapMrktCurUSD", "AdrActCnt", "AdrBalCnt", "HashRate",
    "TxCnt", "SplyCur", "FlowInExUSD", "FlowOutExUSD",
]

# blockchain.info 보조 폴백 — 단위 정합이 실측 확인된 항목만.
# ★ AdrActCnt 는 정의 불일치(unique addresses ≠ active addresses)로 의도적 제외.
# ★ MVRV·거래소 유출입은 대체 불가 → 실패 시 건전성 게이트가 발동한다.
BCI_FALLBACK = {
    "HashRate": "hash-rate",
    "CapMrktCurUSD": "market-cap",
    "SplyCur": "total-bitcoins",
    "TxCnt": "n-transactions",
}

# 온체인 수집 상태 (latest.json 에 노출)
ONCHAIN_STATUS = {"primary": None, "fallback": None, "columns": {}, "errors": []}

NOW_UTC = datetime.now(timezone.utc)
TODAY_UTC = NOW_UTC.date()

print("=" * 66)
print(f"💰 SOLIDUS Daily Data Update v{VERSION}")
print(f"   시각: {NOW_UTC.isoformat()}")
print(f"   FRED: {'✅' if FRED_API_KEY else '❌ 미설정'}")
print("=" * 66)


# ═══════════════════════════════════════════════════════════════
# 1) 기존 데이터 로드 → 수집 시작일 결정
# ═══════════════════════════════════════════════════════════════
if CSV_PATH.exists():
    df_existing = pd.read_csv(CSV_PATH, parse_dates=["Date"])
    last_date = df_existing["Date"].max()
    # 최소 7일 범위 보장 (1~2일 범위에서 Yahoo가 BTC 미반환 방지)
    fetch_start = (last_date - timedelta(days=6)).strftime("%Y-%m-%d")
    print(f"📂 기존 데이터: {len(df_existing)}행 (~{last_date.date()})")
    print("   (최소 7일 오버랩 수집 → 중복은 자동 제거)")
else:
    df_existing = None
    fetch_start = BACKFILL_START
    print(f"📂 기존 데이터 없음 → {BACKFILL_START}부터 전체 수집")

# [C5] end 는 배타적 → D+1 로 넓혀 D-1 확보. 당일 미완결 봉은 뒤에서 제거.
fetch_end = (TODAY_UTC + timedelta(days=1)).strftime("%Y-%m-%d")
print(f"📡 수집 범위: {fetch_start} ~ {fetch_end} (배타적, 당일 미완결 봉 제거 예정)")

# 온체인 수집 시작일 — 자가치유를 위해 항상 400일 이상 소급
if df_existing is None:
    onchain_start = BACKFILL_START
else:
    onchain_start = min(
        pd.Timestamp(fetch_start),
        pd.Timestamp(TODAY_UTC) - timedelta(days=ONCHAIN_LOOKBACK_DAYS),
    ).strftime("%Y-%m-%d")
print(f"🔗 온체인 재수집 시작일: {onchain_start} (자가치유 {ONCHAIN_LOOKBACK_DAYS}일)")


# ═══════════════════════════════════════════════════════════════
# 2) Yahoo Finance
# ═══════════════════════════════════════════════════════════════
print("\n📡 [1/4] Yahoo Finance...")

YAHOO_TICKERS = {
    "BTC-USD":  {"cols": {"Close": "BTC_Close", "Open": "BTC_Open", "High": "BTC_High",
                          "Low": "BTC_Low", "Volume": "BTC_Volume"}},
    "^VIX":     {"cols": {"Close": "VIX"}},
    "^VVIX":    {"cols": {"Close": "VVIX"}},
    "DX-Y.NYB": {"cols": {"Close": "DXY"}},
    "^MOVE":    {"cols": {"Close": "MOVE"}},
    "^TNX":     {"cols": {"Close": "DGS10"}},
    "^FVX":     {"cols": {"Close": "FVX_5Y"}},
    "^IRX":     {"cols": {"Close": "IRX_3M"}},
    "GC=F":     {"cols": {"Close": "Gold"}},
    "CL=F":     {"cols": {"Close": "WTI"}},
    "BZ=F":     {"cols": {"Close": "Brent"}},
    "SPY":      {"cols": {"Close": "SPY"}},
    "QQQ":      {"cols": {"Close": "QQQ"}},
    "TLT":      {"cols": {"Close": "TLT"}},
    "HYG":      {"cols": {"Close": "HYG"}},
    "USDKRW=X": {"cols": {"Close": "USD_KRW"}},
}

yahoo_frames = {}
for ticker, cfg in YAHOO_TICKERS.items():
    try:
        data = yf.download(ticker, start=fetch_start, end=fetch_end,
                           progress=False, auto_adjust=True)
        if len(data) == 0:
            print(f"  ⚠️ {ticker}: 0행")
            continue
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        rename = cfg["cols"]
        cols = [k for k in rename if k in data.columns]
        if not cols:
            continue
        sub = data[cols].rename(columns=rename)
        if isinstance(sub.index, pd.MultiIndex):
            sub.index = sub.index.get_level_values(0)
        sub.index = pd.DatetimeIndex(sub.index)
        sub.index.name = "Date"
        # tz-aware 인덱스 방어 (yfinance 버전에 따라 발생)
        if sub.index.tz is not None:
            sub.index = sub.index.tz_localize(None)
        yahoo_frames[ticker] = sub
        print(f"  ✅ {list(rename.values())[0]}: {len(sub)}행 (~{sub.index.max().date()})")
    except Exception as e:
        print(f"  ❌ {ticker}: {str(e)[:60]}")

df_yahoo = None
for _, frame in yahoo_frames.items():
    if df_yahoo is None:
        df_yahoo = frame.copy()
    else:
        new_cols = [c for c in frame.columns if c not in df_yahoo.columns]
        if new_cols:
            df_yahoo = df_yahoo.join(frame[new_cols], how="outer")

if df_yahoo is not None:
    if isinstance(df_yahoo.columns, pd.MultiIndex):
        df_yahoo.columns = df_yahoo.columns.get_level_values(0)
    df_yahoo.index = pd.DatetimeIndex(df_yahoo.index)
    df_yahoo.index.name = "Date"

    # [C5] 진행 중인 당일 UTC 봉 제거 — 완결된 일봉만 채택
    before = len(df_yahoo)
    df_yahoo = df_yahoo[df_yahoo.index.date < TODAY_UTC]
    if before != len(df_yahoo):
        print(f"  🧹 미완결 당일({TODAY_UTC}) 봉 {before - len(df_yahoo)}행 제거")

    # TNX 자동 스케일 감지
    if "DGS10" in df_yahoo.columns:
        tnx = df_yahoo["DGS10"].dropna()
        if len(tnx) > 0 and tnx.median() > 10:
            df_yahoo["DGS10"] = df_yahoo["DGS10"] / 10


# ═══════════════════════════════════════════════════════════════
# 3) FRED API
# ═══════════════════════════════════════════════════════════════
print("\n📡 [2/4] FRED API...")

FRED_SERIES = {
    "DGS10": "DGS10_FRED", "DGS2": "DGS2", "DFF": "DFF", "DFII10": "DFII10",
    "T5YIE": "T5YIE", "T10YIE": "T10YIE",
    "BAMLH0A0HYM2": "OAS_HY", "BAMLC0A0CM": "OAS_IG",
    "WALCL": "WALCL", "WTREGEN": "WTREGEN", "RRPONTSYD": "RRPONTSYD",
    "NFCI": "NFCI", "ICSA": "ICSA", "UMCSENT": "UMCSENT",
    "UNRATE": "UNRATE", "CPIAUCSL": "CPI", "PPIACO": "PPI", "INDPRO": "INDPRO",
}

df_fred = pd.DataFrame()
if FRED_API_KEY:
    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
        for sid, col in FRED_SERIES.items():
            try:
                s = fred.get_series(sid, observation_start=fetch_start,
                                    observation_end=fetch_end)
                if s is not None and len(s) > 0:
                    df_fred[col] = s
                    print(f"  ✅ {col}: {len(s)}행")
            except Exception as e:
                print(f"  ⚠️ {col}: {str(e)[:50]}")
        df_fred.index = pd.DatetimeIndex(df_fred.index)
        df_fred.index.name = "Date"

        # Net Liquidity
        for c in ["WALCL", "WTREGEN", "RRPONTSYD"]:
            if c in df_fred.columns:
                df_fred[c] = df_fred[c].ffill()
        if all(c in df_fred.columns for c in ["WALCL", "WTREGEN", "RRPONTSYD"]):
            df_fred["Net_Liquidity"] = (df_fred["WALCL"] - df_fred["WTREGEN"]
                                        - df_fred["RRPONTSYD"])
    except Exception as e:
        print(f"  ❌ FRED 실패: {str(e)[:80]}")
else:
    print("  ⏭️ FRED_API_KEY 미설정")


# ═══════════════════════════════════════════════════════════════
# 4) 온체인 — [C1][C2][C3] v2.0.0 전면 교체
# ═══════════════════════════════════════════════════════════════
print("\n📡 [3/4] 온체인 (CoinMetrics Community API v4)...")


def fetch_coinmetrics_api(metrics, start_date):
    """
    CoinMetrics Community API v4 에서 일간 지표 시계열을 가져온다.
    1차: 지표 일괄 요청(1콜). 실패 시 2차: 지표별 개별 요청(콜 N개).
    반환: Date 인덱스 DataFrame (실패 시 빈 DataFrame)
    """
    def _pull(metric_csv):
        rows, url = [], (
            f"{CM_API}?assets=btc&metrics={metric_csv}&frequency=1d"
            f"&start_time={start_date}&page_size=10000"
        )
        guard = 0
        while url and guard < 20:
            r = requests.get(url, timeout=90, headers=HTTP_UA)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
            js = r.json()
            rows.extend(js.get("data", []))
            url = js.get("next_page_url")
            guard += 1
            if url:
                time.sleep(0.4)          # 커뮤니티 티어 레이트리밋 배려
        return rows

    # ── 1차: 일괄 ──
    try:
        rows = _pull(",".join(metrics))
        got = metrics
    except Exception as e:
        print(f"  ⚠️ 일괄 요청 실패 → 개별 요청으로 전환: {str(e)[:90]}")
        ONCHAIN_STATUS["errors"].append(f"batch: {str(e)[:120]}")
        rows, got = [], []
        merged = {}
        for m in metrics:
            try:
                for row in _pull(m):
                    t = row["time"][:10]
                    merged.setdefault(t, {"time": t})[m] = row.get(m)
                got.append(m)
                time.sleep(0.4)
            except Exception as e2:
                print(f"  ❌ {m}: {str(e2)[:80]}")
                ONCHAIN_STATUS["errors"].append(f"{m}: {str(e2)[:120]}")
        rows = list(merged.values())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["time"].str[:10])
    df = df.drop(columns=["time", "asset"], errors="ignore").set_index("Date")
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[[c for c in df.columns if c in metrics]]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_blockchain_info(days=400):
    """blockchain.info charts — 단위 정합이 확인된 4종만 보조 수집."""
    out = {}
    for col, chart in BCI_FALLBACK.items():
        try:
            u = (f"https://api.blockchain.info/charts/{chart}"
                 f"?timespan={days}days&format=json&cors=true")
            r = requests.get(u, timeout=60, headers=HTTP_UA)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            vals = r.json().get("values", [])
            if not vals:
                raise RuntimeError("values 0")
            s = pd.Series(
                {pd.Timestamp(datetime.fromtimestamp(v["x"], tz=timezone.utc).date()):
                 float(v["y"]) for v in vals}
            )
            out[col] = s.sort_index()
            print(f"     · 폴백 {col}: {len(s)}행 (~{s.index.max().date()})")
        except Exception as e:
            print(f"     · 폴백 {col} 실패: {str(e)[:60]}")
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df.index.name = "Date"
    return df


# ── 1차 소스 ──
df_cm = pd.DataFrame()
try:
    df_cm = fetch_coinmetrics_api(ONCHAIN_COLS, onchain_start)
    if len(df_cm) == 0:
        raise RuntimeError("반환 0행")
    ONCHAIN_STATUS["primary"] = "coinmetrics_community_api_v4"
    print(f"  ✅ CM API: {len(df_cm)}행 × {len(df_cm.columns)}컬럼 "
          f"(~{df_cm.index.max().date()})")
    for c in ONCHAIN_COLS:
        if c in df_cm.columns:
            s = df_cm[c].dropna()
            ONCHAIN_STATUS["columns"][c] = {
                "source": "cm_api",
                "latest": str(s.index.max().date()) if len(s) else None,
            }
        else:
            ONCHAIN_STATUS["columns"][c] = {"source": None, "latest": None}
except Exception as e:
    ONCHAIN_STATUS["primary"] = None
    ONCHAIN_STATUS["errors"].append(f"cm_api: {str(e)[:160]}")
    print(f"  🔴 CM API 전면 실패: {str(e)[:120]}")

# ── 2차 소스 (1차에서 빠진 컬럼만 보강) ──
missing = [c for c in BCI_FALLBACK
           if c not in df_cm.columns or df_cm.get(c, pd.Series(dtype=float)).dropna().empty]
if missing:
    print(f"  ↩️ 폴백 시도 (blockchain.info): {missing}")
    df_bci = fetch_blockchain_info(ONCHAIN_LOOKBACK_DAYS)
    if len(df_bci) > 0:
        ONCHAIN_STATUS["fallback"] = "blockchain_info_charts"
        for c in missing:
            if c in df_bci.columns:
                df_cm = df_cm.join(df_bci[[c]], how="outer") if len(df_cm) else df_bci[[c]]
                ONCHAIN_STATUS["columns"][c] = {
                    "source": "blockchain_info",
                    "latest": str(df_bci[c].dropna().index.max().date()),
                }
                print(f"     ✅ {c} 폴백 적용")

# ── 대체 불가 지표 경고 (loud) ──
irreplaceable = ["CapMVRVCur", "AdrActCnt", "AdrBalCnt", "FlowInExUSD", "FlowOutExUSD"]
dead = [c for c in irreplaceable
        if c not in df_cm.columns or df_cm.get(c, pd.Series(dtype=float)).dropna().empty]
if dead:
    print(f"  🔴🔴 대체 불가 지표 수집 실패: {dead} — 건전성 게이트가 차단할 것")
    ONCHAIN_STATUS["errors"].append(f"irreplaceable_missing: {dead}")


# ═══════════════════════════════════════════════════════════════
# 5) Fear & Greed
# ═══════════════════════════════════════════════════════════════
print("\n📡 [4/4] Fear & Greed...")

df_fg = pd.DataFrame()
try:
    resp = requests.get("https://api.alternative.me/fng/?limit=0&format=json",
                        timeout=45, headers=HTTP_UA)
    fg = resp.json()["data"]
    rows = [{"Date": pd.Timestamp(int(d["timestamp"]), unit="s"),
             "FearGreed": int(d["value"])} for d in fg]
    df_fg = pd.DataFrame(rows).set_index("Date").sort_index()
    df_fg = df_fg[~df_fg.index.duplicated(keep="last")]
    print(f"  ✅ {len(df_fg)}행 (~{df_fg.index.max().date()})")
except Exception as e:
    print(f"  ⚠️ {str(e)[:70]}")


# ═══════════════════════════════════════════════════════════════
# 6) 병합
# ═══════════════════════════════════════════════════════════════
print("\n🔧 병합...")

if df_yahoo is None or "BTC_Close" not in df_yahoo.columns:
    print("⚠️ BTC 가격 미수신 — 신규 데이터 없음")
    if df_existing is not None and len(df_existing) > 0:
        last = df_existing.iloc[-1]
        latest = {
            "date": str(last["Date"])[:10],
            "btc_close": (round(float(last.get("BTC_Close", 0)), 2)
                          if pd.notna(last.get("BTC_Close")) else None),
            "phase": last.get("CyclePhase", "UNKNOWN"),
            "note": "no_new_data",
            "generated_at": NOW_UTC.isoformat(),
            "engine": f"SOLIDUS_v3.2.0_HALVING_CONVICTION",
            "fetcher_version": VERSION,
            "onchain_status": ONCHAIN_STATUS,
        }
        with open(LATEST_PATH, "w", encoding="utf-8") as f:
            json.dump(latest, f, indent=2, ensure_ascii=False)
        print(f"✅ latest.json 유지: {latest['date']}")
    sys.exit(0)

# BTC 기준 날짜
btc_dates = df_yahoo[["BTC_Close"]].dropna().index
df_new = pd.DataFrame(index=pd.date_range(btc_dates.min(), btc_dates.max(), freq="D"))
df_new.index.name = "Date"
df_new = df_new.join(df_yahoo, how="left")

# FRED
if len(df_fred) > 0:
    if "DGS10_FRED" in df_fred.columns:
        df_new = df_new.join(df_fred[["DGS10_FRED"]], how="left")
        df_new["DGS10"] = df_new["DGS10_FRED"].combine_first(df_new.get("DGS10"))
        df_new.drop(columns=["DGS10_FRED"], inplace=True, errors="ignore")
    fred_cols = [c for c in df_fred.columns if c not in df_new.columns and c != "DGS10_FRED"]
    if fred_cols:
        df_new = df_new.join(df_fred[fred_cols], how="left")

# 온체인 (신규 구간 join — 전체 덮어쓰기는 7)에서 별도 수행)
if len(df_cm) > 0:
    cm_cols = [c for c in df_cm.columns if c not in df_new.columns]
    if cm_cols:
        df_new = df_new.join(df_cm[cm_cols], how="left")

# Fear & Greed
if len(df_fg) > 0:
    fg_cols = [c for c in df_fg.columns if c not in df_new.columns]
    if fg_cols:
        df_new = df_new.join(df_fg[fg_cols], how="left")

# BTC_Close 없는 행 제거
df_new = df_new.dropna(subset=["BTC_Close"])
df_new = df_new.reset_index().rename(columns={"index": "Date"})
print(f"  신규: {len(df_new)}행 (~{df_new['Date'].max().date()})")


# ═══════════════════════════════════════════════════════════════
# 7) 기존 + 신규 결합 → 온체인 덮어쓰기 → 전체 지표 재계산
# ═══════════════════════════════════════════════════════════════
if df_existing is not None:
    df = pd.concat([df_existing, df_new], ignore_index=True)
    df.drop_duplicates(subset=["Date"], keep="last", inplace=True)
else:
    df = df_new.copy()

df.sort_values("Date", inplace=True)
df.reset_index(drop=True, inplace=True)

# ── [C2] 온체인 자가치유 덮어쓰기 ──
# 최근 ONCHAIN_LOOKBACK_DAYS 구간의 온체인 실측값으로 기존 화석값을 교정한다.
FREEZE_ONSET = pd.Timestamp("2026-05-24")   # 화석 구간 시작 (사고 확정일 기준)
if len(df_cm) > 0:
    df = df.set_index("Date")
    overlap = df.index.intersection(df_cm.index)
    n_repair, n_drift = 0, 0     # 화석 구간 교정 / 이력 구간 값 이동(리비전 감시)
    for c in df_cm.columns:
        if c not in df.columns:
            df[c] = np.nan
        src = df_cm.loc[overlap, c]
        src = src[src.notna()]
        if len(src) == 0:
            continue
        before = df.loc[src.index, c].copy()
        df.loc[src.index, c] = src.values
        try:
            changed = pd.Series(before.fillna(-9e99).values != src.values,
                                index=src.index)
            n_repair += int(changed[changed.index >= FREEZE_ONSET].sum())
            n_drift += int(changed[changed.index < FREEZE_ONSET].sum())
        except Exception:
            pass
    df = df.reset_index()
    print(f"  🔗 온체인 덮어쓰기: {len(overlap)}일 구간")
    print(f"     · 화석구간 교정({FREEZE_ONSET.date()} 이후): {n_repair:,}건  ← 의도된 복구")
    print(f"     · 이력구간 값이동(이전): {n_drift:,}건  ← 0 에 가까워야 정상. "
          f"크면 소스 리비전 발생 → 백테스트 영향 검토 필요")

# ── Forward fill ──
# 매크로/주간·월간 지표: 기존과 동일한 무제한 ffill
# 온체인: [C4] FFILL_LIMIT_FROM 이후 행에만 상한 적용 (이력 정합 보존)
ffill_cols = [
    "VIX", "VVIX", "DXY", "DGS10", "MOVE", "Gold", "WTI", "Brent", "SPY", "QQQ",
    "TLT", "HYG", "USD_KRW", "FVX_5Y", "IRX_3M", "OAS_HY", "OAS_IG", "T5YIE",
    "T10YIE", "DFII10", "DFF", "DGS2", "NFCI", "ICSA", "UMCSENT", "UNRATE",
    "CPI", "PPI", "INDPRO", "WALCL", "WTREGEN", "RRPONTSYD", "Net_Liquidity",
    "CapMVRVCur", "AdrActCnt", "FlowInExUSD", "FlowOutExUSD", "CapMrktCurUSD",
    "HashRate", "TxCnt", "SplyCur", "AdrBalCnt", "FearGreed",
]
_limit_mask = df["Date"] >= FFILL_LIMIT_FROM
for c in ffill_cols:
    if c not in df.columns:
        continue
    if c in ONCHAIN_COLS:
        full = df[c].ffill()                              # 이력 구간용
        lim = df[c].ffill(limit=ONCHAIN_FFILL_LIMIT)      # 신규 구간용
        df[c] = full.where(~_limit_mask, lim)
    else:
        df[c] = df[c].ffill()

# ── 기술 지표 전체 재계산 ──
btc = df["BTC_Close"].astype(float)

for w in [20, 50, 100, 200]:
    df[f"BTC_{w}DMA"] = btc.rolling(w).mean()
df["BTC_20WMA"] = btc.rolling(100).mean()
df["BTC_50WMA"] = btc.rolling(250).mean()
df["BTC_21EMA"] = btc.ewm(span=21, adjust=False).mean()

# RSI 14
delta = btc.diff()
gain = delta.where(delta > 0, 0.0)
loss = (-delta).where(delta < 0, 0.0)
ag = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
al = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
df["BTC_RSI_14"] = (100 - 100 / (1 + ag / al.replace(0, np.nan)))

# MACD
e12 = btc.ewm(span=12, adjust=False).mean()
e26 = btc.ewm(span=26, adjust=False).mean()
macd = e12 - e26
df["BTC_MACD_Signal"] = macd.ewm(span=9, adjust=False).mean()
df["BTC_MACD_Hist"] = macd - df["BTC_MACD_Signal"]

# 변동성 + 수익률
df["BTC_Vol_20d"] = btc.pct_change().rolling(20).std() * np.sqrt(252)
df["BTC_Vol_63d"] = btc.pct_change().rolling(63).std() * np.sqrt(252)
for d in [1, 7, 14, 20, 30, 63, 90, 126, 252]:
    df[f"BTC_Ret_{d}d"] = btc.pct_change(d) * 100

# ATH / DD
df["BTC_ATH"] = btc.cummax()
df["BTC_DD_from_ATH"] = (btc - df["BTC_ATH"]) / df["BTC_ATH"]
df["BTC_200DMA_Slope_Pct"] = (df["BTC_200DMA"] / df["BTC_200DMA"].shift(20) - 1) * 100

# 파생
if "DXY" in df.columns:
    df["DXY_20MA"] = df["DXY"].rolling(20).mean()
if "DGS10" in df.columns and "T5YIE" in df.columns:
    df["RealRate"] = df["DGS10"] - df["T5YIE"]
if "DGS10" in df.columns and "DGS2" in df.columns:
    df["Yield_Spread_10Y2Y"] = df["DGS10"] - df["DGS2"]
if "Net_Liquidity" in df.columns:
    df["NetLiq_Chg_4W"] = df["Net_Liquidity"].pct_change(20) * 100

# ATR
high = df["BTC_High"].astype(float)
low = df["BTC_Low"].astype(float)
prev_close = btc.shift(1).fillna(btc.iloc[0])
tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
               axis=1).max(axis=1)
df["BTC_ATR20"] = tr.rolling(20).mean()

# 온체인 파생
if "CapMVRVCur" in df.columns:
    mvrv = df["CapMVRVCur"].astype(float)
    df["NUPL"] = np.where(mvrv.isna() | (mvrv == 0), np.nan, 1 - 1 / mvrv)
    df["MVRV_ZScore"] = (mvrv - mvrv.rolling(365).mean()) / mvrv.rolling(365).std()
if "AdrActCnt" in df.columns:
    aa = df["AdrActCnt"].astype(float)
    df["AA_Chg_30d"] = aa.pct_change(30) * 100
if "FlowInExUSD" in df.columns and "FlowOutExUSD" in df.columns:
    df["ExNetFlow"] = df["FlowOutExUSD"] - df["FlowInExUSD"]
    df["ExNetFlow_7d"] = df["ExNetFlow"].rolling(7).mean()

df["BTC_Peak_6M"] = btc.rolling(126).max()
df["BTC_DD_6M"] = (btc - df["BTC_Peak_6M"]) / df["BTC_Peak_6M"]

# 해시레이트 리본
if "HashRate" in df.columns:
    hr = df["HashRate"].astype(float)
    df["HashRate_30MA"] = hr.rolling(30).mean()
    df["HashRate_60MA"] = hr.rolling(60).mean()
    df["HashRate_Ribbon"] = np.where(
        df["HashRate_60MA"] > 0,
        (df["HashRate_30MA"] - df["HashRate_60MA"]) / df["HashRate_60MA"] * 100, np.nan)

# BTC-SPY 120일 상관계수 (ERA 모니터링)
if "SPY" in df.columns:
    df["BTC_SPY_Corr120"] = btc.pct_change().rolling(120).corr(df["SPY"].pct_change())


# ── 반감기 Phase ──
HALVINGS = [
    pd.Timestamp("2012-11-28"),
    pd.Timestamp("2016-07-09"),
    pd.Timestamp("2020-05-11"),
    pd.Timestamp("2024-04-20"),
    pd.Timestamp("2028-04-15"),
]


def get_cycle_info(date):
    cycle = 0
    for i, h in enumerate(HALVINGS):
        if date >= h:
            cycle = i + 1
    if cycle == 0:
        return 0, "PRE_CYCLE", 0.0, 0
    h = HALVINGS[cycle - 1]
    days = (date - h).days
    total = (HALVINGS[cycle] - h).days if cycle < len(HALVINGS) else 1461
    if days <= 548:
        phase = "BULL"
    elif days <= 912:
        phase = "WINTER"
    else:
        phase = "RECOVERY"
    return cycle, phase, round(days / total, 6), days


info = [get_cycle_info(pd.Timestamp(d)) for d in df["Date"]]
df["Cycle"] = [x[0] for x in info]
df["CyclePhase"] = [x[1] for x in info]
df["CycleProgress"] = [x[2] for x in info]
df["DaysSinceHalving"] = [x[3] for x in info]


# ═══════════════════════════════════════════════════════════════
# 8) 저장
# ═══════════════════════════════════════════════════════════════
df.to_csv(CSV_PATH, index=False)
print(f"\n✅ {CSV_PATH.name}: {len(df)}행, {len(df.columns)}컬럼")

last = df.iloc[-1]


def safe_round(val, decimals=2):
    try:
        if pd.isna(val):
            return None
        return round(float(val), decimals)
    except Exception:
        return None


latest = {
    "date": str(last["Date"])[:10],
    "btc_close": safe_round(last.get("BTC_Close"), 2),
    "vix": safe_round(last.get("VIX"), 2),
    "move": safe_round(last.get("MOVE"), 2),
    "dxy": safe_round(last.get("DXY"), 2),
    "dgs10": safe_round(last.get("DGS10"), 4),
    "oas_hy": safe_round(last.get("OAS_HY"), 3),
    "nfci": safe_round(last.get("NFCI"), 4),
    "mvrv": safe_round(last.get("CapMVRVCur"), 4),
    "rsi_14": safe_round(last.get("BTC_RSI_14"), 2),
    "btc_100dma": safe_round(last.get("BTC_100DMA"), 2),
    "btc_spy_corr120": safe_round(last.get("BTC_SPY_Corr120"), 4),
    "phase": last.get("CyclePhase", "UNKNOWN"),
    "cycle": int(last.get("Cycle", 0)),
    "days_since_halving": int(last.get("DaysSinceHalving", 0)),
    "fear_greed": safe_round(last.get("FearGreed"), 0),
    "gold": safe_round(last.get("Gold"), 2),
    "spy": safe_round(last.get("SPY"), 2),
    "usd_krw": safe_round(last.get("USD_KRW"), 2),
    "nupl": safe_round(last.get("NUPL"), 4),
    "hashrate": safe_round(last.get("HashRate"), 0),
    "generated_at": NOW_UTC.isoformat(),
    # ★ 엔진 라벨을 실제 운용 버전으로 교정 (구 v3.1.0 스탬프 잔존 해소)
    "engine": "SOLIDUS_v3.2.0_HALVING_CONVICTION",
    "fetcher_version": VERSION,
    "onchain_status": ONCHAIN_STATUS,
}
with open(LATEST_PATH, "w", encoding="utf-8") as f:
    json.dump(latest, f, indent=2, ensure_ascii=False)

print(f"✅ latest.json 갱신: {latest['date']} | BTC ${latest['btc_close']:,}")
print(f"   Phase: {latest['phase']} | MVRV: {latest['mvrv']} "
      f"| BTC-SPY상관: {latest['btc_spy_corr120']}")
print(f"   온체인 주소스: {ONCHAIN_STATUS['primary']} "
      f"| 폴백: {ONCHAIN_STATUS['fallback']} | 오류 {len(ONCHAIN_STATUS['errors'])}건")
