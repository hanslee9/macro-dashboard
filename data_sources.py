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
        "S&P500 PER(일반)":      {"source": "shiller", "code": "pe", "freq": "월간"},
        "S&P500 CAPE(실러PER)":  {"source": "shiller", "code": "cape", "freq": "월간"},
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
# 실러(Yale) S&P500 PER / CAPE 데이터 (공개 엑셀, 인증 불필요, 1871~현재, 월간)
# ──────────────────────────────────────────────────────────
SHILLER_DATA_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


def _shiller_month_to_date(val: float) -> pd.Timestamp:
    """Shiller 데이터의 'YYYY.MM' 형태 날짜값(예: 1999.01, 1999.1)을 실제 날짜로 변환."""
    year = int(val)
    month = int(round((val - year) * 100))
    month = max(1, min(month, 12))
    return pd.Timestamp(year=year, month=month, day=1)


@st.cache_data(ttl=24 * 3600, show_spinner=False)  # 월 1회 업데이트라 하루 캐시로 충분
def _load_shiller_data() -> pd.DataFrame:
    """
    예일대 로버트 실러 교수의 공개 데이터셋에서
    S&P500 일반 PER과 CAPE(경기조정PER)를 월간 시계열로 반환.
    반환: DataFrame(index=월별 날짜, columns=['pe', 'cape'])
    """
    resp = requests.get(
        SHILLER_DATA_URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; macro-dashboard/1.0)"},
    )
    resp.raise_for_status()
    raw_bytes = io.BytesIO(resp.content)

    xls = pd.ExcelFile(raw_bytes)

    # 1) 시트 선택: 이름이 'data'인 시트를 우선 사용(공식 문서·다수 파서가 이 이름을 씀), 없으면 CAPE 텍스트로 탐색
    target_sheet = None
    for name in xls.sheet_names:
        if name.strip().lower() == "data":
            target_sheet = name
            break
    if target_sheet is None:
        for name in xls.sheet_names:
            preview = xls.parse(name, header=None, nrows=20)
            if preview.astype(str).apply(lambda col: col.str.contains("CAPE", case=False, na=False)).any().any():
                target_sheet = name
                break
    if target_sheet is None:
        raise ValueError(f"실러 데이터 파일에서 대상 시트를 찾지 못했습니다. (시트 목록: {xls.sheet_names})")

    # 2) 헤더 행 탐색: ie_data.xls는 헤더가 2줄로 나뉘어 있어(상단 대분류/하단 세부라벨),
    #    단순히 "Date"라는 셀 하나만 찾으면 잘못된 줄(상단 장식 줄)을 잡을 수 있음.
    #    그래서 "Date / Real Price / Real Earnings / CAPE"가 전부 존재하는 행을 검증까지 마친 후 채택.
    #    다수의 외부 파서가 공통으로 쓰는 8번째 행(인덱스 7)을 최우선 후보로 시도.
    raw = xls.parse(target_sheet, header=None, nrows=30)

    def _try_header_row(idx: int):
        try:
            trial = xls.parse(target_sheet, header=idx, nrows=5)
        except Exception:
            return None
        cols = [str(c).strip() for c in trial.columns]
        has_date = any(c.lower() == "date" for c in cols)
        has_cape = any(c.upper() == "CAPE" for c in cols)
        has_price = any("Real Price" in c for c in cols)
        has_earn = any("Real Earnings" in c for c in cols)
        if has_date and has_cape and has_price and has_earn:
            return cols
        return None

    header_row_idx = None
    candidate_rows = [7] + [i for i in range(min(len(raw), 20)) if i != 7]
    for idx in candidate_rows:
        if _try_header_row(idx) is not None:
            header_row_idx = idx
            break

    if header_row_idx is None:
        raise ValueError(
            "실러 데이터 파일에서 'Date/Real Price/Real Earnings/CAPE'가 모두 포함된 헤더 행을 찾지 못했습니다. "
            f"(파일 형식이 바뀌었을 수 있습니다. 시트: {target_sheet})"
        )

    df = xls.parse(target_sheet, header=header_row_idx)
    df.columns = [str(c).strip() for c in df.columns]

    try:
        date_col = next(c for c in df.columns if c.strip().lower() == "date")
        price_col = next(c for c in df.columns if "Real Price" in c)      # 'Real Total Return Price' 등은 제외되도록 정확 매칭
        earn_col = next(c for c in df.columns if "Real Earnings" in c)    # 'Real TR Scaled Earnings' 등은 제외
        cape_col = next(c for c in df.columns if c.strip().upper() == "CAPE")
    except StopIteration:
        raise ValueError(
            f"실러 데이터 파일의 컬럼 구조를 인식하지 못했습니다. "
            f"(헤더 행 {header_row_idx}, 실제 컬럼: {list(df.columns)[:15]})"
        )

    dates = df[date_col].dropna().apply(lambda v: _shiller_month_to_date(float(v)))
    pe = pd.to_numeric(df[price_col], errors="coerce") / pd.to_numeric(df[earn_col], errors="coerce")
    cape = pd.to_numeric(df[cape_col], errors="coerce")

    out = pd.DataFrame({"pe": pe.values, "cape": cape.values}, index=dates.values)
    out.index = pd.to_datetime(out.index)
    out = out.sort_index().dropna(how="all")

    if out.empty:
        raise ValueError("실러 데이터 파싱 결과가 비어 있습니다.")

    return out


def get_shiller_series(mode: str = "pe") -> pd.Series:
    """mode='pe': 일반 트레일링 PER, mode='cape': 실러 CAPE(경기조정PER)"""
    df = _load_shiller_data()
    s = df[mode].dropna()
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


@st.cache_data(ttl=6 * 3600, show_spinner=False)  # GDP는 분기 발표라 자주 바뀌지 않음
def get_buffett_indicator() -> pd.Series:
    """
    버핏지수(시가총액/GDP, %) 계산.
    - 분자: Wilshire 5000 전체시장지수(야후파이낸스 ^W5000) — FRED는 2024.6 자체 Wilshire 데이터 제공 중단
    - 분모: 미국 명목GDP(FRED, 분기) → 일간으로 ffill 후 나눔
    - 참고: Wilshire 지수 1포인트 ≈ 시가총액 10억달러라는 근사 관계를 사용한 관례적 계산법이며,
      최근 시점에는 이 근사치가 다소 벌어졌다는 지적이 있어 절대수준(%)은 참고용으로만 활용 권장.
      (추세·상관관계 분석 목적에는 문제없음)
    """
    wilshire = fred_like_yf = None
    df = yf.download("^W5000", start="1990-01-01", progress=False)
    if df.empty:
        raise ValueError("Wilshire 5000(^W5000) 데이터를 가져오지 못했습니다.")
    wilshire = df["Close"]
    if isinstance(wilshire, pd.DataFrame):
        wilshire = wilshire.iloc[:, 0]
    wilshire = wilshire.dropna()

    gdp = fred.get_series("GDP", observation_start="1990-01-01")  # 십억달러 단위
    gdp.index = pd.to_datetime(gdp.index)
    gdp = gdp.dropna()

    # GDP(분기)를 일간으로 확장(직전 값 유지) 후 Wilshire와 정렬
    combined = pd.DataFrame({"wilshire": wilshire}).sort_index()
    combined["gdp"] = gdp.reindex(combined.index, method="ffill")
    combined = combined.dropna()

    # Wilshire는 포인트 단위(≈ 십억달러 시가총액 근사), GDP는 십억달러 → 비율*100 = %
    ratio = (combined["wilshire"] / combined["gdp"]) * 100
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

    elif source == "shiller":
        s = get_shiller_series(mode=code)  # code: "pe" | "cape"
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
