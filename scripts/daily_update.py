#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💰 SOLIDUS Daily Data Update
매일 GHA에서 실행 → solidus_daily.csv 갱신 + latest.json 생성
"""
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
import os
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

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

# 반감기 정의
HALVINGS = [
    pd.Timestamp("2012-11-28"),
    pd.Timestamp("2016-07-09"),
    pd.Timestamp("2020-05-11"),
    pd.Timestamp("2024-04-20"),
    pd.Timestamp("2028-04-15"),
]

print("=" * 60)
print("💰 SOLIDUS Daily Data Update")
print(f"   시각: {datetime.utcnow().isoformat()}Z")
print(f"   FRED: {'✅' if FRED_API_KEY else '❌ 미설정'}")
print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# 1) 기존 데이터 로드 → 수집 시작일 결정
# ═══════════════════════════════════════════════════════════════
if CSV_PATH.exists():
    df_existing = pd.read_csv(CSV_PATH, parse_dates=["Date"])
    last_date = df_existing["Date"].max()
    fetch_start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"📂 기존 데이터: {len(df_existing)}행 (~{last_date.date()})")
else:
    df_existing = None
    fetch_start = BACKFILL_START
    print(f"📂 기존 데이터 없음 → {BACKFILL_START}부터 전체 수집")

fetch_end = datetime.utcnow().strftime("%Y-%m-%d")
print(f"📡 수집 범위: {fetch_start} ~ {fetch_end}")

if fetch_start >= fetch_end:
    print("✅ 이미 최신 — 종료")
    exit(0)


# ═══════════════════════════════════════════════════════════════
# 2) Yahoo Finance
# ═══════════════════════════════════════════════════════════════
print("\n📡 [1/4] Yahoo Finance...")

YAHOO_TICKERS = {
    "BTC-USD":  {"cols": {"Close":"BTC_Close","Open":"BTC_Open","High":"BTC_High","Low":"BTC_Low","Volume":"BTC_Volume"}},
    "^VIX":     {"cols": {"Close":"VIX"}},
    "^VVIX":    {"cols": {"Close":"VVIX"}},
    "DX-Y.NYB": {"cols": {"Close":"DXY"}},
    "^MOVE":    {"cols": {"Close":"MOVE"}},
    "^TNX":     {"cols": {"Close":"DGS10"}},
    "^FVX":     {"cols": {"Close":"FVX_5Y"}},
    "^IRX":     {"cols": {"Close":"IRX_3M"}},
    "GC=F":     {"cols": {"Close":"Gold"}},
    "CL=F":     {"cols": {"Close":"WTI"}},
    "BZ=F":     {"cols": {"Close":"Brent"}},
    "SPY":      {"cols": {"Close":"SPY"}},
    "QQQ":      {"cols": {"Close":"QQQ"}},
    "TLT":      {"cols": {"Close":"TLT"}},
    "HYG":      {"cols": {"Close":"HYG"}},
    "USDKRW=X": {"cols": {"Close":"USD_KRW"}},
}

yahoo_frames = {}
for ticker, cfg in YAHOO_TICKERS.items():
    try:
        data = yf.download(ticker, start=fetch_start, end=fetch_end,
                           progress=False, auto_adjust=True)
        if len(data) == 0:
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
        yahoo_frames[ticker] = sub
        print(f"  ✅ {list(rename.values())[0]}: {len(sub)}행")
    except Exception as e:
        print(f"  ❌ {ticker}: {str(e)[:50]}")

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
    "DGS10":"DGS10_FRED", "DGS2":"DGS2", "DFF":"DFF", "DFII10":"DFII10",
    "T5YIE":"T5YIE", "T10YIE":"T10YIE",
    "BAMLH0A0HYM2":"OAS_HY", "BAMLC0A0CM":"OAS_IG",
    "WALCL":"WALCL", "WTREGEN":"WTREGEN", "RRPONTSYD":"RRPONTSYD",
    "NFCI":"NFCI", "ICSA":"ICSA", "UMCSENT":"UMCSENT",
    "UNRATE":"UNRATE", "CPIAUCSL":"CPI", "PPIACO":"PPI", "INDPRO":"INDPRO",
}

df_fred = pd.DataFrame()
if FRED_API_KEY:
    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
        for sid, col in FRED_SERIES.items():
            try:
                s = fred.get_series(sid, observation_start=fetch_start, observation_end=fetch_end)
                if s is not None and len(s) > 0:
                    df_fred[col] = s
                    print(f"  ✅ {col}: {len(s)}행")
            except:
                pass
        df_fred.index = pd.DatetimeIndex(df_fred.index)
        df_fred.index.name = "Date"

        # Net Liquidity
        for c in ["WALCL","WTREGEN","RRPONTSYD"]:
            if c in df_fred.columns:
                df_fred[c] = df_fred[c].ffill()
        if all(c in df_fred.columns for c in ["WALCL","WTREGEN","RRPONTSYD"]):
            df_fred["Net_Liquidity"] = df_fred["WALCL"] - df_fred["WTREGEN"] - df_fred["RRPONTSYD"]
    except Exception as e:
        print(f"  ❌ FRED 실패: {str(e)[:50]}")
else:
    print("  ⏭️ FRED_API_KEY 미설정")


# ═══════════════════════════════════════════════════════════════
# 4) CoinMetrics 온체인
# ═══════════════════════════════════════════════════════════════
print("\n📡 [3/4] CoinMetrics...")

CM_COLS = {
    "time":"Date","CapMVRVCur":"CapMVRVCur","AdrActCnt":"AdrActCnt",
    "FlowInExUSD":"FlowInExUSD","FlowOutExUSD":"FlowOutExUSD",
    "CapMrktCurUSD":"CapMrktCurUSD","HashRate":"HashRate",
    "TxCnt":"TxCnt","SplyCur":"SplyCur","AdrBalCnt":"AdrBalCnt",
}
df_cm = pd.DataFrame()
try:
    resp = requests.get(
        "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv",
        timeout=120)
    resp.raise_for_status()
    raw = pd.read_csv(StringIO(resp.text))
    avail = [c for c in CM_COLS if c in raw.columns]
    df_cm = raw[avail].rename(columns=CM_COLS)
    df_cm["Date"] = pd.to_datetime(df_cm["Date"])
    df_cm = df_cm[df_cm["Date"] >= fetch_start].set_index("Date")
    for c in df_cm.columns:
        df_cm[c] = pd.to_numeric(df_cm[c], errors="coerce")
    print(f"  ✅ {len(df_cm)}행, {len(df_cm.columns)}컬럼")
except Exception as e:
    print(f"  ❌ {str(e)[:50]}")


# ═══════════════════════════════════════════════════════════════
# 5) Fear & Greed
# ═══════════════════════════════════════════════════════════════
print("\n📡 [4/4] Fear & Greed...")

df_fg = pd.DataFrame()
try:
    resp = requests.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=30)
    fg = resp.json()["data"]
    rows = [{"Date":pd.Timestamp(int(d["timestamp"]),unit="s"),"FearGreed":int(d["value"])} for d in fg]
    df_fg = pd.DataFrame(rows).set_index("Date").sort_index()
    df_fg = df_fg[df_fg.index >= fetch_start]
    print(f"  ✅ {len(df_fg)}행")
except Exception as e:
    print(f"  ⚠️ {str(e)[:50]}")


# ═══════════════════════════════════════════════════════════════
# 6) 병합
# ═══════════════════════════════════════════════════════════════
print("\n🔧 병합...")

if df_yahoo is None or "BTC_Close" not in df_yahoo.columns:
    print("❌ BTC 가격 없음 — 종료")
    exit(1)

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

# CoinMetrics
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
df_new = df_new.reset_index().rename(columns={"index":"Date"})
print(f"  신규: {len(df_new)}행")


# ═══════════════════════════════════════════════════════════════
# 7) 기존 + 신규 결합 → 전체 지표 재계산
# ═══════════════════════════════════════════════════════════════
if df_existing is not None:
    df = pd.concat([df_existing, df_new], ignore_index=True)
    df.drop_duplicates(subset=["Date"], keep="last", inplace=True)
else:
    df = df_new.copy()

df.sort_values("Date", inplace=True)
df.reset_index(drop=True, inplace=True)

# Forward fill (매크로/온체인 주간→일간)
ffill_cols = [
    "VIX","VVIX","DXY","DGS10","MOVE","Gold","WTI","Brent","SPY","QQQ","TLT","HYG","USD_KRW",
    "FVX_5Y","IRX_3M","OAS_HY","OAS_IG","T5YIE","T10YIE","DFII10","DFF","DGS2",
    "NFCI","ICSA","UMCSENT","UNRATE","CPI","PPI","INDPRO",
    "WALCL","WTREGEN","RRPONTSYD","Net_Liquidity",
    "CapMVRVCur","AdrActCnt","FlowInExUSD","FlowOutExUSD","CapMrktCurUSD","HashRate",
    "TxCnt","SplyCur","AdrBalCnt","FearGreed",
]
for c in ffill_cols:
    if c in df.columns:
        df[c] = df[c].ffill()

# ── 기술 지표 전체 재계산 ──
btc = df["BTC_Close"].astype(float)

for w in [20,50,100,200]:
    df[f"BTC_{w}DMA"] = btc.rolling(w).mean()
df["BTC_20WMA"] = btc.rolling(100).mean()
df["BTC_50WMA"] = btc.rolling(250).mean()
df["BTC_21EMA"] = btc.ewm(span=21, adjust=False).mean()

# RSI 14
delta = btc.diff()
gain = delta.where(delta > 0, 0.0)
loss = (-delta).where(delta < 0, 0.0)
ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
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
for d in [1,7,14,20,30,63,90,126,252]:
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
tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
df["BTC_ATR20"] = tr.rolling(20).mean()

# 온체인 파생
if "CapMVRVCur" in df.columns:
    mvrv = df["CapMVRVCur"].astype(float)
    df["NUPL"] = np.where(mvrv.isna()|(mvrv==0), np.nan, 1-1/mvrv)
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
    df["HashRate_Ribbon"] = np.where(df["HashRate_60MA"]>0,
        (df["HashRate_30MA"]-df["HashRate_60MA"])/df["HashRate_60MA"]*100, np.nan)

# BTC-SPY 120일 상관계수 (ERA 모니터링)
if "SPY" in df.columns:
    df["BTC_SPY_Corr120"] = btc.pct_change().rolling(120).corr(df["SPY"].pct_change())

# ── 반감기 Phase ──
def get_cycle_info(date):
    cycle = 0
    for i, h in enumerate(HALVINGS):
        if date >= h: cycle = i + 1
    if cycle == 0: return 0, "PRE_CYCLE", 0.0, 0
    h = HALVINGS[cycle-1]
    days = (date - h).days
    total = (HALVINGS[cycle] - h).days if cycle < len(HALVINGS) else 1461
    if days <= 548: phase = "BULL"
    elif days <= 912: phase = "WINTER"
    else: phase = "RECOVERY"
    return cycle, phase, round(days/total, 6), days

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

# latest.json
last = df.iloc[-1]

def safe_round(val, decimals=2):
    try:
        if pd.isna(val): return None
        return round(float(val), decimals)
    except: return None

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
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "engine": "SOLIDUS_v3.1.0_HALVING_CONVICTION",
}
with open(LATEST_PATH, "w") as f:
    json.dump(latest, f, indent=2, ensure_ascii=False)

print(f"✅ latest.json 갱신: {latest['date']} | BTC ${latest['btc_close']:,}")
print(f"   Phase: {latest['phase']} | MVRV: {latest['mvrv']} | BTC-SPY상관: {latest['btc_spy_corr120']}")
