"""
data_sources.py
거시경제 지표 통합 수집 모듈 (FRED + yfinance + FINRA)

Streamlit Cloud 배포 시:
  - requirements.txt 에 fredapi, yfinance, pandas, openpyxl 추가
  - Streamlit Secrets 에 FRED_API_KEY 등록 (Public 레포이므로 코드에 직접 넣지 말 것)
  - FINRA 마진부채는 공개 엑셀 파일을 직접 읽어오므로 별도 API 키 불필요
"""

import io
import requests
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
        "미국 PPI":               {"source": "fred", "code": "PPIACO", "freq": "월간"},
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
        "CBOE 풋/콜비율(주식)":   {"source": "cboe_putcall", "code": "equity", "freq": "일간"},
        "WTI 원유":               {"source": "fred", "code": "DCOILWTICO", "freq": "일간"},
    },

    "신용·부채": {
        "하이일드 스프레드(HY OAS, CDS프록시)": {"source": "fred", "code": "BAMLH0A0HYM2", "freq": "일간"},
        "투자등급 스프레드(IG OAS)":            {"source": "fred", "code": "BAMLC0A0CM", "freq": "일간"},
        "미국 정부부채(총액)":                  {"source": "fred", "code": "GFDEBTN", "freq": "분기"},
        "미국 가계부채":                        {"source": "fred", "code": "HHDNS", "freq": "분기"},
        "마진부채(FINRA, 잔액)":                {"source": "finra_margin", "code": "level", "freq": "월간"},
        "마진부채(FINRA, YoY%)":                {"source": "finra_margin", "code": "yoy", "freq": "월간"},
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

# ──────────────────────────────────────────────────────────
# FINRA 마진부채 (공개 엑셀, 인증 불필요, 1997.01 ~ 현재, 월간)
# ──────────────────────────────────────────────────────────
FINRA_MARGIN_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


@st.cache_data(ttl=6 * 3600, show_spinner=False)  # 월 1회 발표이므로 6시간 캐시로 충분
def _load_finra_margin_debt() -> pd.Series:
    """
    FINRA 공개 엑셀에서 '고객 증권 마진계좌 부채잔액(Debit Balances)'을
    월간 시계열(pandas Series, 단위: 백만달러)로 반환.
    """
    resp = requests.get(FINRA_MARGIN_URL, timeout=20)
    resp.raise_for_status()
    raw = pd.read_excel(io.BytesIO(resp.content), header=None)

    # 헤더 행 위치를 자동 탐색 ('Debit'이 포함된 행)
    header_row_idx = None
    for i, row in raw.iterrows():
        if row.astype(str).str.contains("Debit", case=False, na=False).any():
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError("FINRA 엑셀에서 헤더 행을 찾지 못했습니다.")

    df = pd.read_excel(io.BytesIO(resp.content), header=header_row_idx)
    df.columns = [str(c).strip() for c in df.columns]

    month_col = next(c for c in df.columns if "month" in c.lower() or "year" in c.lower())
    debit_col = next(c for c in df.columns if "debit" in c.lower())

    df = df[[month_col, debit_col]].dropna()
    df.columns = ["month", "debit_balance"]

    # 'Jun-26' 형태 파싱 (일부 파일은 datetime으로 이미 들어있는 경우도 있어 두 방식 시도)
    dates = pd.to_datetime(df["month"], format="%b-%y", errors="coerce")
    if dates.isna().mean() > 0.5:  # 형식이 다르면 일반 파서로 재시도
        dates = pd.to_datetime(df["month"], errors="coerce")

    s = pd.Series(
        pd.to_numeric(df["debit_balance"], errors="coerce").values,
        index=dates,
    ).dropna()
    s = s[s.index.notna()].sort_index()
    s.name = "FINRA_margin_debit_balance"
    return s


def get_margin_debt(mode: str = "level") -> pd.Series:
    """
    mode="level": 마진부채 잔액(백만달러) 원본
    mode="yoy":   전년동월대비 증감률(%)
    """
    s = _load_finra_margin_debt()
    if mode == "yoy":
        return (s.pct_change(12) * 100).dropna()
    return s


# ──────────────────────────────────────────────────────────
# CBOE 풋/콜비율 (공개 CSV, 인증 불필요, 일간)
# ──────────────────────────────────────────────────────────
CBOE_PUTCALL_URLS = {
    "equity": "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv",
    "index":  "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/indexpcarchive.csv",
}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_putcall_ratio(kind: str = "equity") -> pd.Series:
    """
    CBOE 풋/콜비율(P/C Ratio) 일간 시계열 반환.
    kind="equity": 개별주 옵션 기준 (일반적으로 더 널리 참고됨)
    kind="index":  지수 옵션 기준
    """
    url = CBOE_PUTCALL_URLS[kind]
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    # CBOE CSV는 상단에 안내문구 몇 줄이 섞여 있어 헤더 행을 자동 탐색
    raw = pd.read_csv(io.StringIO(resp.text), header=None, on_bad_lines="skip")
    header_row_idx = None
    for i, row in raw.iterrows():
        if row.astype(str).str.contains("P/C Ratio|Trade_date", case=False, na=False, regex=True).any():
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError("CBOE CSV에서 헤더 행을 찾지 못했습니다.")

    df = pd.read_csv(io.StringIO(resp.text), header=header_row_idx, on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]

    date_col = next(c for c in df.columns if "date" in c.lower())
    ratio_col = next(c for c in df.columns if "ratio" in c.lower())

    dates = pd.to_datetime(df[date_col], errors="coerce")
    s = pd.Series(
        pd.to_numeric(df[ratio_col], errors="coerce").values,
        index=dates,
    ).dropna()
    s = s[s.index.notna()].sort_index()
    s.name = f"CBOE_PC_ratio_{kind}"
    return s


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_by_source(source: str, code: str, start: str = "2015-01-01") -> pd.Series:
    """
    source/code 조합으로 직접 데이터를 가져오는 저수준 함수.
    - 사전 정의된 INDICATORS 뿐 아니라, 사용자가 화면에서 즉석으로
      입력한 FRED 코드 / yfinance 티커에도 그대로 재사용됨.
    실패 시 예외를 그대로 올림 (호출부에서 사용자에게 에러 메시지 표시하도록).
    """
    if source == "fred":
        s = fred.get_series(code, observation_start=start)
        s.index = pd.to_datetime(s.index)
        return s.dropna()

    elif source == "yfinance":
        df = yf.download(code, start=start, progress=False)
        if df.empty:
            return pd.Series(dtype=float)
        s = df["Close"]
        if isinstance(s, pd.DataFrame):  # 멀티인덱스 컬럼 방지
            s = s.iloc[:, 0]
        return s.dropna()

    elif source == "finra_margin":
        s = get_margin_debt(mode=code)  # code: "level" | "yoy"
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "cboe_putcall":
        s = get_putcall_ratio(kind=code)  # code: "equity" | "index"
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    else:
        raise ValueError(f"알 수 없는 source: {source}")


def get_series(indicator_name: str, start: str = "2015-01-01") -> pd.Series:
    """
    사전 정의된 지표명(INDICATORS에 등록된 것)을 받아 pandas Series 반환.
    실패 시 빈 Series 반환 (UI에서 경고 처리).
    """
    meta = _FLAT.get(indicator_name)
    if meta is None:
        return pd.Series(dtype=float)

    try:
        return fetch_by_source(meta["source"], meta["code"], start)
    except Exception as e:
        st.warning(f"'{indicator_name}' 데이터 로딩 실패: {e}")
        return pd.Series(dtype=float)


def normalize(series: pd.Series, base=100) -> pd.Series:
    """시작점을 base(기본 100)로 맞춘 정규화 — 서로 단위 다른 지표 비교용"""
    if series.empty:
        return series
    return series / series.iloc[0] * base
