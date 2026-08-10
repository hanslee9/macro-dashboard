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

    "미국 거시지표": {
        "미국 실질GDP성장률":     {"source": "fred", "code": "A191RL1Q225SBEA", "freq": "분기"},
        "미국 명목GDP":           {"source": "fred", "code": "GDP", "freq": "분기"},
        "비농업고용자수":         {"source": "fred", "code": "PAYEMS", "freq": "월간"},
        "미국 CPI":               {"source": "fred", "code": "CPIAUCSL", "freq": "월간"},
        "근원 CPI":               {"source": "fred", "code": "CPILFESL", "freq": "월간"},
        "미국 PPI":               {"source": "fred", "code": "PPIACO", "freq": "월간"},
        "미국 실업률":            {"source": "fred", "code": "UNRATE", "freq": "월간"},
        "미국 산업생산지수":       {"source": "fred", "code": "INDPRO", "freq": "월간"},
        "미국 소비자심리지수":     {"source": "fred", "code": "UMCSENT", "freq": "월간"},
    },

    "금리·통화": {
        "미국 기준금리(FFR, 실효금리)":     {"source": "fred", "code": "FEDFUNDS", "freq": "월간"},
        "미국 기준금리(목표상단)":          {"source": "fred", "code": "DFEDTARU", "freq": "일간"},
        "미국 기준금리(목표하단)":          {"source": "fred", "code": "DFEDTARL", "freq": "일간"},
        "미국 3개월 국채금리":     {"source": "fred", "code": "DGS3MO", "freq": "일간"},
        "미국 2년 국채금리":       {"source": "fred", "code": "DGS2", "freq": "일간"},
        "미국 10년 국채금리":     {"source": "fred", "code": "DGS10", "freq": "일간"},
        "미국 2Y-10Y 금리차":     {"source": "fred", "code": "T10Y2Y", "freq": "일간"},
        "미국 M2 통화량":         {"source": "fred", "code": "M2SL", "freq": "월간"},
        "연준 자산규모(Fed B/S)": {"source": "fred", "code": "WALCL", "freq": "주간"},
        "미국 30년 모기지금리":   {"source": "fred", "code": "MORTGAGE30US", "freq": "주간"},
        "원/달러 환율":           {"source": "fred", "code": "DEXKOUS", "freq": "일간"},
        "엔/달러 환율":           {"source": "fred", "code": "DEXJPUS", "freq": "일간"},
        "달러인덱스(DXY)":        {"source": "yfinance", "code": "DX-Y.NYB", "freq": "일간"},
    },

    "지수·주가": {
        "S&P500":                {"source": "yfinance", "code": "^GSPC", "freq": "일간"},
        "나스닥100":              {"source": "yfinance", "code": "^NDX", "freq": "일간"},
        "다우존스":                {"source": "yfinance", "code": "^DJI", "freq": "일간"},
        "코스피":                 {"source": "yfinance", "code": "^KS11", "freq": "일간"},
        "코스닥":                 {"source": "yfinance", "code": "^KQ11", "freq": "일간"},
        "니케이225(일본)":        {"source": "yfinance", "code": "^N225", "freq": "일간"},
        "VIX(변동성지수)":        {"source": "fred", "code": "VIXCLS", "freq": "일간"},
        "CBOE 풋/콜비율(주식)":   {"source": "cboe_putcall", "code": "equity", "freq": "일간"},
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

    "원자재": {
        "WTI 원유":               {"source": "fred", "code": "DCOILWTICO", "freq": "일간"},
        "금 선물":                {"source": "yfinance", "code": "GC=F", "freq": "일간"},
        "은 선물":                {"source": "yfinance", "code": "SI=F", "freq": "일간"},
    },

    "밸류에이션(복합지표)": {
        "버핏지수(시총/GDP, %)": {"source": "buffett_indicator", "code": "ratio", "freq": "일간(GDP는 분기, ffill)"},
        "S&P500 PER(일반)":       {"source": "multpl", "code": "pe", "freq": "월간"},
        "S&P500 CAPE(실러PER)":   {"source": "multpl", "code": "cape", "freq": "월간"},
        "S&P500 배당수익률(%)":   {"source": "multpl", "code": "dividend_yield", "freq": "월간"},
        "S&P500 이익수익률(%)":   {"source": "multpl", "code": "earnings_yield", "freq": "월간"},
        "S&P500 PBR(주가순자산)": {"source": "multpl", "code": "price_to_book", "freq": "월간"},
        "S&P500 PSR(주가매출)":   {"source": "multpl", "code": "price_to_sales", "freq": "월간"},
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
# S&P500 밸류에이션 지표 (multpl.com 공개 HTML 표, 1871~현재, 월간)
# 예일대 실러 교수 원본 엑셀은 헤더가 2줄로 쪼개진 복잡한 구조라 파싱이 계속 깨져서
# (여러 차례 시도 실패), 같은 데이터를 "Date/Value" 두 컬럼짜리 HTML 표로 정리해서
# 제공하는 multpl.com으로 소스를 변경. 사이트맵 확인 결과 같은 방식으로 여러
# 밸류에이션 지표를 더 제공하고 있어(FRED/야후에는 없는 것들), 함께 등록함.
# ──────────────────────────────────────────────────────────
MULTPL_URLS = {
    "cape":            "https://www.multpl.com/shiller-pe/table/by-month",
    "pe":              "https://www.multpl.com/s-p-500-pe-ratio/table/by-month",
    "dividend_yield":  "https://www.multpl.com/s-p-500-dividend-yield/table/by-month",
    "earnings_yield":  "https://www.multpl.com/s-p-500-earnings-yield/table/by-month",
    "price_to_book":   "https://www.multpl.com/s-p-500-price-to-book/table/by-month",
    "price_to_sales":  "https://www.multpl.com/s-p-500-price-to-sales/table/by-month",
}


@st.cache_data(ttl=24 * 3600, show_spinner=False)  # 월 1회 업데이트라 하루 캐시로 충분
def get_multpl_series(mode: str) -> pd.Series:
    """
    multpl.com의 "Date | Value" 형태 HTML 표를 그대로 파싱하는 범용 함수.
    mode: MULTPL_URLS의 키 중 하나
      - 'pe': 일반 트레일링 PER
      - 'cape': 실러 CAPE(경기조정PER)
      - 'dividend_yield': S&P500 배당수익률
      - 'earnings_yield': S&P500 이익수익률(1/PER)
      - 'price_to_book': S&P500 주가순자산비율(PBR)
      - 'price_to_sales': S&P500 주가매출비율(PSR)
    """
    if mode not in MULTPL_URLS:
        raise ValueError(f"알 수 없는 mode: {mode}")

    url = MULTPL_URLS[mode]
    resp = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; macro-dashboard/1.0)"},
    )
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    target = None
    for t in tables:
        cols = [str(c).strip().lower() for c in t.columns]
        if "date" in cols and "value" in cols:
            target = t
            break
    if target is None:
        raise ValueError(f"multpl.com 페이지에서 Date/Value 표를 찾지 못했습니다. (표 개수: {len(tables)})")

    target.columns = [str(c).strip().lower() for c in target.columns]
    dates = pd.to_datetime(target["date"], format="%b %d, %Y", errors="coerce")
    # 배당수익률·이익수익률 등은 '3.45%' 형태 문자열일 수 있어 % 기호 제거 후 숫자 변환
    raw_values = target["value"].astype(str).str.replace("%", "", regex=False).str.strip()
    values = pd.to_numeric(raw_values, errors="coerce")

    s = pd.Series(values.values, index=dates.values).dropna()
    s.index = pd.to_datetime(s.index)
    s = s[s.index.notna()].sort_index()

    if s.empty:
        raise ValueError("multpl.com 데이터 파싱 결과가 비어 있습니다.")

    s.name = f"shiller_{mode}"
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


@st.cache_data(ttl=6 * 3600, show_spinner=False)  # GDP·Z.1 모두 분기 발표라 자주 바뀌지 않음
def get_buffett_indicator() -> pd.Series:
    """
    버핏지수(시가총액/GDP, %) 계산.
    - 분자: 연준 Z.1(자금순환) 통계 "All Sectors; Corporate Equities; Asset, Market Value Levels"
      (BOGZ1LM893064105Q) — 미국 전체 기업주식 시가총액을 직접 집계한 연준 공식 분기 데이터.
    - 분모: 미국 명목GDP(FRED, 분기)
    - 이전 버전은 야후파이낸스 Wilshire5000(^W5000) 지수를 "1포인트≈10억달러"로 근사해서 썼는데,
      실제 배포 후 확인해보니 S&P500과 그래프 모양이 거의 구분 안 될 정도로 닮아 있어 신뢰도 문제가
      의심됨. 두 시계열 다 연준·정부 공식 분기 통계로 교체해서 이 문제를 근본적으로 해결함.
    """
    equity_value = fred.get_series("BOGZ1LM893064105Q", observation_start="1950-01-01")
    equity_value.index = pd.to_datetime(equity_value.index)
    equity_value = equity_value.dropna() / 1000.0  # 백만달러 → 십억달러 (GDP와 단위 통일)

    gdp = fred.get_series("GDP", observation_start="1950-01-01")  # 십억달러 단위, 분기
    gdp.index = pd.to_datetime(gdp.index)
    gdp = gdp.dropna()

    # 두 시계열 모두 분기 데이터이므로 분기(Period) 단위로 정렬해서 결합
    eq_q = equity_value.copy()
    eq_q.index = eq_q.index.to_period("Q")
    gdp_q = gdp.copy()
    gdp_q.index = gdp_q.index.to_period("Q")

    combined = pd.DataFrame({"equity": eq_q, "gdp": gdp_q}).dropna()
    if combined.empty:
        raise ValueError("버핏지수 계산을 위한 연준 시가총액·GDP 데이터 결합 결과가 비어 있습니다.")

    ratio = (combined["equity"] / combined["gdp"]) * 100
    ratio.index = ratio.index.to_timestamp()
    ratio.name = "buffett_indicator_pct"
    return ratio


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

    elif source == "buffett_indicator":
        s = get_buffett_indicator()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "multpl":
        s = get_multpl_series(mode=code)
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
