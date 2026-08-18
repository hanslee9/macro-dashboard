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
import numpy as np
import pandas as pd
import streamlit as st
from fredapi import Fred
import yfinance as yf
from dbnomics import fetch_series as _dbnomics_fetch_series

# ──────────────────────────────────────────────────────────
# FRED 클라이언트
# ──────────────────────────────────────────────────────────
fred = Fred(api_key=st.secrets["FRED_API_KEY"])

# ──────────────────────────────────────────────────────────
# ECOS(한국은행) / KOSIS(국가통계포털) API 키
# ──────────────────────────────────────────────────────────
ECOS_API_KEY = st.secrets["ECOS_API_KEY"]
KOSIS_API_KEY = st.secrets["KOSIS_API_KEY"]

# ──────────────────────────────────────────────────────────
# 지표 정의: 카테고리별로 그룹핑 (UI 셀렉트박스에서 그대로 사용)
# source: "fred" | "yfinance"
# freq: 원본 발표 주기 (참고용 표시)
# ──────────────────────────────────────────────────────────
INDICATORS = {

    "한국 주요지표": {
        "코스피지수":             {"source": "yfinance", "code": "^KS11", "freq": "일간"},
        "코스닥지수":             {"source": "yfinance", "code": "^KQ11", "freq": "일간"},
        "코스피 시가총액":         {"source": "kosis_marketcap", "code": "market_cap", "freq": "월간"},  # KOSIS, 코스닥 제외(S&P500과 대응 개념)
        "KOSPI PER(일반)":       {"source": "kosis_per", "code": "per", "freq": "월간"},  # KOSIS tblId=DT_343_2010_S0033
        "KOSPI PBR":             {"source": "kosis_pbr", "code": "pbr", "freq": "월간"},  # KOSIS tblId=DT_343_2010_S0034
        "KOSPI 배당수익률(%)":   {"source": "kosis_dividend", "code": "dividend", "freq": "월간"},  # KOSIS tblId=DT_343_2010_S0032
        "코스피 추세이격률(%)":   {"source": "trend_deviation", "code": "코스피지수", "freq": "일간"},
        "코스피 월간수익률(%)":   {"source": "monthly_return", "code": "코스피지수", "freq": "월간"},
        "한국 명목GDP":           {"source": "fred", "code": "MKTGDPKRA646NWDB", "freq": "연간"},
        "한국 CPI":               {"source": "fred", "code": "KORCPIALLMINMEI", "freq": "월간"},
        "한국 M2 통화량":         {"source": "korea_m2_hardcoded", "code": "m2", "freq": "월간"},  # 한국은행 스냅샷에서 다운로드한 공식데이터 하드코딩(2003.10~2026.06)
        "한국 기준금리":           {"source": "ecos_base_rate", "code": "base_rate", "freq": "일간"},  # 한국은행 공식 발표이력(1999~) 하드코딩, ECOS 실시간 접속차단 대체
        "원/달러 환율":           {"source": "fred", "code": "DEXKOUS", "freq": "일간"},
        "한국 무역수지":           {"source": "korea_trade_balance", "code": "trade", "freq": "분기"},  # FRED 수출-수입 자체계산
        "한국 경상수지":           {"source": "fred", "code": "KORB6BLTT02STSAQ", "freq": "분기"},  # OECD, GDP대비 %
    },

    "미국 거시지표": {
        "미국 명목GDP":           {"source": "fred", "code": "GDP", "freq": "분기"},
        "미국 CPI":               {"source": "fred", "code": "CPIAUCSL", "freq": "월간"},
        "미국 실업률":            {"source": "fred", "code": "UNRATE", "freq": "월간"},
        "비농업고용자수":         {"source": "fred", "code": "PAYEMS", "freq": "월간"},
        "ISM 제조업 PMI":         {"source": "dbnomics", "code": "ISM/pmi/pm", "freq": "월간"},  # 무료 대안소스(DBnomics), 데이터 정합성 검증 필요
        "신규 실업급여 신청 건수":  {"source": "fred", "code": "ICSA", "freq": "주간"},
        "미국 실질GDP성장률":     {"source": "fred", "code": "A191RL1Q225SBEA", "freq": "분기"},
        "근원 CPI":               {"source": "fred", "code": "CPILFESL", "freq": "월간"},
        "소매 매출액":             {"source": "fred", "code": "RSAFS", "freq": "월간"},
        "개인소비지출(PCE)":        {"source": "fred", "code": "PCE", "freq": "월간"},
        "미국 PPI":               {"source": "fred", "code": "PPIACO", "freq": "월간"},
        "미국 산업생산지수":       {"source": "fred", "code": "INDPRO", "freq": "월간"},
        "미국 소비자심리지수":     {"source": "fred", "code": "UMCSENT", "freq": "월간"},
        "OECD 경기선행지수(미국, CLI)": {"source": "fred", "code": "USALOLITONOSTSAM", "freq": "월간"},
        "개인소득":                {"source": "fred", "code": "PI", "freq": "월간"},
        "내구재 수주":             {"source": "fred", "code": "DGORDER", "freq": "월간"},
        "신규 주택허가 건수":       {"source": "fred", "code": "PERMIT", "freq": "월간"},
        "중장비트럭 판매량(선행지표)": {"source": "fred", "code": "HTRUCKSSAAR", "freq": "월간"},
        "미국 경기침체확률(Chauvet-Piger)": {"source": "fred", "code": "RECPROUSM156N", "freq": "월간"},
    },

    "금리·통화": {
        "미국 10년 국채금리":     {"source": "fred", "code": "DGS10", "freq": "일간"},
        "미국 기준금리(FFR, 실효금리)":     {"source": "fred", "code": "FEDFUNDS", "freq": "월간"},
        "미국 2Y-10Y 금리차":     {"source": "fred", "code": "T10Y2Y", "freq": "일간"},
        "달러인덱스(DXY)":        {"source": "yfinance", "code": "DX-Y.NYB", "freq": "일간"},
        "미국 기준금리(목표, 계단식)":      {"source": "fed_target_combined", "code": "target", "freq": "일간"},
        "미국 M2 통화량":         {"source": "fred", "code": "M2SL", "freq": "월간"},
        "연준 자산규모(Fed B/S)": {"source": "fred", "code": "WALCL", "freq": "주간"},
        "미국 30년 모기지금리":   {"source": "fred", "code": "MORTGAGE30US", "freq": "주간"},
        "미국 3개월 국채금리":     {"source": "fred", "code": "DGS3MO", "freq": "일간"},
        "미국 2년 국채금리":       {"source": "fred", "code": "DGS2", "freq": "일간"},
        "엔/달러 환율":           {"source": "fred", "code": "DEXJPUS", "freq": "일간"},
    },

    "지수·주가": {
        "S&P500":                {"source": "yfinance", "code": "^GSPC", "freq": "일간"},
        "나스닥100":              {"source": "yfinance", "code": "^NDX", "freq": "일간"},
        "다우존스":                {"source": "yfinance", "code": "^DJI", "freq": "일간"},
        "니케이225(일본)":        {"source": "yfinance", "code": "^N225", "freq": "일간"},
        "미국 전체 시가총액(조달러)": {"source": "us_market_cap", "code": "market_cap", "freq": "분기"},  # 연준 Z.1 공식 집계, 버핏지수 분자와 동일 원본
        "VIX(변동성지수)":        {"source": "fred", "code": "VIXCLS", "freq": "일간"},
        "CBOE 풋/콜비율(주식)":   {"source": "cboe_putcall", "code": "equity", "freq": "일간"},
    },

    "신용·부채": {
        "하이일드 스프레드(HY OAS, CDS프록시)": {"source": "fred", "code": "BAMLH0A0HYM2", "freq": "일간"},
        "마진부채(FINRA, YoY%)":                {"source": "finra_margin", "code": "yoy", "freq": "월간"},
        "투자등급 스프레드(IG OAS)":            {"source": "fred", "code": "BAMLC0A0CM", "freq": "일간"},
        "마진부채(FINRA, 잔액)":                {"source": "finra_margin", "code": "level", "freq": "월간"},
        "미국 가계부채":                        {"source": "fred", "code": "CMDEBT", "freq": "분기"},
        "미국 정부부채(총액)":                  {"source": "fred", "code": "GFDEBTN", "freq": "분기"},
    },

    "외환·무역": {
        "미국 무역수지":          {"source": "fred", "code": "BOPGSTB", "freq": "월간"},
        "미국 경상수지":          {"source": "fred", "code": "IEABC", "freq": "분기"},
    },

    "원자재": {
        "WTI 원유":               {"source": "fred", "code": "DCOILWTICO", "freq": "일간"},
        "금 선물":                {"source": "yfinance", "code": "GC=F", "freq": "일간"},
        "은 선물":                {"source": "yfinance", "code": "SI=F", "freq": "일간"},
        "발틱운임지수(BDI) 프록시-건화물운임선물ETF": {"source": "yfinance", "code": "BDRY", "freq": "일간"},  # BDI 자체는 유료(Baltic Exchange). BDRY는 Capesize/Panamax/Supramax 운임선물 직접 편입 ETF(2018.3~), 해운주 ETF(SEA)보다 BDI에 더 직접적으로 연동됨
    },

    "밸류에이션 배수": {
        "S&P500 PER(일반)":       {"source": "multpl", "code": "pe", "freq": "월간"},
        "S&P500 CAPE(실러PER)":   {"source": "multpl", "code": "cape", "freq": "월간"},
        "S&P500 PBR(주가순자산)": {"source": "multpl", "code": "price_to_book", "freq": "월간"},
        "S&P500 ROE(자기자본이익률, %)": {"source": "roe", "code": "roe", "freq": "월간"},  # PBR/PER = 이익/순자산 = ROE (근사 아닌 정확한 항등식)
        "S&P500 배당수익률(%)":   {"source": "multpl", "code": "dividend_yield", "freq": "월간"},
        "S&P500 이익수익률(%)":   {"source": "multpl", "code": "earnings_yield", "freq": "월간"},
        "S&P500 PEG비율(트레일링 근사)": {"source": "peg", "code": "peg", "freq": "월간"},  # 향후이익성장률 대신 과거EPS성장률 기준 근사치
        "S&P500 PSR(주가매출)":   {"source": "multpl", "code": "price_to_sales", "freq": "월간"},
        "S&P500 배당성향(Payout Ratio, %)": {"source": "payout_ratio", "code": "payout", "freq": "월간"},  # 배당수익률×PER = 배당/이익 (정확한 항등식)
        "미국 기업이익률(GDP대비, %)": {"source": "fred_ratio", "code": "CP/GDP", "freq": "분기"},  # 기업이익(CP) / GDP — 마진이 역사적으로 높은지 판단하는 참고 지표
    },

    "복합·추세지표": {
        "버핏지수(시총/GDP, %)":  {"source": "buffett_indicator", "code": "ratio", "freq": "분기"},
        "S&P500 추세이격률(%)":  {"source": "trend_deviation", "code": "S&P500", "freq": "일간"},
        "버핏지수 추세이격률(%)": {"source": "buffett_deviation", "code": "pct", "freq": "분기"},
        "S&P500 CAPE 추세이격률(%)": {"source": "trend_deviation", "code": "S&P500 CAPE(실러PER)", "freq": "월간"},
        "나스닥100 추세이격률(%)":   {"source": "trend_deviation", "code": "나스닥100", "freq": "일간"},
        "S&P500 월간수익률(%)":      {"source": "monthly_return", "code": "S&P500", "freq": "월간"},
        "나스닥100 월간수익률(%)":    {"source": "monthly_return", "code": "나스닥100", "freq": "월간"},
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


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_peg_ratio() -> pd.Series:
    """
    S&P500 PEG비율(트레일링 근사치) = 일반PER ÷ (최근 12개월 EPS 전년동월대비 성장률, %)

    정통 PEG 비율은 애널리스트의 '향후' 이익성장률 전망치를 분모로 쓰지만, 이런 예측치는
    무료로 자동 수집할 수 있는 공개 API가 없다(Yardeni, GuruFocus 등 유료 소스만 존재).
    대신 이미 보유한 데이터(multpl PER + S&P500 가격)에서 EPS를 역산해, '과거(trailing)
    이익성장률' 기준으로 근사한 대체 지표를 제공한다. 전망치 기반 PEG보다 후행적이라는
    한계가 있으니 화면에 caveat로 안내할 것.
    """
    pe = get_multpl_series(mode="pe")  # 월별, S&P500 가격 ÷ EPS
    price_df = yf.download("^GSPC", start="1990-01-01", progress=False)
    if price_df.empty:
        raise ValueError("PEG 계산을 위한 S&P500 가격 데이터를 가져오지 못했습니다.")
    price = price_df["Close"]
    if isinstance(price, pd.DataFrame):
        price = price.iloc[:, 0]
    price.index = pd.to_datetime(price.index)

    pe_m = pe.resample("ME").last()
    price_m = price.resample("ME").last()

    combined = pd.DataFrame({"pe": pe_m, "price": price_m}).dropna()
    eps = combined["price"] / combined["pe"]  # EPS 역산
    eps_growth_pct = eps.pct_change(12) * 100  # 전년동월대비 EPS 성장률(%)

    peg = combined["pe"] / eps_growth_pct
    peg = peg[eps_growth_pct > 0]  # 이익 역성장(마이너스) 구간은 PEG 정의상 의미 없어 제외
    peg = peg.dropna()
    peg.name = "sp500_peg_trailing"
    return peg


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_roe_proxy() -> pd.Series:
    """
    S&P500 ROE(자기자본이익률, %) = PBR ÷ PER × 100

    PBR = 주가/순자산, PER = 주가/이익 이므로 PBR÷PER = 이익/순자산 = ROE.
    같은 시점의 '주가'가 분자·분모에서 정확히 상쇄되므로, 이는 근사치가 아니라
    수학적으로 정확한 항등식이다. multpl의 price_to_book, pe 두 시계열만으로 산출한다.
    """
    pbr = get_multpl_series(mode="price_to_book")
    per = get_multpl_series(mode="pe")
    combined = pd.DataFrame({"pbr": pbr, "per": per}).dropna()
    roe = (combined["pbr"] / combined["per"]) * 100
    roe.name = "sp500_roe_pct"
    return roe


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_payout_ratio() -> pd.Series:
    """
    S&P500 배당성향(Payout Ratio, %) = 배당수익률 × PER

    배당수익률 = 배당/주가, PER = 주가/이익 이므로 곱하면 배당/이익 = 배당성향.
    ROE와 마찬가지로 같은 시점의 '주가'가 상쇄되어 근사치가 아닌 정확한 값이다.
    """
    div_yield = get_multpl_series(mode="dividend_yield")
    per = get_multpl_series(mode="pe")
    combined = pd.DataFrame({"dy": div_yield, "per": per}).dropna()
    payout = (combined["dy"] / 100) * combined["per"] * 100  # dividend_yield는 %단위이므로 100으로 나눈 뒤 다시 %로
    payout.name = "sp500_payout_ratio_pct"
    return payout


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_corporate_profit_margin() -> pd.Series:
    """
    미국 기업이익률(GDP대비, %) = 기업이익(CP, FRED) ÷ 명목GDP(FRED) × 100

    S&P500 개별 기업 마진 데이터는 무료로 구하기 어렵지만, 국가 전체 기업이익을
    GDP와 비교하는 이 지표는 '지금 기업 마진이 역사적으로 높은 수준인지'를 가늠하는
    대표적인 무료 매크로 프록시다. CAPE·일반PER 괴리를 해석할 때 참고할 수 있다.
    """
    cp = fred.get_series("CP", observation_start="1947-01-01")
    cp.index = pd.to_datetime(cp.index)
    cp = cp.dropna()

    gdp = fred.get_series("GDP", observation_start="1947-01-01")
    gdp.index = pd.to_datetime(gdp.index)
    gdp = gdp.dropna()

    combined = pd.DataFrame({"cp": cp, "gdp": gdp}).dropna()
    margin = (combined["cp"] / combined["gdp"]) * 100
    margin.name = "us_corporate_profit_margin_pct"
    return margin



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


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_us_market_cap() -> pd.Series:
    """
    미국 전체 기업 시가총액(연준 Z.1 자금순환 통계, BOGZ1LM893064105Q)을
    조 달러(USD Trillion) 단위로 반환. 버핏지수 계산의 분자와 동일한 원본 데이터.
    - FRED 원본 인덱스는 분기 '시작일' 기준이라, 분기 '말일' 기준으로 변환하는
      get_buffett_indicator()와 인덱스가 어긋나 다른 지표와 짝지을 때 월간 리샘플링
      결합 시 표본이 0이 되는 문제가 있었음(같은 달에 값이 존재하지 않게 됨).
      동일하게 분기말일로 맞춰 다른 분기 지표들과 정상적으로 비교되도록 함.
    """
    s = fred.get_series("BOGZ1LM893064105Q", observation_start="1945-01-01")
    s.index = pd.to_datetime(s.index)
    s = s.dropna() / 1_000_000.0  # 백만달러 -> 조달러(트릴리언)
    s = s.resample("QE").last()  # 분기말일로 정렬(버핏지수 등 다른 분기지표와 정합성 확보)
    s.name = "us_market_cap_trillion_usd"
    return s


@st.cache_data(ttl=6 * 3600, show_spinner=False)  # GDP·Z.1 모두 분기 발표라 자주 바뀌지 않음
def get_buffett_indicator() -> pd.Series:
    """
    버핏지수(시가총액/GDP, %) 계산.
    - 분자: 연준 Z.1(자금순환) 통계 "All Sectors; Corporate Equities; Asset, Market Value Levels"
      (BOGZ1LM893064105Q) — 미국 전체 기업주식 시가총액을 직접 집계한 연준 공식 분기 데이터.
      FRED 원본은 1945년 4분기부터 존재함.
    - 분모: 미국 명목GDP(FRED, 분기, 1947년 1분기부터 존재)
    - 이전 버전(to_period 기반 결합)은 실제 배포 후 확인해보니 데이터가 1990년경부터만
      나오는 버그가 있었음 — 분기 정렬 방식을 resample 기반으로 바꿔 더 안정적으로 수정.
    """
    equity_value = fred.get_series("BOGZ1LM893064105Q", observation_start="1945-01-01")
    equity_value.index = pd.to_datetime(equity_value.index)
    equity_value = equity_value.dropna() / 1000.0  # 백만달러 → 십억달러 (GDP와 단위 통일)
    equity_q = equity_value.resample("QE").last()  # 분기말 기준으로 명확히 정렬

    gdp = fred.get_series("GDP", observation_start="1945-01-01")  # 십억달러 단위, 분기
    gdp.index = pd.to_datetime(gdp.index)
    gdp = gdp.dropna()
    gdp_q = gdp.resample("QE").last()

    combined = pd.DataFrame({"equity": equity_q, "gdp": gdp_q}).dropna()
    if combined.empty:
        raise ValueError(
            "버핏지수 계산을 위한 연준 시가총액·GDP 데이터 결합 결과가 비어 있습니다. "
            f"(시가총액 원본 범위: {equity_value.index.min()}~{equity_value.index.max()}, "
            f"GDP 원본 범위: {gdp.index.min()}~{gdp.index.max()})"
        )

    ratio = (combined["equity"] / combined["gdp"]) * 100
    ratio.name = "buffett_indicator_pct"
    return ratio


def compute_trend_deviation_pct(series: pd.Series) -> pd.Series:
    """
    범용 '장기추세선 대비 이격률(%)' 계산 함수.
    - 전체 기간에 로그선형회귀를 적용해 장기추세선을 구하고, 실제값이 그 추세선보다
      몇 % 위/아래에 있는지를 반환. 양수: 추세선 위(고평가), 음수: 추세선 아래(저평가).
    - 값이 0 이하인 지표에는 로그를 취할 수 없어 적용 불가.
    """
    s = series.dropna()
    if len(s) < 20:
        raise ValueError("추세이격률 계산을 위한 표본이 너무 적습니다(최소 20개 필요).")
    if (s <= 0).any():
        raise ValueError("0 이하 값이 포함된 지표에는 추세이격률(로그기반)을 적용할 수 없습니다.")

    x = np.arange(len(s))
    y = np.log(s.values)
    slope, intercept = np.polyfit(x, y, 1)
    trend = np.exp(slope * x + intercept)

    deviation_pct = (s.values / trend - 1) * 100
    return pd.Series(deviation_pct, index=s.index)


def summarize_reversion_stats(deviation: pd.Series, tolerance: float = 0.3) -> dict | None:
    """
    이격률 시계열에서, '현재와 비슷한 크기(±tolerance 비율 이내)의 이격이 과거에 있었을 때
    추세선(이격률 0%)까지 되돌아오는 데 걸린 기간'의 통계를 계산.
    - 회귀 기준: 이격률의 부호가 바뀌는 시점(0%를 다시 통과하는 시점)을 '회귀 완료'로 정의.
    - 반환: {"current_deviation": 현재이격률, "n_episodes": 유사국면 수,
             "median_months": 중앙값(개월), "mean_months": 평균(개월),
             "min_months": 최소, "max_months": 최대} 또는 유사 사례가 없으면 None.
    """
    s = deviation.dropna()
    if len(s) < 10:
        return None

    current_dev = s.iloc[-1]
    if current_dev == 0:
        return None
    current_sign = 1 if current_dev > 0 else -1

    # 국면(같은 부호가 유지되는 구간) 단위로 분할, 각 국면의 '정점 이격폭'과 '지속 기간' 기록
    sign_arr = np.sign(s.values)
    episodes = []
    seg_start = 0
    seg_sign = sign_arr[0] if sign_arr[0] != 0 else current_sign
    seg_peak = s.values[0]

    for i in range(1, len(s)):
        si = sign_arr[i]
        if si != 0 and si != seg_sign:
            episodes.append({
                "sign": seg_sign,
                "peak": seg_peak,
                "start": s.index[seg_start],
                "end": s.index[i],  # 부호가 바뀐 시점 = 회귀 완료 시점
            })
            seg_start = i
            seg_sign = si
            seg_peak = s.values[i]
        else:
            if seg_sign > 0:
                seg_peak = max(seg_peak, s.values[i])
            else:
                seg_peak = min(seg_peak, s.values[i])

    # 지금 '현재 국면'은 아직 회귀가 안 끝난 상태이므로 통계 대상에서 제외하고,
    # 과거에 '완료된' 국면들 중 현재와 부호가 같고 정점 크기가 비슷한 사례만 추출
    similar = [
        e for e in episodes
        if e["sign"] == current_sign
        and abs(e["peak"]) >= abs(current_dev) * (1 - tolerance)
    ]
    if not similar:
        return None

    durations_months = [(e["end"] - e["start"]).days / 30.44 for e in similar]
    return {
        "current_deviation": round(float(current_dev), 1),
        "n_episodes": len(similar),
        "median_months": round(float(np.median(durations_months)), 1),
        "mean_months": round(float(np.mean(durations_months)), 1),
        "min_months": round(float(np.min(durations_months)), 1),
        "max_months": round(float(np.max(durations_months)), 1),
    }


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_buffett_deviation() -> pd.Series:
    """
    버핏지수의 '장기추세선 대비 이격률(%)'을 계산 (compute_trend_deviation_pct 적용).
    - 목적: "격차가 벌어질 때 하락, 좁혀질 때 상승"이라는 가설을 이중축 착시 없이
      정량적으로 검증하기 위한 파생지표. S&P500과 나란히 그려서 이격률의 고점·저점이
      실제 주가 반전 시점과 맞아떨어지는지 시각적으로 확인하는 용도.
    """
    s = get_buffett_indicator().dropna()
    out = compute_trend_deviation_pct(s)
    out.name = "buffett_deviation_pct"
    return out


FED_TARGET_SPLICE_DATE = pd.Timestamp("2008-12-16")  # 연준이 단일목표→범위(상단/하단) 방식으로 전환한 날짜


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_fed_target_rate_combined() -> pd.Series:
    """
    미국 기준금리(목표, 계단식)를 전체 기간 하나로 이어붙여서 반환.
    - 2008년 12월 16일 이전: 연준이 '단일 숫자'로 목표금리를 발표하던 시기 (FRED: DFEDTAR)
    - 2008년 12월 16일 이후: 금융위기로 금리가 0%대까지 내려가며 '범위(상단/하단)'로
      발표 방식이 바뀜 (FRED: DFEDTARU/DFEDTARL). 여기서는 통상 시장에서 대표값으로
      인용되는 상단(DFEDTARU)을 사용해 이어붙임.
    - 목적: 상단/하단을 따로 두면 2008년 이전 구간은 데이터 자체가 없어 혼란을 주므로,
      전체 역사를 하나의 깔끔한 계단식 그래프로 볼 수 있게 통합.
    """
    old = fred.get_series("DFEDTAR", observation_start="1982-01-01")
    old.index = pd.to_datetime(old.index)
    old = old.dropna()
    old = old[old.index < FED_TARGET_SPLICE_DATE]

    new = fred.get_series("DFEDTARU", observation_start="2008-01-01")
    new.index = pd.to_datetime(new.index)
    new = new.dropna()
    new = new[new.index >= FED_TARGET_SPLICE_DATE]

    combined = pd.concat([old, new]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.name = "fed_target_rate_combined"
    return combined


# ──────────────────────────────────────────────────────────
# DBnomics (ISM 제조업 PMI 무료 대안소스)
# ISM 공식 데이터는 유료 구독제이고, FRED의 구 코드(NAPM)는 2016년 이후 갱신 중단됨.
# DBnomics가 ISM 원자료를 무료로 재배포하고 있어 이를 사용하되, 공식 소스가 아니므로
# 값이 비정상적으로 튀는 구간이 실제로 확인됨(2026년 재배포 데이터가 특정 구간에서
# 10~13대로 급락 — 실제 ISM 공식 발표치는 같은 기간 50대 초중반이었음).
# ISM PMI는 역사적으로도 대략 25~75 범위를 벗어난 적이 없으므로, 이 범위를 벗어나는
# 값은 재배포 과정의 오류로 간주해 걸러낸다(최근 구간이 비게 될 수 있음).
# ──────────────────────────────────────────────────────────
DBNOMICS_VALID_RANGE = (25, 75)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _load_dbnomics_series(series_id: str) -> pd.Series:
    df = _dbnomics_fetch_series(series_id)
    s = pd.Series(
        pd.to_numeric(df["value"], errors="coerce").values,
        index=pd.to_datetime(df["period"]),
    ).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    lo, hi = DBNOMICS_VALID_RANGE
    s = s[(s >= lo) & (s <= hi)]  # 재배포 과정 오류로 보이는 비정상값 제거
    s.name = series_id
    return s


def get_dbnomics_series(series_id: str, start: str = "2015-01-01") -> pd.Series:
    s = _load_dbnomics_series(series_id)
    return s[s.index >= pd.to_datetime(start)].dropna()


# ──────────────────────────────────────────────────────────
# ECOS(한국은행 경제통계시스템) 범용 조회 함수
# 통계표코드·항목코드는 ECOS 사이트(ecos.bok.or.kr) 통계검색에서 확인.
# URL 형식: /StatisticSearch/{키}/json/kr/1/{건수}/{통계표코드}/{주기}/{시작}/{종료}/{항목코드}
# ──────────────────────────────────────────────────────────
def _select_primary_item(df: pd.DataFrame, item_col: str, keyword: str) -> pd.DataFrame:
    """
    한 표 안에 여러 세부항목(코스피 vs 코스피200 vs 업종별, 또는 ECOS의 세부 분류 등)이
    섞여 있을 때, '전체'에 해당하는 항목을 원칙에 따라 선택한다. 우선순위:
      1) 항목명이 keyword와 정확히 일치 (예: '코스피')
      2) 항목명에 숫자가 포함되지 않은 항목만 남김(코스피200, 코스피100 같은 하위지수 배제)
         — 그 결과가 항목 1개로 좁혀지면 채택
      3) 위 두 방식으로도 못 좁히면, 데이터가 가장 촘촘한 항목을 임시 대체값으로 사용
         (실제 응답 구조를 사람이 직접 확인해 정확한 항목코드를 고정하는 것을 권장)
    """
    if item_col is None or df[item_col].nunique() <= 1:
        return df

    exact = df[df[item_col].astype(str) == keyword]
    if not exact.empty:
        return exact

    no_digit = df[~df[item_col].astype(str).str.contains(r"\d", regex=True)]
    if not no_digit.empty and no_digit[item_col].nunique() == 1:
        return no_digit

    # 마지막 수단: 데이터가 가장 촘촘한 항목(정확한 항목명 확인 전까지의 임시 대체)
    best_item = df.groupby(item_col).size().idxmax()
    return df[df[item_col] == best_item]


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_ecos_series(stat_code: str, cycle: str, item_code: str = "",
                     start: str = "19900101", end: str = "20301231",
                     item_keyword: str | None = None) -> pd.Series:
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/"
        f"{stat_code}/{cycle}/{start}/{end}/{item_code}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    if "StatisticSearch" not in data:
        err = data.get("RESULT", {}).get("MESSAGE", "알 수 없는 오류")
        raise ValueError(f"ECOS 조회 실패(stat_code={stat_code}): {err}")

    rows = data["StatisticSearch"]["row"]
    df = pd.DataFrame(rows)

    # item_code를 명시적으로 지정하지 않았거나(예: 하위 세부항목이 여러 개인 통계표),
    # ECOS가 항목을 넓게 묶어 여러 ITEM_NAME1이 섞여 돌아올 수 있음.
    # KOSIS와 동일한 원칙(정확한 이름 우선 → 숫자 없는 항목만 좁히기)으로 하나만 선택.
    if "ITEM_NAME1" in df.columns and df["ITEM_NAME1"].nunique() > 1:
        df = _select_primary_item(df, "ITEM_NAME1", item_keyword or "")

    def _parse_time(t: str) -> pd.Timestamp:
        t = str(t)
        if len(t) == 4:        # 연간: YYYY
            return pd.Timestamp(f"{t}-01-01")
        if len(t) == 6:        # 월간: YYYYMM
            return pd.Timestamp(f"{t[:4]}-{t[4:]}-01")
        if len(t) == 5 and "Q" in t.upper():  # 분기: YYYYQ#
            y, q = t[:4], t[-1]
            month = {"1": 1, "2": 4, "3": 7, "4": 10}[q]
            return pd.Timestamp(year=int(y), month=month, day=1)
        return pd.to_datetime(t)

    s = pd.Series(
        pd.to_numeric(df["DATA_VALUE"], errors="coerce").values,
        index=df["TIME"].map(_parse_time),
    ).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = f"ecos_{stat_code}_{item_code}"
    return s


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_korea_base_rate() -> pd.Series:
    """
    한국은행 공식 기준금리(%, 계단식).

    ECOS 실시간 API는 Streamlit Cloud에서 네트워크 단 접속이 차단되어 사용 불가.
    기준금리는 매일 바뀌는 값이 아니라 금융통화위원회가 결정할 때만(연 8회 이하)
    바뀌는 '계단식' 값이라, 변경 시점 전체 이력을 아래에 직접 기록해두고
    각 시점부터 다음 변경 시점까지 그 값을 그대로 유지(step function)하는 방식으로
    처리한다. 위키백과 '한국은행 기준금리' 문서(1999~2023) + 한국은행 공식
    발표자료 기반 최신 정리(2024~2026)를 교차 확인해 작성.

    ⚠ 새로운 금통위 결정이 나오면(대략 연 8회) 아래 리스트 맨 끝에 한 줄만
    추가하면 된다. 이 함수 자체를 다시 손볼 필요는 없음.
    최종 반영일 기준(2026-08-18) 최신값: 2026-07-16, 2.75%
    """
    history = [
        ("1999-05-06", 4.75), ("2000-02-10", 5.00), ("2000-10-05", 5.25),
        ("2001-02-08", 5.00), ("2001-07-05", 4.75), ("2001-08-09", 4.50),
        ("2001-09-19", 4.00), ("2002-05-07", 4.25), ("2003-05-13", 4.00),
        ("2003-07-10", 3.75), ("2004-08-12", 3.50), ("2004-11-11", 3.25),
        ("2005-10-11", 3.50), ("2005-12-08", 3.75), ("2006-02-09", 4.00),
        ("2006-06-08", 4.25), ("2006-08-10", 4.50), ("2007-07-12", 4.75),
        ("2007-08-09", 5.00), ("2008-08-07", 5.25), ("2008-10-09", 5.00),
        ("2008-10-27", 4.25), ("2008-11-07", 4.00), ("2008-12-11", 3.00),
        ("2009-01-09", 2.50), ("2009-02-12", 2.00), ("2010-07-09", 2.25),
        ("2010-11-16", 2.50), ("2011-01-13", 2.75), ("2011-03-10", 3.00),
        ("2011-06-10", 3.25), ("2012-07-12", 3.00), ("2012-10-11", 2.75),
        ("2013-05-09", 2.50), ("2014-08-14", 2.25), ("2014-10-15", 2.00),
        ("2015-03-12", 1.75), ("2015-06-11", 1.50), ("2016-06-09", 1.25),
        ("2017-11-30", 1.50), ("2018-11-30", 1.75), ("2019-07-18", 1.50),
        ("2019-10-16", 1.25), ("2020-03-17", 0.75), ("2020-05-28", 0.50),
        ("2021-08-26", 0.75), ("2021-11-25", 1.00), ("2022-01-14", 1.25),
        ("2022-04-14", 1.50), ("2022-05-26", 1.75), ("2022-07-13", 2.25),
        ("2022-08-25", 2.50), ("2022-10-12", 3.00), ("2022-11-24", 3.25),
        ("2023-01-13", 3.50), ("2024-10-11", 3.25), ("2024-11-28", 3.00),
        ("2025-02-25", 2.75), ("2025-05-29", 2.50), ("2026-07-16", 2.75),
    ]
    idx = pd.to_datetime([d for d, _ in history])
    vals = [v for _, v in history]
    s = pd.Series(vals, index=idx).sort_index()
    s.name = "korea_base_rate_official"
    return s


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_korea_m2() -> pd.Series:
    """
    한국 M2(광의통화, 평잔, 원계열, 월간, 조원 단위).
    KOSIS도 Streamlit Cloud에서 접속 차단(ECOS와 동일 증상, 5개 KOSIS 지표
    전수 테스트로 확인됨)되어, 사용자가 한국은행 금융·경제 스냅샷
    (https://snapshot.bok.or.kr/dashboard/A5)에서 직접 다운로드한 공식
    데이터(2003-10 ~ 2026-06)를 코드에 직접 반영. 기준금리와 동일한 방식.
    ⚠ 매월 새 값이 나오면 아래 리스트 맨 끝에 한 줄만 추가하면 됨.
    """
    history = [
        ("2003-10", 830.3312), ("2003-11", 843.0544), ("2003-12", 849.7732), ("2004-01", 854.5206), ("2004-02", 860.954), ("2004-03", 868.0668),
        ("2004-04", 870.9087), ("2004-05", 873.4175), ("2004-06", 878.8205), ("2004-07", 879.9954), ("2004-08", 885.0234), ("2004-09", 892.677),
        ("2004-10", 893.0754), ("2004-11", 897.2327), ("2004-12", 899.295), ("2005-01", 902.9928), ("2005-02", 910.391), ("2005-03", 922.9425),
        ("2005-04", 930.9891), ("2005-05", 933.6971), ("2005-06", 939.1507), ("2005-07", 948.3728), ("2005-08", 960.979), ("2005-09", 957.4869),
        ("2005-10", 957.3909), ("2005-11", 955.3282), ("2005-12", 960.2352), ("2006-01", 957.3267), ("2006-02", 961.5766), ("2006-03", 965.3913),
        ("2006-04", 971.3943), ("2006-05", 977.3819), ("2006-06", 987.6685), ("2006-07", 991.0335), ("2006-08", 998.0247), ("2006-09", 1011.9629),
        ("2006-10", 1023.3164), ("2006-11", 1030.4374), ("2006-12", 1041.5401), ("2007-01", 1049.5174), ("2007-02", 1057.1589), ("2007-03", 1059.549),
        ("2007-04", 1063.1562), ("2007-05", 1064.9628), ("2007-06", 1075.5342), ("2007-07", 1077.6501), ("2007-08", 1082.9766), ("2007-09", 1087.1704),
        ("2007-10", 1088.1672), ("2007-11", 1089.9731), ("2007-12", 1097.5908), ("2008-01", 1105.9168), ("2008-02", 1117.0837), ("2008-03", 1128.6707),
        ("2008-04", 1143.9566), ("2008-05", 1159.5819), ("2008-06", 1175.8669), ("2008-07", 1190.4749), ("2008-08", 1198.2102), ("2008-09", 1208.1341),
        ("2008-10", 1237.5882), ("2008-11", 1263.5627), ("2008-12", 1286.9228), ("2009-01", 1292.4627), ("2009-02", 1303.3854), ("2009-03", 1315.2935),
        ("2009-04", 1329.9107), ("2009-05", 1336.6042), ("2009-06", 1339.126), ("2009-07", 1350.6515), ("2009-08", 1380.7769), ("2009-09", 1384.4142),
        ("2009-10", 1394.0944), ("2009-11", 1400.4755), ("2009-12", 1399.3239), ("2010-01", 1406.5775), ("2010-02", 1425.0691), ("2010-03", 1435.0325),
        ("2010-04", 1448.9879), ("2010-05", 1456.9093), ("2010-06", 1466.5812), ("2010-07", 1482.4174), ("2010-08", 1488.0333), ("2010-09", 1495.1415),
        ("2010-10", 1498.0339), ("2010-11", 1506.5451), ("2010-12", 1516.914), ("2011-01", 1520.6421), ("2011-02", 1521.6551), ("2011-03", 1530.2232),
        ("2011-04", 1539.0091), ("2011-05", 1547.7252), ("2011-06", 1550.0224), ("2011-07", 1555.99), ("2011-08", 1571.3788), ("2011-09", 1579.257),
        ("2011-10", 1597.9267), ("2011-11", 1603.0131), ("2011-12", 1599.168), ("2012-01", 1603.967), ("2012-02", 1612.0982), ("2012-03", 1618.3207),
        ("2012-04", 1629.4279), ("2012-05", 1633.889), ("2012-06", 1647.3039), ("2012-07", 1655.3281), ("2012-08", 1665.02), ("2012-09", 1665.2816),
        ("2012-10", 1676.7479), ("2012-11", 1675.8119), ("2012-12", 1686.156), ("2013-01", 1698.1316), ("2013-02", 1711.0769), ("2013-03", 1718.3087),
        ("2013-04", 1717.4422), ("2013-05", 1723.9035), ("2013-06", 1735.1686), ("2013-07", 1733.1155), ("2013-08", 1739.7178), ("2013-09", 1761.1101),
        ("2013-10", 1767.7794), ("2013-11", 1782.7299), ("2013-12", 1795.257), ("2014-01", 1799.8469), ("2014-02", 1814.781), ("2014-03", 1826.0904),
        ("2014-04", 1830.8), ("2014-05", 1847.4997), ("2014-06", 1858.2336), ("2014-07", 1869.7302), ("2014-08", 1891.8147), ("2014-09", 1901.6794),
        ("2014-10", 1912.4776), ("2014-11", 1935.697), ("2014-12", 1946.2136), ("2015-01", 1949.4245), ("2015-02", 1966.5437), ("2015-03", 1983.3172),
        ("2015-04", 2006.4843), ("2015-05", 2018.8642), ("2015-06", 2028.0911), ("2015-07", 2046.768), ("2015-08", 2058.8048), ("2015-09", 2069.7571),
        ("2015-10", 2076.0509), ("2015-11", 2084.1855), ("2015-12", 2094.6477), ("2016-01", 2112.0893), ("2016-02", 2129.7841), ("2016-03", 2136.6138),
        ("2016-04", 2145.3859), ("2016-05", 2163.6433), ("2016-06", 2178.0645), ("2016-07", 2193.8894), ("2016-08", 2212.9306), ("2016-09", 2215.0339),
        ("2016-10", 2232.3533), ("2016-11", 2255.8458), ("2016-12", 2265.1366), ("2017-01", 2274.5506), ("2017-02", 2282.6056), ("2017-03", 2294.8769),
        ("2017-04", 2310.9095), ("2017-05", 2321.6933), ("2017-06", 2324.5893), ("2017-07", 2339.1818), ("2017-08", 2354.3219), ("2017-09", 2357.9057),
        ("2017-10", 2377.964), ("2017-11", 2393.2497), ("2017-12", 2404.9057), ("2018-01", 2416.7288), ("2018-02", 2433.4956), ("2018-03", 2437.7192),
        ("2018-04", 2444.5243), ("2018-05", 2455.8814), ("2018-06", 2459.364), ("2018-07", 2472.679), ("2018-08", 2488.3917), ("2018-09", 2486.2514),
        ("2018-10", 2508.905), ("2018-11", 2517.9481), ("2018-12", 2524.3522), ("2019-01", 2550.1864), ("2019-02", 2552.4134), ("2019-03", 2570.4105),
        ("2019-04", 2582.1274), ("2019-05", 2586.7759), ("2019-06", 2606.7743), ("2019-07", 2620.5232), ("2019-08", 2644.9594), ("2019-09", 2662.0012),
        ("2019-10", 2682.0266), ("2019-11", 2710.3288), ("2019-12", 2716.614), ("2020-01", 2743.8279), ("2020-02", 2776.7935), ("2020-03", 2798.6408),
        ("2020-04", 2854.1697), ("2020-05", 2898.3424), ("2020-06", 2919.4118), ("2020-07", 2939.1157), ("2020-08", 2957.9019), ("2020-09", 2982.1035),
        ("2020-10", 3013.0517), ("2020-11", 3039.7658), ("2020-12", 3052.9594), ("2021-01", 3082.1037), ("2021-02", 3112.8056), ("2021-03", 3139.7518),
        ("2021-04", 3176.5157), ("2021-05", 3208.3288), ("2021-06", 3219.4859), ("2021-07", 3257.0563), ("2021-08", 3293.2403), ("2021-09", 3314.9106),
        ("2021-10", 3350.8414), ("2021-11", 3394.5459), ("2021-12", 3411.438), ("2022-01", 3449.5163), ("2022-02", 3487.0903), ("2022-03", 3490.1077),
        ("2022-04", 3501.103), ("2022-05", 3531.043), ("2022-06", 3551.5212), ("2022-07", 3568.8021), ("2022-08", 3600.817), ("2022-09", 3609.7369),
        ("2022-10", 3632.4942), ("2022-11", 3680.5442), ("2022-12", 3682.2961), ("2023-01", 3689.9501), ("2023-02", 3695.0988), ("2023-03", 3681.9329),
        ("2023-04", 3668.5753), ("2023-05", 3660.1347), ("2023-06", 3677.1903), ("2023-07", 3706.867), ("2023-08", 3705.5423), ("2023-09", 3711.1255),
        ("2023-10", 3725.3847), ("2023-11", 3753.1987), ("2023-12", 3746.2654), ("2024-01", 3737.15), ("2024-02", 3762.4214), ("2024-03", 3804.5027),
        ("2024-04", 3808.0166), ("2024-05", 3809.6043), ("2024-06", 3810.9253), ("2024-07", 3823.6121), ("2024-08", 3834.0454), ("2024-09", 3850.11),
        ("2024-10", 3856.8302), ("2024-11", 3877.6535), ("2024-12", 3899.8838), ("2025-01", 3923.8137), ("2025-02", 3930.0325), ("2025-03", 3914.4253),
        ("2025-04", 3930.1172), ("2025-05", 3955.6771), ("2025-06", 3975.3556), ("2025-07", 3994.1219), ("2025-08", 4038.586), ("2025-09", 4059.8043),
        ("2025-10", 4057.9785), ("2025-11", 4063.1749), ("2025-12", 4081.3601), ("2026-01", 4113.5223), ("2026-02", 4113.7286), ("2026-03", 4128.5884),
        ("2026-04", 4152.205), ("2026-05", 4183.5792), ("2026-06", 4212.9554),
    ]
    idx = pd.to_datetime([f"{d}-01" for d, _ in history])
    vals = [v for _, v in history]
    s = pd.Series(vals, index=idx).sort_index()
    s.name = "korea_m2"
    return s


# ──────────────────────────────────────────────────────────
# KOSIS(국가통계포털) 범용 조회 함수
# tblId는 KOSIS 통계표 화면(orgId=343, 한국거래소 통계)에서 확인.
# ──────────────────────────────────────────────────────────
@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_kosis_series(tbl_id: str, org_id: str = "343",
                      start: str = "199001", end: str = "203012",
                      item_keyword: str = "코스피") -> pd.Series:
    url = (
        "https://kosis.kr/openapi/Param/statisticsParameterData.do"
        f"?method=getList&apiKey={KOSIS_API_KEY}&itmId=ALL&objL1=ALL"
        f"&format=json&jsonVD=Y&prdSe=M&startPrdDe={start}&endPrdDe={end}"
        f"&orgId={org_id}&tblId={tbl_id}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict) and "err" in data:
        raise ValueError(f"KOSIS 조회 실패(tblId={tbl_id}): {data.get('errMsg', data['err'])}")

    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError(f"KOSIS 조회 결과가 비어 있습니다(tblId={tbl_id}).")

    # 이 표에는 시장구분·업종 등 여러 세부항목이 동시에 담겨 있을 수 있음.
    # itmId=ALL로 요청하면 그 항목들이 한 응답에 섞여서 오는데, 이를 구분하지 않고
    # 그대로 쓰면 날짜별로 서로 다른 항목이 뒤섞여 값이 튀는 문제가 생김
    # (예: 코스피 PER이 특정 구간에서만 갑자기 200~500대로 치솟는 오류).
    item_col = "ITM_ID" if "ITM_ID" in df.columns else ("ITM_NM" if "ITM_NM" in df.columns else None)
    df = _select_primary_item(df, item_col, item_keyword)

    def _parse_prd(t: str) -> pd.Timestamp:
        t = str(t)
        if len(t) == 4:
            return pd.Timestamp(f"{t}-01-01")
        if len(t) == 6:
            return pd.Timestamp(f"{t[:4]}-{t[4:]}-01")
        return pd.to_datetime(t)

    s = pd.Series(
        pd.to_numeric(df["DT"], errors="coerce").values,
        index=df["PRD_DE"].map(_parse_prd),
    ).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = f"kosis_{tbl_id}"
    return s


def _get_kospi_index_fundamental() -> pd.DataFrame:
    """
    pykrx로 코스피 지수(코드 1001) 전체의 일별 PER/PBR/배당수익률을 조회.
    pykrx는 KRX 로그인(계정 ID/PW, 개인 실명이 아닌 일반 회원가입 계정)이 필요하며,
    Streamlit Secrets의 KRX_ID/KRX_PW를 환경변수로 주입해 사용한다.
    KRX 서버 응답 속도상 전체 기간(2000년대~현재)을 한 번에 긁으면 느릴 수 있어
    12시간 캐시로 부담을 줄인다.
    """
    import os
    if "KRX_ID" not in st.secrets or "KRX_PW" not in st.secrets:
        raise ValueError(
            "KRX_ID/KRX_PW가 Streamlit Secrets에 설정되어 있지 않습니다. "
            "KRX 계정 정보를 Secrets에 등록해주세요."
        )
    os.environ["KRX_ID"] = st.secrets["KRX_ID"]
    os.environ["KRX_PW"] = st.secrets["KRX_PW"]

    from pykrx import stock

    end = pd.Timestamp.today().strftime("%Y%m%d")
    df = stock.get_index_fundamental("20000101", end, "1001")  # 1001 = 코스피
    if df is None or df.empty:
        raise ValueError("pykrx(KRX) 조회 결과가 비어 있습니다(로그인 실패 가능성 포함).")
    df.index = pd.to_datetime(df.index)
    return df


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_kospi_per() -> pd.Series:
    """코스피 PER(일반). pykrx(KRX 로그인) 경유."""
    df = _get_kospi_index_fundamental()
    s = pd.to_numeric(df["PER"], errors="coerce").dropna()
    s.name = "kospi_per"
    return s


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_kospi_pbr() -> pd.Series:
    """코스피 PBR. pykrx(KRX 로그인) 경유."""
    df = _get_kospi_index_fundamental()
    s = pd.to_numeric(df["PBR"], errors="coerce").dropna()
    s.name = "kospi_pbr"
    return s


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_kospi_dividend_yield() -> pd.Series:
    """코스피 배당수익률(%). pykrx(KRX 로그인) 경유."""
    df = _get_kospi_index_fundamental()
    s = pd.to_numeric(df["배당수익률"], errors="coerce").dropna()
    s.name = "kospi_dividend_yield"
    return s


def get_kospi_market_cap() -> pd.Series:
    """
    코스피 시가총액.
    pykrx의 지수 펀더멘털 함수는 PER/PBR/배당수익률만 제공하고 시가총액은
    포함하지 않는다(개별 종목 시가총액을 전부 합산해야 해서 훨씬 무거운 작업).
    당장은 결측 처리.
    """
    raise ValueError("해당 데이터를 불러올 수 없습니다.")


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_korea_trade_balance() -> pd.Series:
    """한국 무역수지 = 수출 - 수입 (FRED, 분기, 자체계산)."""
    exports = fred.get_series("XTEXVA01KRQ667N", observation_start="1990-01-01")
    imports = fred.get_series("XTIMVA01KRQ667S", observation_start="1990-01-01")
    exports.index = pd.to_datetime(exports.index)
    imports.index = pd.to_datetime(imports.index)
    combined = pd.DataFrame({"exports": exports, "imports": imports}).dropna()
    balance = combined["exports"] - combined["imports"]
    balance.name = "korea_trade_balance"
    return balance.dropna()


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

    elif source == "buffett_deviation":
        s = get_buffett_deviation()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "fed_target_combined":
        s = get_fed_target_rate_combined()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "trend_deviation":
        # code: 원본 지표명(INDICATORS에 등록된 이름) — 그 지표의 추세이격률을 계산해서 반환
        base = get_series(code, start=start)
        return compute_trend_deviation_pct(base)

    elif source == "monthly_return":
        # code: 원본 지표명(INDICATORS에 등록된 이름) — 그 지표의 '전월대비 변화율(%)'을 반환.
        # 목적: GDP성장률처럼 이미 '변화율(정체형)'인 지표와 비교할 때, 가격(추세형) 대신
        # 이 지표를 짝지어야 허위상관(추세혼입) 문제를 피할 수 있음.
        base = get_series(code, start=start).dropna()
        monthly = base.resample("ME").last()
        pct = monthly.pct_change().dropna() * 100
        pct.name = f"{code}_monthly_return_pct"
        return pct

    elif source == "multpl":
        s = get_multpl_series(mode=code)
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "dbnomics":
        return get_dbnomics_series(code, start=start)

    elif source == "us_market_cap":
        s = get_us_market_cap()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "peg":
        s = get_peg_ratio()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "roe":
        s = get_roe_proxy()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "payout_ratio":
        s = get_payout_ratio()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "fred_ratio":
        # code: "CP/GDP" 형태만 우선 지원(기업이익률 전용). 향후 다른 비율 추가 시 일반화 가능.
        if code == "CP/GDP":
            s = get_corporate_profit_margin()
        else:
            raise ValueError(f"알 수 없는 fred_ratio code: {code}")
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "ecos_base_rate":
        s = get_korea_base_rate()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "korea_m2_hardcoded":
        s = get_korea_m2()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()


    elif source == "kosis_per":
        s = get_kospi_per()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "kosis_pbr":
        s = get_kospi_pbr()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "kosis_dividend":
        s = get_kospi_dividend_yield()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "kosis_marketcap":
        s = get_kospi_market_cap()
        s = s[s.index >= pd.to_datetime(start)]
        return s.dropna()

    elif source == "korea_trade_balance":
        s = get_korea_trade_balance()
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
