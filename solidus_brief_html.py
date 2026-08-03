#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ======================================================================
# solidus_brief_html.py — 💰 SOLIDUS BTC HTML 브리핑 생성기
# VERSION : v1.1.0
# CHANGE  : v1.1.0 [TRAILING] 트레일링 수익률 카드 신설 — 1·3·6개월 전략(SOLIDUS) vs
#           BTC 보유 vs 초과수익(α) 3행 비교표. 기준 = run_solidus equity_curve 단일 원천
#           (Commander 지시 2026-08-03).
# ENGINE  : solidus_engine_v3_2_0.py (Crown #11 HALVING_CONVICTION + Hysteresis)
# 실행 위치: solidus-public-data 레포 루트 (엔진·데이터 동거)
#   - 데이터  : data/solidus_daily.csv + latest.json
#   - 산출물  : briefing.html + index.html (GitHub Pages 서빙)
#
# 설계 원칙 (ARGUS argus_brief_html_v2 계보 이식):
#   1) FRESHGUARD — 정적 산출물에 시간 상대 주장을 굽지 않는다.
#      열람 시점에 브라우저가 latest.json을 fetch → 신선도 배지 재작성.
#   2) VGUARD — 빌드 타임 버전 표기 무결성 게이트 (불일치 시 exit 1 → GHA FAIL).
#   3) fail-loud — 엔진/데이터 실패 시 조용한 fallback 금지, 명시적 실패.
#   4) 목표비중 최상단 · 최대 강조 (프로젝트 지침: 본문 대비 2배 이상)
# ======================================================================
import os
import sys
import io
import glob
import importlib.util
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))
RENDERER_VER = "v1.1.0"          # 버전 단일 진리원 — 배지·푸터는 본 상수만 참조
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 데이터 경로 (레포 로컬 우선, 부재 시 raw URL 폴백 — 로컬 테스트용)
DATA_PATHS = [os.path.join(SCRIPT_DIR, "data", "solidus_daily.csv"),
              os.path.join(SCRIPT_DIR, "solidus_daily.csv")]
DATA_URL = "https://raw.githubusercontent.com/daifulee/solidus-public-data/main/data/solidus_daily.csv"
ENGINE_GLOB = os.path.join(SCRIPT_DIR, "solidus_engine_v*.py")
OUT_FILES = [os.path.join(SCRIPT_DIR, "briefing.html"),
             os.path.join(SCRIPT_DIR, "index.html")]


# ══════════════════════════════════════════════════════════════
# 엔진 · 데이터 로드
# ══════════════════════════════════════════════════════════════
def load_engine():
    """레포 루트의 최신 solidus_engine_v*.py 를 importlib 로 직접 로드 (fail-loud)."""
    cands = sorted(glob.glob(ENGINE_GLOB))
    if not cands:
        raise FileNotFoundError(f"엔진 없음: {ENGINE_GLOB}")
    path = cands[-1]  # 버전 정렬 최신
    print(f"  엔진: {os.path.basename(path)}")
    spec = importlib.util.spec_from_file_location("solidus_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, os.path.basename(path)


def load_data():
    """로컬 CSV 우선, 부재 시 raw URL (로컬 개발 편의)."""
    for p in DATA_PATHS:
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["Date"])
            print(f"  데이터: {p} ({len(df)}행)")
            return df.sort_values("Date").reset_index(drop=True)
    import requests
    r = requests.get(DATA_URL, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"])
    print(f"  데이터: URL 폴백 ({len(df)}행)")
    return df.sort_values("Date").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 표시 유틸
# ══════════════════════════════════════════════════════════════
def g(row, col, default=np.nan):
    v = row.get(col, default)
    try:
        return default if pd.isna(v) else float(v)
    except Exception:
        return default


def fv(v, d=1, suffix=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    if abs(v) >= 1000:
        return f"{v:,.{d}f}{suffix}"
    return f"{v:.{d}f}{suffix}"


def phase_kr(p):
    return {"BULL": "상승장", "WINTER": "하락장", "RECOVERY": "회복장"}.get(p, "미정")


def phase_emoji(p):
    return {"BULL": "🟢", "WINTER": "❄️", "RECOVERY": "🔄"}.get(p, "⚪")


def mvrv_grade(m):
    if np.isnan(m):
        return ("N/A", "#94a3b8")
    if m < 1.0:
        return ("극저평가", "#4ade80")
    if m < 1.3:
        return ("저평가", "#4ade80")
    if m < 2.0:
        return ("적정", "#facc15")
    if m < 2.5:
        return ("고평가", "#fb923c")
    if m < 3.0:
        return ("과열주의", "#f87171")
    return ("극과열", "#f87171")


def fg_face(v):
    if v is None:
        return "❓"
    if v <= 25:
        return "😨"
    if v <= 45:
        return "😟"
    if v <= 55:
        return "😐"
    if v <= 75:
        return "😊"
    return "🤑"


# 매크로 신호등 (🟢 안전 / 🟡 경계 / 🟠 위험) — ARGUS v9.2.0 방식 이식
# mode: hi=높을수록 위험 / lo=낮을수록 위험 / mid=적정대 이탈 시 위험
_MACRO_BANDS = {
    "VIX":     ("hi", 20, 30),
    "MOVE":    ("hi", 100, 130),
    "DXY":     ("hi", 103, 107),
    "DGS10":   ("hi", 4.5, 5.0),
    "OAS_HY":  ("hi", 3.5, 5.0),
    "NFCI":    ("hi", -0.3, 0.0),
    "T5YIE":   ("mid", 2.0, 2.5, 1.7, 2.8),
}
_BAND_COLOR = {"safe": "#4ade80", "warn": "#facc15", "danger": "#fb923c"}


def band_color(key, v):
    spec = _MACRO_BANDS.get(key)
    if spec is None or v is None or (isinstance(v, float) and np.isnan(v)):
        return "#e2e8f0"
    mode = spec[0]
    if mode == "hi":
        _, warn, dang = spec
        b = "safe" if v < warn else ("warn" if v < dang else "danger")
    elif mode == "lo":
        _, warn, dang = spec
        b = "safe" if v > warn else ("warn" if v > dang else "danger")
    else:
        _, lo_s, hi_s, lo_d, hi_d = spec
        b = "safe" if lo_s <= v <= hi_s else ("warn" if lo_d <= v <= hi_d else "danger")
    return _BAND_COLOR[b]


# ══════════════════════════════════════════════════════════════
# 🛡️ FRESHGUARD — 열람 시점 신선도 재검증 (ARGUS v9.2.27 이식)
#    latest.json 은 briefing.html 과 같은 레포 루트에 서빙 → 상대경로 fetch.
#    SOLIDUS latest.json 의 날짜 키는 소문자 "date".
# ══════════════════════════════════════════════════════════════
FRESHGUARD_JS = """<script>
(function(){
  var el = document.getElementById('fg-badge');
  if (!el || !window.fetch) return;
  var src = el.getAttribute('data-src');
  var gen = el.getAttribute('data-gen') || '';
  function paint(c, t, tip){ el.style.color = c; el.textContent = t; if (tip) el.title = tip; }
  fetch('latest.json', {cache: 'no-store'})
    .then(function(r){ if (!r.ok) throw 0; return r.json(); })
    .then(function(j){
      var canon = String(j.date || j.Date || '').slice(0, 10);
      if (!canon) throw 0;
      if (canon === src) {
        paint('#4ade80', '🟢 정본 일치', '열람 시점 재검증 통과 — 정본 ' + canon);
      } else {
        paint('#f87171', '🔴 STALE · 정본 ' + canon,
              '이 페이지는 ' + src + ' 데이터로 ' + gen + ' 에 생성됨. 현재 정본은 ' + canon + '.');
      }
    })
    .catch(function(){
      paint('#94a3b8', '⚪ 미검증', '정본(latest.json) 회수 실패 — 열람 시점 신선도 확인 불가');
    });
})();
</script>"""


# ══════════════════════════════════════════════════════════════
# CSS (ARGUS 다크 슬레이트 테마 계승)
# ══════════════════════════════════════════════════════════════
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Noto Sans KR',sans-serif; background:#0f172a; color:#e2e8f0;
       font-size:11px; line-height:1.55; padding:16px; max-width:860px; margin:0 auto; word-break:keep-all; }
.hdr { display:flex; align-items:center; justify-content:space-between; padding:10px 14px;
       background:linear-gradient(135deg,#1e293b,#0f172a); border-radius:10px; margin-bottom:12px;
       border-left:4px solid #f7931a; flex-wrap:wrap; gap:6px; }
.hdr h1 { font-size:18px; font-weight:900; }
.badge { display:inline-block; padding:3px 10px; border-radius:5px; font-weight:700; font-size:10px; }
.bb { background:#f7931a; color:#0f172a; } .bv { background:#1e293b; color:#64748b; }
.card { background:#1e293b; border-radius:10px; padding:14px 16px; margin-bottom:10px; }
.ct { font-size:13px; font-weight:700; margin-bottom:10px; color:#e2e8f0; }
.hero { text-align:center; padding:22px 16px 18px; background:linear-gradient(160deg,#1e293b 0%,#111c30 100%); }
.hero-label { font-size:13px; color:#94a3b8; font-weight:500; letter-spacing:.05em; }
.hero-val { font-size:76px; font-weight:900; line-height:1.05; margin:2px 0 4px; }
.hero-sub { font-size:12px; color:#94a3b8; }
.pbadge { display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700; font-size:12px;
          border:1.5px solid; margin-top:8px; }
.g2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.g3 { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.stat { text-align:center; padding:10px 6px; background:#0f172a; border-radius:8px; }
.stat-val { font-size:20px; font-weight:900; line-height:1.15; }
.stat-lbl { font-size:9px; color:#64748b; margin-top:2px; }
table { width:100%; border-collapse:collapse; font-size:11px; }
th { text-align:left; color:#64748b; font-weight:500; padding:5px 8px; border-bottom:1px solid #334155; font-size:9px; }
td { padding:5px 8px; border-bottom:1px solid rgba(51,65,85,.5); }
.bar-wrap { background:#0f172a; border-radius:6px; height:16px; position:relative; overflow:hidden; margin:8px 0 4px; }
.bar-fill { height:100%; border-radius:6px; }
.sr { display:flex; align-items:center; gap:7px; padding:5px 10px; border-radius:5px; margin-bottom:2px; font-size:11px; }
.si { width:16px; text-align:center; flex-shrink:0; }
.sn { min-width:150px; font-weight:600; }
.sc { flex:1; color:#64748b; font-size:10px; }
.sv { min-width:120px; text-align:right; color:#94a3b8; font-size:10px; }
.sig-met { background:rgba(34,197,94,.09); }
.sig-fail { background:rgba(148,163,184,.04); }
.warn { font-size:10px; color:#fbbf24; padding:6px 10px; background:rgba(251,191,36,.07); border-radius:5px; margin:6px 0; }
.footer { text-align:right; font-size:9px; color:#475569; margin-top:10px; line-height:1.7; }
@media (max-width:600px) {
  body { padding:8px; font-size:10px; }
  .g2 { grid-template-columns:1fr; }
  .g3 { grid-template-columns:repeat(3,1fr); }
  .hero-val { font-size:56px; }
  .sn { min-width:0; flex:1 1 auto; }
  .sc { flex:1 1 100%; order:9; padding-left:23px; }
  th, td { white-space:nowrap; }
}
"""


# ══════════════════════════════════════════════════════════════
# 섹션 빌더
# ══════════════════════════════════════════════════════════════
def build_hero(ct, prev_pos):
    """🎯 목표비중 히어로 — 최상단 · 본문 대비 초대형 (프로젝트 지침)."""
    pct = int(round(ct["target"] * 100))
    prev_pct = int(round(prev_pos * 100))
    phase = ct["phase"]
    if pct >= 80:
        col = "#4ade80"
    elif pct >= 40:
        col = "#fb923c"
    elif pct > 0:
        col = "#facc15"
    else:
        col = "#f87171"
    pcol = {"BULL": "#4ade80", "WINTER": "#60a5fa", "RECOVERY": "#facc15"}.get(phase, "#94a3b8")
    if pct > prev_pct:
        chg = f'<span style="color:#4ade80">📈 전일 {prev_pct}% → {pct}% (상향)</span>'
    elif pct < prev_pct:
        chg = f'<span style="color:#f87171">📉 전일 {prev_pct}% → {pct}% (하향)</span>'
    else:
        chg = f'<span style="color:#64748b">➡️ 전일 동일 ({prev_pct}%)</span>'
    return (
        f'<div class="card hero">'
        f'<div class="hero-label">🎯 BTC 목표비중</div>'
        f'<div class="hero-val" style="color:{col}">{pct}%</div>'
        f'<div class="hero-sub">{chg}</div>'
        f'<div><span class="pbadge" style="border-color:{pcol};color:{pcol}">'
        f'{phase_emoji(phase)} {phase} ({phase_kr(phase)}) · D+{ct["days"]}</span></div>'
        f'<div class="hero-sub" style="margin-top:8px">발동 조항: <b>{ct.get("clause","?")}</b></div>'
        f'</div>\n')


def build_cycle(ct, eng):
    """반감기 사이클 진행 카드 — 타임라인 바 + 다음 전환 D-day."""
    phase, days = ct["phase"], ct["days"]
    BULL_END = eng.CONST_BULL_END_DAYS
    WINTER_END = eng.CONST_WINTER_END_DAYS
    cycle_total = (eng.HALVINGS[4] - eng.HALVINGS[3]).days
    h4 = eng.HALVINGS[3].date()

    pos_pct = min(100.0, days / cycle_total * 100)
    b_pct = BULL_END / cycle_total * 100
    w_pct = (WINTER_END - BULL_END) / cycle_total * 100
    r_pct = 100 - b_pct - w_pct

    if phase == "BULL":
        nxt, dl = "WINTER", BULL_END - days
    elif phase == "WINTER":
        nxt, dl = "RECOVERY", WINTER_END - days
    else:
        nxt, dl = "BULL (5차 반감기)", max(0, cycle_total - days)
    nxt_date = (pd.Timestamp(h4) + pd.Timedelta(days=days + dl)).date()

    bar = (
        f'<div class="bar-wrap" style="height:20px">'
        f'<div style="position:absolute;left:0;width:{b_pct:.1f}%;height:100%;background:rgba(74,222,128,.25)"></div>'
        f'<div style="position:absolute;left:{b_pct:.1f}%;width:{w_pct:.1f}%;height:100%;background:rgba(96,165,250,.25)"></div>'
        f'<div style="position:absolute;left:{b_pct+w_pct:.1f}%;width:{r_pct:.1f}%;height:100%;background:rgba(250,204,21,.22)"></div>'
        f'<div style="position:absolute;left:{pos_pct:.1f}%;top:-2px;width:3px;height:24px;background:#f7931a;border-radius:2px"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#64748b">'
        f'<span>🟢 BULL 0~{BULL_END}일</span><span>❄️ WINTER ~{WINTER_END}일</span>'
        f'<span>🔄 RECOVERY ~{cycle_total}일</span></div>')

    return (
        f'<div class="card"><div class="ct">🔄 반감기 사이클 (4차: {h4})</div>'
        f'{bar}'
        f'<div style="margin-top:10px;font-size:12px">현재 <b>{phase_emoji(phase)} {phase} D+{days}</b>'
        f' → <b>{nxt}</b> 전환까지 <b style="color:#f7931a">{dl}일</b>'
        f' <span style="color:#64748b">(예상 {nxt_date})</span></div>'
        f'</div>\n')


def build_core_stats(row, ct):
    """핵심 지표 6타일."""
    btc = g(row, "BTC_Close")
    d100 = g(row, "BTC_100DMA")
    rsi = g(row, "BTC_RSI_14")
    mvrv = g(row, "CapMVRVCur")
    fg = row.get("FearGreed")
    fg_val = int(fg) if pd.notna(fg) else None
    dma_pct = (btc / d100 - 1) * 100 if (not np.isnan(d100) and d100 > 0) else None
    dma_col = "#4ade80" if (dma_pct or 0) >= 0 else "#f87171"
    mg, mcol = mvrv_grade(mvrv)
    above = ct.get("above100")
    hys = ("🟢 위 (밴드확정)" if above else "🔴 아래 (밴드확정)")
    tiles = [
        (f"${btc:,.0f}", "₿ BTC 종가", "#e2e8f0"),
        (f"${d100:,.0f}", "📏 100일선", "#e2e8f0"),
        (f"{dma_pct:+.1f}%" if dma_pct is not None else "N/A", "vs 100일선", dma_col),
        (f"{rsi:.1f}", "📉 RSI-14", "#f87171" if rsi < 30 else ("#fb923c" if rsi > 70 else "#e2e8f0")),
        (f"{mvrv:.3f}", f"⛓️ MVRV · {mg}", mcol),
        (f"{fg_face(fg_val)} {fg_val if fg_val is not None else 'N/A'}", "공포탐욕", "#e2e8f0"),
    ]
    cells = "".join(
        f'<div class="stat"><div class="stat-val" style="color:{c}">{v}</div>'
        f'<div class="stat-lbl">{l}</div></div>' for v, l, c in tiles)
    return (
        f'<div class="card"><div class="ct">📍 핵심 지표</div>'
        f'<div class="g3">{cells}</div>'
        f'<div style="margin-top:8px;font-size:10px;color:#94a3b8">100일선 데드밴드(±2%) 상태: '
        f'<b>{hys}</b> — 밴드 내 횡보 시 직전 상태 유지 (휩쏘 차단)</div>'
        f'</div>\n')


def build_conditions(row, ct, eng):
    """엔진 조건 대시보드 — 현재 Phase 의 발동/대기 조항을 ARGUS 신호행 방식으로 표시."""
    phase = ct["phase"]
    btc = g(row, "BTC_Close")
    d100 = g(row, "BTC_100DMA")
    rsi = g(row, "BTC_RSI_14", 50)
    mvrv = g(row, "CapMVRVCur", 1.5)
    above = bool(ct.get("above100"))
    rows = []

    def sig(name, cond, cur, met, note=""):
        icon = "✅" if met else "✗"
        cls = "sig-met" if met else "sig-fail"
        rows.append(
            f'<div class="sr {cls}"><span class="si">{icon}</span>'
            f'<span class="sn">{name}</span><span class="sc">{cond}{note}</span>'
            f'<span class="sv">{cur}</span></div>')

    if phase == "WINTER":
        reentry = d100 * (1 + eng.CONST_DMA_HYS_BAND) if not np.isnan(d100) else np.nan
        gap = (reentry / btc - 1) * 100 if not np.isnan(reentry) else None
        sig("100일선 위 (밴드)", f"BTC > 100일선 x1.02 재진입",
            f"필요 ${reentry:,.0f} ({gap:+.1f}%)" if gap is not None else "N/A", above)
        sig("PreBull 진입", "MVRV < 1.3 ∩ 100일선 위 → 20~50%",
            f"MVRV={mvrv:.3f}", above and mvrv < eng.CONST_PRE_BULL_MVRV)
        sig("MVRV-Mid 소규모", "1.3 <= MVRV < 2.0 ∩ 100일선 위 → 10%",
            f"MVRV={mvrv:.3f}", above and eng.CONST_PRE_BULL_MVRV <= mvrv < 2.0)
        sig("Phoenix 과매도", "RSI < 30 ∩ BTC > 100일선 x0.85 → 15%",
            f"RSI={rsi:.1f}", rsi < eng.CONST_WINTER_PHOENIX_RSI and
            (not np.isnan(d100)) and btc > d100 * eng.CONST_PHOENIX_FLOOR_PCT)
        sig("극저평가 바닥매수", "MVRV < 1.0 → 30%", f"MVRV={mvrv:.3f}", mvrv < 1.0)
        note = ("현재 발동 조항이 없으면 WINTER 기본 <b>0%</b> 유지. "
                "위 조항 중 하나라도 충족 시 해당 비중으로 진입.")
    elif phase == "BULL":
        days = ct["days"]
        if days <= eng.CONST_BULL_EARLY_END:
            sig("전반 기본 100%", f"D+{days} <= {eng.CONST_BULL_EARLY_END}", f"D+{days}", True)
            sig("100일선 방어", "100일선 아래(밴드) → 50% 축소", f"above100={above}", not above)
        else:
            base = "90%" if days <= eng.CONST_BULL_MID_END else "70%"
            sig(f"중후반 기본 {base}", f"D+{days}", f"D+{days}", True)
            breach = (not np.isnan(d100)) and btc < d100 * eng.CONST_DMA_BREACH_PCT
            sig("이탈 방어", "BTC < 100일선 x0.95 → 25%", f"${btc:,.0f}", breach)
            if days > eng.CONST_BULL_MID_END:
                sig("MVRV 과열캡", "MVRV >= 3.5 → 30% 캡", f"MVRV={mvrv:.3f}", mvrv >= 3.5)
        note = "BULL 시간감쇠: 전반 100% → 중반 90% → 후반 70%."
    else:  # RECOVERY
        sig("기본 60%", "RECOVERY 기본", "—", True)
        sig("100일선 방어", "100일선 아래(밴드) → 20%", f"above100={above}", not above)
        gates = eng.compute_momentum_gates(row)
        sig("모멘텀 부스트", "게이트 3/4+ → x1.3 확대", f"게이트 {gates}/4", gates >= 3)
        sig("MVRV 저평가 확대", "MVRV<1.2→70% / <1.5→50%", f"MVRV={mvrv:.3f}", mvrv < 1.5)
        note = "RECOVERY: 기본 60%에서 모멘텀·밸류에이션 따라 20~100% 가변."
    return (
        f'<div class="card"><div class="ct">🔧 엔진 조건 대시보드 — {phase_emoji(phase)} {phase} 조항</div>'
        + "".join(rows) +
        f'<div style="margin-top:8px;font-size:10px;color:#94a3b8">{note}</div></div>\n')


def build_macro(row):
    """매크로 신호등 카드."""
    corr = g(row, "BTC_SPY_Corr120")
    items = [
        ("VIX", g(row, "VIX"), 1, ""), ("MOVE", g(row, "MOVE"), 1, ""),
        ("DXY", g(row, "DXY"), 1, ""), ("DGS10", g(row, "DGS10"), 2, "%"),
        ("T5YIE", g(row, "T5YIE"), 2, "%"), ("OAS_HY", g(row, "OAS_HY"), 2, ""),
        ("NFCI", g(row, "NFCI"), 3, ""), ("Gold", g(row, "Gold"), 0, ""),
        ("SPY", g(row, "SPY"), 1, ""), ("WTI", g(row, "WTI"), 1, ""),
        ("USD/KRW", g(row, "USD_KRW"), 0, ""),
    ]
    cells = "".join(
        f'<div class="stat"><div class="stat-val" style="font-size:15px;color:{band_color(k, v)}">{fv(v, d, s)}</div>'
        f'<div class="stat-lbl">{k}</div></div>'
        for k, v, d, s in items)
    if not np.isnan(corr):
        if corr >= 0.5:
            cw = ' <span style="color:#f87171;font-weight:700">🔴 ERA 경보</span>'
        elif corr >= 0.4:
            cw = ' <span style="color:#fbbf24">⚠️ 경계</span>'
        else:
            cw = ' <span style="color:#4ade80">양호</span>'
        corr_line = (f'<div style="margin-top:8px;font-size:11px">BTC-SPY 120일 상관: '
                     f'<b>{corr:.3f}</b>{cw}</div>')
    else:
        corr_line = ""
    return (f'<div class="card"><div class="ct">📡 매크로 신호등</div>'
            f'<div class="g3">{cells}</div>{corr_line}</div>\n')


def build_onchain(row):
    nupl = g(row, "NUPL")
    hr = g(row, "HashRate")
    aa = g(row, "AdrActCnt")
    mvrv = g(row, "CapMVRVCur")
    mg, mcol = mvrv_grade(mvrv)
    hr_s = f"{hr/1e9:.2f}G" if not np.isnan(hr) and hr > 0 else "N/A"
    aa_s = f"{aa/1000:.0f}K" if not np.isnan(aa) else "N/A"
    tiles = [
        (f"{fv(mvrv,3)}", f"MVRV · {mg}", mcol),
        (f"{fv(nupl,3)}", "NUPL", "#e2e8f0"),
        (aa_s, "활성주소", "#e2e8f0"),
        (hr_s, "해시레이트", "#e2e8f0"),
    ]
    cells = "".join(
        f'<div class="stat"><div class="stat-val" style="font-size:16px;color:{c}">{v}</div>'
        f'<div class="stat-lbl">{l}</div></div>' for v, l, c in tiles)
    return (f'<div class="card"><div class="ct">⛓️ 온체인</div>'
            f'<div class="g3" style="grid-template-columns:repeat(4,1fr)">{cells}</div></div>\n')


def build_chart(df, eq):
    """6개월 차트 — BTC 종가 + 100일선(±2% 데드밴드 음영) + 목표비중 스텝. 순수 SVG."""
    try:
        n = 183
        d = df.tail(n).reset_index(drop=True)
        e = eq.tail(len(d)).reset_index(drop=True)
        W, H, HP = 828, 190, 54   # 가격영역 H, 포지션영역 HP
        px = lambda i: 10 + i * (W - 20) / max(1, len(d) - 1)
        closes = d["BTC_Close"].astype(float)
        dmas = d["BTC_100DMA"].astype(float)
        lo = min(closes.min(), np.nanmin(dmas) * 0.98) * 0.985
        hi = max(closes.max(), np.nanmax(dmas) * 1.02) * 1.015
        py = lambda v: 8 + (H - 16) * (1 - (v - lo) / (hi - lo))

        band_up, band_dn, line_b, line_d = [], [], [], []
        for i in range(len(d)):
            c, m = closes.iloc[i], dmas.iloc[i]
            line_b.append(f"{px(i):.1f},{py(c):.1f}")
            if not np.isnan(m):
                line_d.append(f"{px(i):.1f},{py(m):.1f}")
                band_up.append(f"{px(i):.1f},{py(m*1.02):.1f}")
                band_dn.append(f"{px(i):.1f},{py(m*0.98):.1f}")
        band_pts = " ".join(band_up + band_dn[::-1])

        # 포지션 스텝 (하단)
        pos_pts = [f"10,{H+HP:.1f}"]
        for i in range(len(e)):
            p = float(e["Position"].iloc[i])
            y = H + HP - p * (HP - 6)
            if i > 0:
                pos_pts.append(f"{px(i):.1f},{prev_y:.1f}")
            pos_pts.append(f"{px(i):.1f},{y:.1f}")
            prev_y = y
        pos_pts.append(f"{W-10},{H+HP}")

        # 월 눈금
        ticks = ""
        months_seen = set()
        for i in range(len(d)):
            dt = pd.Timestamp(d["Date"].iloc[i])
            key = (dt.year, dt.month)
            if dt.day <= 3 and key not in months_seen:
                months_seen.add(key)
                ticks += (f'<line x1="{px(i):.0f}" y1="0" x2="{px(i):.0f}" y2="{H+HP}" '
                          f'stroke="#1e293b" stroke-width="1"/>'
                          f'<text x="{px(i)+3:.0f}" y="{H+HP-3}" fill="#475569" font-size="9">{dt.month}월</text>')

        svg = (
            f'<svg viewBox="0 0 {W} {H+HP+8}" xmlns="http://www.w3.org/2000/svg" '
            f'style="width:100%;background:#0f172a;border-radius:8px">'
            f'{ticks}'
            f'<polygon points="{band_pts}" fill="rgba(96,165,250,0.10)"/>'
            f'<polyline points="{" ".join(line_d)}" fill="none" stroke="#60a5fa" '
            f'stroke-width="1.4" stroke-dasharray="5,3"/>'
            f'<polyline points="{" ".join(line_b)}" fill="none" stroke="#f7931a" stroke-width="2"/>'
            f'<polygon points="{" ".join(pos_pts)}" fill="rgba(74,222,128,0.30)" stroke="#4ade80" stroke-width="1"/>'
            f'<text x="14" y="16" fill="#f7931a" font-size="10" font-weight="700">— BTC</text>'
            f'<text x="70" y="16" fill="#60a5fa" font-size="10">- - 100일선 (±2% 밴드)</text>'
            f'<text x="14" y="{H+14}" fill="#4ade80" font-size="10">▮ 목표비중 (0~100%)</text>'
            f'</svg>')
        last_p = float(e["Position"].iloc[-1]) * 100
        return (f'<div class="card"><div class="ct">📈 최근 6개월 — BTC · 100일선 · 목표비중</div>'
                f'{svg}<div style="font-size:9px;color:#64748b;margin-top:4px">'
                f'하단 초록 면적 = 엔진 목표비중 추이 (현재 {last_p:.0f}%)</div></div>\n')
    except Exception as ex:
        print(f"  ⚠️ 차트 생략: {ex!r}")
        return ""


def build_trailing(eq):
    """📆 트레일링 수익률 — 1·3·6개월 전략(SOLIDUS) vs BTC 보유 vs 초과수익(α).
    단일 원천 = run_solidus equity_curve (Equity·BTC_Close). 달력일 기준 lookback."""
    try:
        e = eq.copy()
        e["Date"] = pd.to_datetime(e["Date"])
        last = e.iloc[-1]
        t0 = last["Date"]

        def cell(v, unit="%"):
            if v is None:
                return '<td style="color:#475569">N/A</td>'
            col = "#4ade80" if v > 0 else ("#f87171" if v < 0 else "#94a3b8")
            return f'<td style="color:{col};font-weight:700">{v:+.1f}{unit}</td>'

        windows = [("1개월", 30), ("3개월", 91), ("6개월", 183)]
        strat, btc, alpha = [], [], []
        for _, days in windows:
            base_df = e[e["Date"] <= t0 - pd.Timedelta(days=days)]
            if len(base_df) == 0:
                strat.append(None); btc.append(None); alpha.append(None)
                continue
            base = base_df.iloc[-1]
            s = (float(last["Equity"]) / float(base["Equity"]) - 1) * 100
            b = (float(last["BTC_Close"]) / float(base["BTC_Close"]) - 1) * 100
            strat.append(s); btc.append(b); alpha.append(s - b)

        head = "".join(f"<th style='text-align:right'>{w}</th>" for w, _ in windows)
        row_s = "".join(cell(v) for v in strat)
        row_b = "".join(cell(v) for v in btc)
        row_a = "".join(cell(v, "%p") for v in alpha)
        return (
            f'<div class="card"><div class="ct">📆 트레일링 수익률</div>'
            f'<table style="text-align:right">'
            f'<tr><th style="text-align:left">구분</th>{head}</tr>'
            f'<tr><td style="text-align:left;font-weight:600">💰 전략 (SOLIDUS)</td>{row_s}</tr>'
            f'<tr><td style="text-align:left;font-weight:600">₿ BTC 보유</td>{row_b}</tr>'
            f'<tr><td style="text-align:left;color:#94a3b8">초과수익 (α)</td>{row_a}</tr>'
            f'</table>'
            f'<div style="margin-top:6px;font-size:10px;color:#64748b">'
            f'전략 = 엔진 목표비중을 그대로 따랐을 때의 자산곡선 (equity_curve 단일 원천, 달력일 기준)</div>'
            f'</div>\n')
    except Exception as ex:
        print(f"  ⚠️ 트레일링 카드 생략: {ex!r}")
        return ""


def build_trades(res):
    """최근 목표비중 변동 이력 (엔진 trades 마지막 8건)."""
    tr = res.get("trades", [])[-8:]
    if not tr:
        return ""
    body = ""
    for t in reversed(tr):
        act = t["action"]
        icon = {"ENTER": "🟢", "EXIT": "🔴", "BUY": "📈", "SELL": "📉"}.get(act, "·")
        body += (f'<tr><td>{t["date"]}</td><td>{icon} {act}</td>'
                 f'<td>{t["from"]*100:.0f}% → <b>{t["to"]*100:.0f}%</b></td>'
                 f'<td>${t["price"]:,.0f}</td><td style="color:#64748b">{t["phase"]} · MVRV {t["mvrv"]}</td></tr>')
    return (f'<div class="card"><div class="ct">🧾 최근 목표비중 변동 이력</div>'
            f'<table><tr><th>날짜</th><th>액션</th><th>비중</th><th>BTC</th><th>맥락</th></tr>'
            f'{body}</table></div>\n')


def build_stats(res):
    """전기간 백테스트 성과 (참고용)."""
    s = res.get("stats", {})
    if not s:
        return ""
    tiles = [
        (f"{s.get('CAGR','?')}%", "CAGR", "#4ade80"),
        (f"{s.get('Sharpe','?')}", "Sharpe", "#e2e8f0"),
        (f"{s.get('MDD','?')}%", "MDD", "#f87171"),
        (f"{s.get('Calmar','?')}", "Calmar", "#e2e8f0"),
        (f"{s.get('Alpha_vs_BnH','?')}%p", "vs 보유 α", "#facc15"),
        (f"{s.get('Avg_Position','?')}%", "평균 비중", "#94a3b8"),
    ]
    cells = "".join(
        f'<div class="stat"><div class="stat-val" style="font-size:16px;color:{c}">{v}</div>'
        f'<div class="stat-lbl">{l}</div></div>' for v, l, c in tiles)
    return (f'<div class="card"><div class="ct">📊 전기간 성과 ({s.get("Years","?")}년 백테스트, 참고용)</div>'
            f'<div class="g3">{cells}</div></div>\n')


# ══════════════════════════════════════════════════════════════
# 조립
# ══════════════════════════════════════════════════════════════
def generate_html(df, res, ct, engine_name, eng):
    eq = res["equity_curve"]
    row = df.iloc[-1]
    data_date = str(row["Date"])[:10]
    prev_pos = float(eq["Position"].iloc[-2]) if len(eq) >= 2 else ct["target"]
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    header = (
        f'<div class="hdr">'
        f'<h1>💰 SOLIDUS <span style="color:#f7931a">₿TC</span> 브리핑</h1>'
        f'<div>'
        f'<span class="badge bb">{RENDERER_VER}</span> '
        f'<span class="badge bv">데이터 기준일 {data_date}</span> '
        f'<span class="badge bv" id="fg-badge" data-src="{data_date}" data-gen="{now_kst}">⏳ 신선도 확인중</span>'
        f'</div></div>\n')

    footer = (
        f'<div class="footer">'
        f'엔진 {eng.ENGINE_VERSION} · Crown {eng.CROWN_LABEL}<br>'
        f'렌더러 solidus_brief_html {RENDERER_VER} ({engine_name}) · 생성 {now_kst}<br>'
        f'본 페이지는 정보 제공용이며 투자 권유가 아님</div>\n')

    html = (
        f'<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="data-date" content="{data_date}">'
        f'<title>💰 SOLIDUS BTC 브리핑 — {data_date}</title>'
        f'<style>{CSS}</style></head><body>\n'
        + header
        + build_hero(ct, prev_pos)
        + build_cycle(ct, eng)
        + build_core_stats(row, ct)
        + build_conditions(row, ct, eng)
        + build_chart(df, eq)
        + build_trailing(eq)
        + '<div class="g2">' + build_macro(row) + build_onchain(row) + '</div>'
        + build_trades(res)
        + build_stats(res)
        + footer
        + FRESHGUARD_JS
        + '</body></html>')
    return html


# ══════════════════════════════════════════════════════════════
# 🛡️ VGUARD — 버전 표기 무결성 게이트 (ARGUS v9.2.12 이식)
# ══════════════════════════════════════════════════════════════
def version_integrity_check(html):
    import re
    tokens = set(re.findall(r"v\d+\.\d+\.\d+", html))
    # 엔진 버전 문자열(v3.2.0 등)은 허용 — 렌더러 v1.x 토큰만 검사
    r_tokens = {t for t in tokens if t.startswith("v1.")}
    if r_tokens != {RENDERER_VER}:
        print(f"🔴 VGUARD FAIL: 렌더러 버전 표기 불일치 — 발견 {sorted(r_tokens)} / 허용 ['{RENDERER_VER}']")
        sys.exit(1)
    cnt = html.count(RENDERER_VER)
    if cnt < 2:
        print(f"🔴 VGUARD FAIL: RENDERER_VER 출현 {cnt}회 (<2 — 배지·푸터 결손 의심)")
        sys.exit(1)
    print(f"🛡️ VGUARD PASS: 버전 단일 {RENDERER_VER} · 출현 {cnt}회")


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print(f"💰 SOLIDUS HTML 브리핑 생성기 {RENDERER_VER}")
    print("=" * 60)
    eng, engine_name = load_engine()
    df = load_data()

    res = eng.run_solidus(df)          # hysteresis 상태 누적 (정식 경로)
    ct = eng.current_target(df)
    if not ct:
        print("🔴 current_target 산출 실패 — fail-loud 종료")
        sys.exit(1)
    print(f"  📍 {ct['date']} | {ct['phase']} D+{ct['days']} | 🎯 {ct['target']:.0%} ({ct['clause']})")

    html = generate_html(df, res, ct, engine_name, eng)
    version_integrity_check(html)      # 실패 시 여기서 exit(1) — 결함 파일 미출력

    for out in OUT_FILES:
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  ✅ {out} ({len(html):,} bytes)")
    print("완료")


if __name__ == "__main__":
    main()
