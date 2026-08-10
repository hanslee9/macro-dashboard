"""
data_sources.py
거시경제 지표 통합 수집 모듈 (FRED + yfinance)

Streamlit Cloud 배포 시:
  - requirements.txt 에 fredapi, yfinance, pandas 추가
  - Streamlit Secrets 에 FRED_API_KEY 등록 (Public 레포이므로 코드에 직접 넣지 말 것)
"""

import pandas as pd
import streamlit as st
from fredapi import Fred
import yfinance as yf

# ──────────────────────────────────────────────────────────
# FRED 클라이언트
# ──────────────────────────────────────────────────────────
fred = Fred(api_key=st.secrets["FRED_API_KEY"])

# ──────────────────────────────────────────────────────────
# 지표 정의: 카테고리별로 그룹핑 (UI 셀렉트박스에서 그대로 사용)
# source: "fred" | "yfinance"
# freq: 원본 발표 주기 (참고용 표시)
# ──────────────────────────────────────────────────────────
INDICATORS = {

    "경기·성장": {
        "미국 실질GDP성장률":     {"source": "fred", "code": "A191RL1Q225SBEA", "freq": "분기"},
        "미국 CPI":               {"source": "fred", "code": "CPIAUCSL", "freq": "월간"},
        "미국 실업률":            {"source": "fred", "code": "UNRATE", "freq": "월간"},
        "미국 산업생산지수":       {"source": "fred", "code": "INDPRO", "freq": "월간"},
        "미국 소비자심리지수":     {"source": "fred", "code": "UMCSENT", "freq": "월간"},
    },

    "통화·금리": {
        "미국 기준금리(FFR)":     {"source": "fred", "code": "FEDFUNDS", "freq": "월간"},
        "미국 10년 국채금리":     {"source": "fred", "code": "DGS10", "freq": "일간"},
        "미국 2Y-10Y 금리차":     {"source": "fred", "code": "T10Y2Y", "freq": "일간"},
        "미국 M2 통화량":         {"source": "fred", "code": "M2SL", "freq": "월간"},
        "연준 자산규모(Fed B/S)": {"source": "fred", "code": "WALCL", "freq": "주간"},
        "원/달러 환율":           {"source": "fred", "code": "DEXKOUS", "freq": "일간"},
        "달러인덱스(DXY)":        {"source": "yfinance", "code": "DX-Y.NYB", "freq": "일간"},
        "미국 30년 모기지금리":   {"source": "fred", "code": "MORTGAGE30US", "freq": "주간"},
    },

    "자본시장": {
        "S&P500":                {"source": "yfinance", "code": "^GSPC", "freq": "일간"},
        "나스닥100":              {"source": "yfinance", "code": "^NDX", "freq": "일간"},
        "코스피":                 {"source": "yfinance", "code": "^KS11", "freq": "일간"},
        "코스닥":                 {"source": "yfinance", "code": "^KQ11", "freq": "일간"},
        "VIX(변동성지수)":        {"source": "fred", "code": "VIXCLS", "freq": "일간"},
    },

    "신용·부채": {
        "하이일드 스프레드(HY OAS, CDS프록시)": {"source": "fred", "code": "BAMLH0A0HYM2", "freq": "일간"},
        "투자등급 스프레드(IG OAS)":            {"source": "fred", "code": "BAMLC0A0CM", "freq": "일간"},
        "미국 정부부채(총액)":                  {"source": "fred", "code": "GFDEBTN", "freq": "분기"},
        "미국 가계부채":                        {"source": "fred", "code": "HHDNS", "freq": "분기"},
    },

    "외환·무역": {
        "미국 경상수지":          {"source": "fred", "code": "BOPBCA", "freq": "분기"},
        "미국 무역수지":          {"source": "fred", "code": "BOPGSTB", "freq": "월간"},
    },
}


def list_indicator_names():
    """UI 멀티셀렉트용: {카테고리: [지표명, ...]} 형태 반환"""
    return {cat: list(items.keys()) for cat, items in INDICATORS.items()}


def _flatten():
    flat = {}
    for cat, items in INDICATORS.items():
        for name, meta in items.items():
            flat[name] = meta
    return flat


_FLAT = _flatten()


@st.cache_data(ttl=3600, show_spinner=False)
def get_series(indicator_name: str, start: str = "2015-01-01") -> pd.Series:
    """
    지표명 하나를 받아 pandas Series(date index, float value) 반환.
    실패 시 빈 Series 반환 (UI에서 경고 처리).
    """
    meta = _FLAT.get(indicator_name)
    if meta is None:
        return pd.Series(dtype=float)

    try:
        if meta["source"] == "fred":
            s = fred.get_series(meta["code"], observation_start=start)
            s.index = pd.to_datetime(s.index)
            return s.dropna()

        elif meta["source"] == "yfinance":
            df = yf.download(meta["code"], start=start, progress=False)
            if df.empty:
                return pd.Series(dtype=float)
            s = df["Close"]
            if isinstance(s, pd.DataFrame):  # 멀티인덱스 컬럼 방지
                s = s.iloc[:, 0]
            return s.dropna()

    except Exception as e:
        st.warning(f"'{indicator_name}' 데이터 로딩 실패: {e}")
        return pd.Series(dtype=float)

    return pd.Series(dtype=float)


def normalize(series: pd.Series, base=100) -> pd.Series:
    """시작점을 base(기본 100)로 맞춘 정규화 — 서로 단위 다른 지표 비교용"""
    if series.empty:
        return series
    return series / series.iloc[0] * base
