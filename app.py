"""
app.py
거시경제 지표 선택형 비교 대시보드 (독립 앱)

기존 백테스트 레포와는 완전히 별개의 프로젝트입니다.
새 GitHub 레포(예: macro-dashboard)를 만들어 이 파일과
data_sources.py, requirements.txt 만 올려서 별도로 Streamlit Cloud에 배포하세요.

requirements.txt:
    streamlit
    pandas
    fredapi
    yfinance
    plotly
"""

import itertools
import warnings
import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from scipy.stats import pearsonr
from statsmodels.tsa.stattools import grangercausalitytests

from data_sources import (
    list_indicator_names, get_series, normalize, fetch_by_source, summarize_reversion_stats,
)
from correlation_narratives import get_narrative

st.set_page_config(page_title="거시경제 지표 대시보드", layout="wide")


def _compute_pair_correlation(s1: pd.Series, s2: pd.Series):
    """두 시계열을 월간으로 정렬 후 피어슨 상관계수 계산. 표본 3개월 미만이면 None."""
    combined = pd.DataFrame({"a": s1, "b": s2}).sort_index()
    combined = combined.resample("ME").last().dropna()
    if len(combined) < 3:
        return None
    corr, pval = pearsonr(combined["a"], combined["b"])
    return {
        "corr": corr,
        "pval": pval,
        "n": len(combined),
        "start": combined.index.min(),
        "end": combined.index.max(),
    }


LAG_MONTHS = [0, 3, 6, 12]


def _compute_lag_correlations(s_a: pd.Series, s_b: pd.Series, lags=LAG_MONTHS):
    """
    a(t-lag) vs b(t) 상관계수를 lag별로 계산.
    lag>0: a가 b를 lag개월 선행한다고 가정하는 방향.
    표본이 부족한 lag는 결과에서 corr=None으로 표시.
    """
    a_m = s_a.dropna().resample("ME").last()
    b_m = s_b.dropna().resample("ME").last()
    results = []
    for lag in lags:
        a_shifted = a_m.shift(lag)
        combined = pd.DataFrame({"a": a_shifted, "b": b_m}).dropna()
        if len(combined) < 6:
            results.append({"lag": lag, "corr": None, "n": len(combined)})
            continue
        corr, _ = pearsonr(combined["a"], combined["b"])
        results.append({"lag": lag, "corr": corr, "n": len(combined)})
    return results


def _dynamic_lag_observation(name_a: str, name_b: str, lag_results: list) -> str:
    """
    [상관지수] 표의 실제 계산값을 인용해서 동적으로 관찰 문장을 생성.
    - 동시(0) 대비 절댓값이 0.1 이상 더 강한 lag가 있을 때만 "선행성 시사"로 서술.
    - 그 외에는 보수적으로 "뚜렷한 시차 패턴 없음, 동시적 관계"로 서술.
    """
    valid = {r["lag"]: r["corr"] for r in lag_results if r["corr"] is not None}
    if 0 not in valid:
        return "동시(0개월) 기준 상관계수를 계산할 표본이 부족해 시차 패턴을 판단할 수 없습니다."

    base = valid[0]
    others = {lag: c for lag, c in valid.items() if lag != 0}
    if not others:
        return f"동시(0개월) 기준 상관계수는 {base:+.2f}이며, 비교할 시차 데이터가 부족합니다."

    best_lag, best_corr = max(others.items(), key=lambda kv: abs(kv[1]))
    diff = abs(best_corr) - abs(base)

    lag_label = {3: "3개월", 6: "6개월", 12: "12개월"}.get(best_lag, f"{best_lag}개월")

    if diff >= 0.1:
        direction = f"'{name_a}'가 '{name_b}'보다 약 {lag_label} 선행"
        return (
            f"동시(0개월) 상관계수는 {base:+.2f}인 반면, {lag_label} 시차에서 {best_corr:+.2f}로 "
            f"더 강하게 나타나, {direction}하는 패턴이 시사됩니다. "
            f"다만 이는 이번 분석기간의 관찰 결과이며, 통계적으로 확정된 인과관계는 아닙니다."
        )
    else:
        return (
            f"동시(0개월) 상관계수({base:+.2f})와 시차 상관계수(최대 {best_corr:+.2f}, {lag_label}) 간 "
            f"차이가 크지 않아, 뚜렷한 선행·후행 패턴 없이 대체로 동시적(coincident)인 관계로 해석됩니다."
        )


def _interpret_strength(corr: float) -> str:
    a = abs(corr)
    if a >= 0.7:
        return "강한 상관"
    elif a >= 0.4:
        return "중간 정도 상관"
    elif a >= 0.2:
        return "약한 상관"
    return "거의 상관 없음"


GRANGER_LAGS = [1, 3, 6, 12]
GRANGER_MIN_OBS = 50  # 표본당 최소 권장 관측치 (일반적 가이드라인)


def _prepare_stationary_series(s: pd.Series) -> pd.Series:
    """
    월간으로 리샘플링 후 1차 차분해 정상성(stationarity)을 확보.
    - 양수만 있는 지표는 로그차분(=근사 변화율), 그 외에는 단순차분.
    - 그레인저 검정은 정상 시계열을 가정하므로, 추세가 있는 원본 레벨값을
      그대로 넣으면 허위 유의성(spurious significance)이 나올 위험이 큼.
    """
    s_m = s.dropna().resample("ME").last()
    if len(s_m) == 0:
        return s_m
    if (s_m > 0).all():
        return np.log(s_m).diff().dropna()
    return s_m.diff().dropna()


def _compute_granger_causality(s_a: pd.Series, s_b: pd.Series, lags=GRANGER_LAGS, min_obs=GRANGER_MIN_OBS):
    """
    양방향 그레인저 인과검정: A→B, B→A를 lag별로 계산.
    - 두 시계열을 월간 차분(정상화)한 뒤 공통 구간으로 정렬.
    - lag별 p-value만 반환 (p<0.05 여부로 판단, "정확한 시차 특정"은 지표 자기상관 때문에
      어려우므로 여러 lag를 병렬 표시하는 방식을 취함).
    - 표본 대비 lag가 과도하면(자유도 부족 우려, 경험적으로 lag*5 > n) 해당 lag는 계산하지 않고 None 처리.
    반환: {
        "n": 정상화 후 공통 표본 수,
        "insufficient": 최소 권장 표본(min_obs) 미달 여부,
        "a_causes_b": {lag: p-value or None, ...},
        "b_causes_a": {lag: p-value or None, ...},
    }
    """
    a_d = _prepare_stationary_series(s_a)
    b_d = _prepare_stationary_series(s_b)
    combined = pd.DataFrame({"a": a_d, "b": b_d}).dropna()
    n = len(combined)

    result = {"n": n, "insufficient": n < min_obs, "a_causes_b": {}, "b_causes_a": {}}

    for lag in lags:
        if n < 3 * lag + 10:  # 자유도 확보를 위한 경험적 최소 표본 규칙
            result["a_causes_b"][lag] = None
            result["b_causes_a"][lag] = None
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # grangercausalitytests는 [종속변수, 설명변수] 순서 컬럼을 받음
                gc_a_to_b = grangercausalitytests(combined[["b", "a"]], maxlag=lag, verbose=False)
                gc_b_to_a = grangercausalitytests(combined[["a", "b"]], maxlag=lag, verbose=False)
            result["a_causes_b"][lag] = gc_a_to_b[lag][0]["ssr_ftest"][1]
            result["b_causes_a"][lag] = gc_b_to_a[lag][0]["ssr_ftest"][1]
        except Exception:
            result["a_causes_b"][lag] = None
            result["b_causes_a"][lag] = None

    return result


ROLLING_WINDOW_DEFAULT = 60   # 5년 창 (정상화 후 표본 기준)
ROLLING_STEP_DEFAULT = 3      # 3개월씩 창을 이동
ROLLING_LAG_DEFAULT = 3       # 롤링 분석에 사용할 고정 lag (단기 반응을 대표하는 값)


def _compute_rolling_granger(
    s_a: pd.Series, s_b: pd.Series,
    lag: int = ROLLING_LAG_DEFAULT, window: int = ROLLING_WINDOW_DEFAULT, step: int = ROLLING_STEP_DEFAULT,
):
    """
    시간에 따라 그레인저 인과관계 자체가 변하는지(=국면에 따른 관계 변화, "추세의 인과관계")를 보기 위한
    롤링윈도우 그레인저 검정.
    - 전체 기간을 하나로 뭉쳐서 보는 기존 [그레인저 인과검정]과 달리, window개월짜리 구간을 step개월씩
      이동시키며 각 구간마다 별도로 양방향 검정을 반복 수행.
    - lag는 window 대비 안정적인 결과를 위해 하나로 고정. 창마다 lag를 바꾸면 결과 간
      비교가 어려워지므로, "관계의 유무·방향이 시간에 따라 어떻게 변하는가"에 집중하는 설계.
    - 표본(정상화 후 전체 공통구간)이 window보다 짧으면 계산 불가(빈 windows) 반환.
    반환: {"n": 전체 공통표본 수, "windows": [{"end": 창 끝 시점, "a_causes_b": p or None, "b_causes_a": p or None}, ...]}
    """
    a_d = _prepare_stationary_series(s_a)
    b_d = _prepare_stationary_series(s_b)
    combined = pd.DataFrame({"a": a_d, "b": b_d}).dropna()
    n = len(combined)

    if n < window:
        return {"n": n, "windows": []}

    windows = []
    for start in range(0, n - window + 1, step):
        chunk = combined.iloc[start:start + window]
        end_date = chunk.index[-1]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gc_a_to_b = grangercausalitytests(chunk[["b", "a"]], maxlag=lag, verbose=False)
                gc_b_to_a = grangercausalitytests(chunk[["a", "b"]], maxlag=lag, verbose=False)
            p_ab = gc_a_to_b[lag][0]["ssr_ftest"][1]
            p_ba = gc_b_to_a[lag][0]["ssr_ftest"][1]
        except Exception:
            p_ab, p_ba = None, None
        windows.append({"end": end_date, "a_causes_b": p_ab, "b_causes_a": p_ba})

    return {"n": n, "windows": windows}


def _rolling_granger_figure(
    name_a: str, name_b: str, rolling: dict, window: int, step: int, lag: int, show_both: bool = True,
) -> go.Figure:
    """
    롤링윈도우 그레인저 p-value를 시계열 라인차트로 시각화 (0.05 기준선 포함).
    show_both=False면 name_a→name_b(즉 인자로 넘어온 첫 방향, 보통 '지표→주가')만 표시.
    """
    ends = [w["end"] for w in rolling["windows"]]
    p_ab = [w["a_causes_b"] for w in rolling["windows"]]
    p_ba = [w["b_causes_a"] for w in rolling["windows"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ends, y=p_ab, mode="lines+markers", name=f"{name_a} → {name_b}"))
    if show_both:
        fig.add_trace(go.Scatter(x=ends, y=p_ba, mode="lines+markers", name=f"{name_b} → {name_a}"))
    fig.add_hline(y=0.05, line_dash="dash", line_color="red", annotation_text="유의수준 0.05")
    y_values = p_ab + (p_ba if show_both else [])
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="p-value",
        xaxis_title=f"창 크기 {window}개월 / {step}개월씩 이동 / lag={lag}개월 (각 점 = 그 시점까지의 {window}개월 창)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_yaxes(range=[0, max(1.0, max([p for p in y_values if p is not None], default=1.0))])
    return fig


def main():
    # st.caption()은 기본적으로 옅은 회색으로 렌더링되어 설명 문구가 잘 안 보이는 문제가 있어,
    # 전역 CSS로 진한 색(거의 검정)으로 오버라이드. 여러 스트림릿 버전에서 caption이 렌더링되는
    # DOM 구조(testid, small 태그 등)가 조금씩 달라질 수 있어 선택자를 여러 개 겹쳐서 안정성 확보.
    st.markdown(
        """
        <style>
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] p,
        [data-testid="stCaptionContainer"] span,
        div[data-testid="stMarkdownContainer"] small,
        small {
            color: #1a1a1a !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.header("📊 거시경제 지표 비교")

    # ── 기간 선택 ──────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "시작일",
            value=pd.to_datetime("2015-01-01"),
            min_value=pd.to_datetime("1871-01-01"),  # CAPE(실러) 데이터가 1871년부터 있어 최대한 넓게 허용
            max_value=pd.to_datetime("today"),
        )
    with col2:
        end_date = st.date_input(
            "종료일",
            value=pd.to_datetime("today"),
            min_value=pd.to_datetime("1871-01-01"),
            max_value=pd.to_datetime("today"),
        )

    # ── 지표 선택 (카테고리별 격자 + 체크박스, multpl.com 스타일) ──
    grouped = list_indicator_names()

    # 화면 표시 순서: 지수·주가를 맨 앞으로
    CATEGORY_ORDER = ["지수·주가", "미국 거시지표", "금리·통화", "신용·부채", "외환·무역", "원자재", "밸류에이션(복합지표)"]
    ordered_categories = [c for c in CATEGORY_ORDER if c in grouped] + [c for c in grouped if c not in CATEGORY_ORDER]

    all_names = [name for cat in ordered_categories for name in grouped[cat]]

    # ── 사용자가 화면에서 즉석으로 추가한 지표 (세션 한정) ──
    if "custom_indicators" not in st.session_state:
        st.session_state.custom_indicators = {}  # {표시이름: {"source":..., "code":...}}

    with st.expander("➕ 지표 직접 추가 (FRED 코드 / 야후파이낸스 티커)"):
        st.caption(
            "여기서 추가한 지표는 **이번 접속에서만** 아래 격자의 '직접추가지표' 칸에 나타납니다. "
            "계속 쓰실 지표는 개발자(코드 관리자)에게 data_sources.py에 등록을 요청하세요."
        )
        c1, c2, c3 = st.columns([2, 1, 2])
        with c1:
            custom_display_name = st.text_input("표시할 이름", placeholder="예: 미국 신규주택착공")
        with c2:
            custom_source = st.selectbox("소스", ["fred", "yfinance"])
        with c3:
            custom_code = st.text_input(
                "코드",
                placeholder="FRED 예: HOUST  /  야후 예: ^TNX, AAPL, KRW=X",
            )

        if st.button("추가"):
            if not custom_display_name or not custom_code:
                st.warning("이름과 코드를 모두 입력해주세요.")
            else:
                try:
                    test = fetch_by_source(custom_source, custom_code, start="2020-01-01")
                    if test.empty:
                        st.error("데이터를 가져오지 못했습니다. 코드를 확인해주세요.")
                    else:
                        st.session_state.custom_indicators[custom_display_name] = {
                            "source": custom_source, "code": custom_code,
                        }
                        st.success(f"'{custom_display_name}' 추가 완료! 아래 격자의 '직접추가지표' 칸에서 고르실 수 있습니다.")
                except Exception as e:
                    st.error(f"조회 실패: {e}")

        if st.session_state.custom_indicators:
            st.caption("현재 세션에 추가된 지표: " + ", ".join(st.session_state.custom_indicators.keys()))
            if st.button("추가 지표 전체 삭제"):
                st.session_state.custom_indicators = {}
                st.rerun()

    custom_names = list(st.session_state.custom_indicators.keys())
    all_names_with_custom = all_names + custom_names

    # 격자에 표시할 (카테고리명, 지표목록) 순서 — 직접추가지표는 있을 때만 맨 끝에 추가
    grid_sections = [(cat, grouped[cat]) for cat in ordered_categories]
    if custom_names:
        grid_sections.append(("직접추가지표", custom_names))

    MAX_SELECT = 4

    # 체크박스 위젯 상태 초기화(최초 1회, 기본값 나스닥100+하이일드스프레드)
    DEFAULT_SELECTED = {"나스닥100", "하이일드 스프레드(HY OAS, CDS프록시)"}
    for name in all_names_with_custom:
        key = f"chk_{name}"
        if key not in st.session_state:
            st.session_state[key] = name in DEFAULT_SELECTED

    # 현재 선택된 개수(위젯 렌더링 전 session_state 기준으로 미리 파악)
    current_count = sum(1 for name in all_names_with_custom if st.session_state.get(f"chk_{name}", False))

    st.markdown("**비교할 지표를 선택하세요 (최대 4개, 이미 선택된 것은 다시 눌러 해제)**")
    grid_cols = st.columns(len(grid_sections))
    for col, (cat, names) in zip(grid_cols, grid_sections):
        with col:
            st.markdown(f"**{cat}**")
            for name in names:
                key = f"chk_{name}"
                is_checked = st.session_state.get(key, False)
                disabled = (not is_checked) and (current_count >= MAX_SELECT)
                st.checkbox(name, key=key, disabled=disabled)

    # 최종 선택 목록 (전체 지표 순서를 그대로 유지 — 그래프 범례 색상 안정성용)
    selected = [name for name in all_names_with_custom if st.session_state.get(f"chk_{name}", False)]

    if len(selected) > MAX_SELECT:
        st.warning(f"최대 {MAX_SELECT}개까지만 반영됩니다. 앞의 {MAX_SELECT}개만 사용합니다.")
        selected = selected[:MAX_SELECT]
    elif len(selected) < 2:
        st.info("비교하려면 2개 이상 선택해주세요.")

    # ── 정규화 / 로그스케일 옵션 ──────────────────────
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        normalize_opt = st.checkbox(
            "시작점 기준 정규화 (단위가 다른 지표를 100 기준으로 비교)",
            value=False,
            help="예: 지수(2500) vs 금리(4.5%)처럼 스케일이 다른 지표를 같은 그래프에서 형태 비교할 때 사용",
        )
    with col_opt2:
        log_opt = st.checkbox(
            "로그 스케일 (Y축)",
            value=False,
            help="기간이 길어 초반 변화가 잘 안 보일 때 사용. 값이 0 이하인 지표(예: 금리차)는 자동으로 로그 적용에서 제외됩니다.",
        )

    if len(selected) < 1:
        st.info("지표를 최소 1개 이상 선택해주세요.")
        return

    # ── 데이터 로딩 ──────────────────────────────────
    series_dict = {}
    with st.spinner("데이터 불러오는 중..."):
        for name in selected:
            try:
                if name in st.session_state.custom_indicators:
                    meta = st.session_state.custom_indicators[name]
                    s = fetch_by_source(meta["source"], meta["code"], start=str(start_date))
                else:
                    s = get_series(name, start=str(start_date))
            except Exception as e:
                st.warning(f"'{name}' 데이터 로딩 실패: {e}")
                continue

            if s.empty:
                st.warning(f"'{name}' 데이터를 가져오지 못했습니다.")
                continue

            # 날짜 인덱스가 아닌 경우(로딩 실패 등으로 빈 Series 등) 안전하게 건너뜀
            if not pd.api.types.is_datetime64_any_dtype(s.index):
                st.warning(f"'{name}' 데이터 형식이 올바르지 않아 건너뜁니다.")
                continue

            s = s[s.index <= pd.to_datetime(end_date)]
            if s.empty:
                st.warning(f"'{name}' 선택한 기간에 해당하는 데이터가 없습니다.")
                continue

            series_dict[name] = s

    if not series_dict:
        st.error("선택한 지표의 데이터를 하나도 불러오지 못했습니다.")
        return

    # ── 그래프 ────────────────────────────────────────
    # 색상 팔레트: 라인과 해당 Y축 색상을 동일하게 맞춰 구분 용이하게 함
    COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]  # 파랑/빨강/초록/보라
    names = list(series_dict.keys())
    n = len(names)

    fig = go.Figure()

    # 플롯 영역 여백: 왼쪽은 보조축 유무에 따라, 오른쪽은 범례 폭만큼만 확보
    left_domain = 0.11 if n >= 3 else 0.06     # 왼쪽 보조축 있으면 여백 더 필요
    right_domain = 0.80 if n >= 3 else 0.86    # 오른쪽 보조축 있으면 여백 더 필요

    if normalize_opt:
        # 정규화 모드: 모든 지표를 동일 스케일(시작점=100)로 단일 축 비교
        for i, name in enumerate(names):
            color = COLORS[i % len(COLORS)]
            fig.add_trace(go.Scatter(
                x=series_dict[name].index, y=normalize(series_dict[name]),
                mode="lines", name=name, line=dict(color=color, width=1.3),
            ))
        yaxis_conf = dict(
            title=dict(text="정규화 지수 (시작점=100)", font=dict(size=15)),
            tickfont=dict(size=13),
        )
        if log_opt:
            yaxis_conf["type"] = "log"  # 정규화값은 항상 양수라 로그 적용 안전
        fig.update_layout(
            yaxis=yaxis_conf,
            xaxis=dict(domain=[left_domain, right_domain]),
        )

    else:
        # 원본 단위 모드: 좌측 2개 + 우측 2개, 총 4개 Y축까지 지원
        # 축 배치: [0]=왼쪽(메인), [1]=오른쪽(메인), [2]=왼쪽(보조, free), [3]=오른쪽(보조, free)
        axis_slots = [
            dict(key="yaxis",  side="left",  anchor="x",    position=None),
            dict(key="yaxis2", side="right", anchor="x",    position=None),
            dict(key="yaxis3", side="left",  anchor="free", position=left_domain - 0.07),
            dict(key="yaxis4", side="right", anchor="free", position=right_domain + 0.07),
        ]
        trace_yref = ["y", "y2", "y3", "y4"]

        layout_axes = {}
        log_skipped = []
        for i, name in enumerate(names):
            slot = axis_slots[i] if i < 4 else axis_slots[3]  # 5개 이상이면 마지막 축 공유
            color = COLORS[i % len(COLORS)]
            yref = trace_yref[i] if i < 4 else trace_yref[3]

            fig.add_trace(go.Scatter(
                x=series_dict[name].index, y=series_dict[name],
                mode="lines", name=name, line=dict(color=color, width=1.3), yaxis=yref,
            ))

            axis_conf = dict(
                title=dict(text=name, font=dict(color=color, size=15)),
                tickfont=dict(color=color, size=13),
                side=slot["side"],
                showgrid=(i == 0),  # 격자선은 첫 축 기준으로만 표시(그래프 혼잡 방지)
            )
            # 로그스케일: 0 이하 값이 있으면(예: 금리차) 적용 불가하므로 해당 축만 제외
            if log_opt:
                if (series_dict[name] > 0).all():
                    axis_conf["type"] = "log"
                else:
                    log_skipped.append(name)

            if slot["anchor"] == "free":
                axis_conf.update(anchor="free", overlaying="y", position=slot["position"])
            elif slot["key"] != "yaxis":
                axis_conf.update(overlaying="y")

            layout_axes[slot["key"]] = axis_conf

        fig.update_layout(
            xaxis=dict(domain=[left_domain, right_domain]),
            **layout_axes,
        )

        if log_skipped:
            st.caption(f"⚠ 0 이하 값이 포함되어 로그스케일 미적용: {', '.join(log_skipped)}")

    # 범례: 그래프 오른쪽 바로 옆(불필요한 여백 없이)에 세로로 배치
    legend_x = right_domain + (0.14 if n >= 3 else 0.06)
    fig.update_layout(
        height=560,
        showlegend=True,
        legend=dict(
            orientation="v",
            x=legend_x, xanchor="left",
            y=1, yanchor="top",
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=60, r=140, t=40, b=40),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)

    # ── 추세이격률 회귀 통계 (선택된 지표 중 '추세이격률' 계열이 있으면 표시) ──
    deviation_names = [n for n in names if "추세이격률" in n]
    if deviation_names:
        st.markdown("#### 🔄 추세선 회귀 통계 (참고용)")
        st.caption(
            "※ 이격률이 다시 0%(추세선)로 돌아온 과거 사례들을 찾아, 그때 걸린 기간을 집계한 것입니다. "
            "정확한 타이밍 예측이 아니라, 장기투자자가 '지금이 역사적으로 흔치 않은 이격 수준인지' "
            "참고하는 용도로만 활용하시기 바랍니다."
        )
        for name in deviation_names:
            stats = summarize_reversion_stats(series_dict[name])
            if stats is None:
                st.info(f"'{name}': 현재와 비슷한 크기의 과거 이격 사례를 찾지 못했습니다(표본 부족 또는 역사상 이례적인 수준).")
                continue
            direction = "위(고평가 쪽)" if stats["current_deviation"] > 0 else "아래(저평가 쪽)"
            st.markdown(
                f"**{name}**: 현재 이격률 **{stats['current_deviation']:+.1f}%**(추세선 {direction}). "
                f"과거 비슷한 크기의 이격 사례 **{stats['n_episodes']}건** 중, "
                f"추세선까지 되돌아오는 데 평균 **{stats['mean_months']:.0f}개월**(중앙값 {stats['median_months']:.0f}개월, "
                f"최소 {stats['min_months']:.0f}~최대 {stats['max_months']:.0f}개월) 걸렸습니다."
            )
        st.divider()

    # ── 지수간 상관관계 (숫자 분석 → 서사 순) ──────────
    st.subheader("📈 지수간 상관관계")

    if n < 2:
        st.caption("지표를 2개 이상 선택하면 상관관계 분석이 표시됩니다.")
    else:
        for name_a, name_b in itertools.combinations(names, 2):
            st.markdown(f"#### {name_a}  ×  {name_b}")

            result = _compute_pair_correlation(series_dict[name_a], series_dict[name_b])
            if result is None:
                st.info("표본이 부족하여 상관계수를 계산할 수 없습니다 (월간 환산 기준 최소 3개 이상 데이터 필요).")
                st.divider()
                continue

            # 1) [분석기간]
            strength = _interpret_strength(result["corr"])
            st.markdown(
                f"**[분석기간]** {result['start'].strftime('%Y-%m')} ~ {result['end'].strftime('%Y-%m')} "
                f"(월간 환산 표본 {result['n']}개)"
            )
            st.caption(
                "※ 월말 기준으로 리샘플링한 값으로 계산됩니다. 상관계수는 두 지표가 같은 기간 동안 "
                "함께 움직인 정도만을 나타내며, 인과관계를 의미하지 않습니다."
            )

            # 2) [상관지수] 4칸 표 (동시/3/6/12개월) — 소제목보다 작은 폰트, 좁은 폭
            #    lag>0의 의미: '거시지표(원인) → 주가(결과)' 방향으로 해석되도록,
            #    두 지표 중 하나가 주가지수라면 그 지표를 항상 '결과(후행)' 자리에 고정한다.
            #    (name_a/name_b는 화면상 나열 순서를 따를 뿐이라, 그대로 쓰면 방향이 뒤집힐 수 있음)
            PRICE_INDICES = {"S&P500", "나스닥100", "다우존스", "코스피", "코스닥", "니케이225(일본)"}
            a_is_price = name_a in PRICE_INDICES
            b_is_price = name_b in PRICE_INDICES
            if a_is_price and not b_is_price:
                lead_name, lag_name = name_b, name_a  # 주가가 아닌 쪽(name_b)을 선행 가정
            else:
                lead_name, lag_name = name_a, name_b  # 판단 불가(둘 다 주가이거나 둘 다 아님) → 원래 순서 유지

            lag_results = _compute_lag_correlations(series_dict[lead_name], series_dict[lag_name])
            st.markdown("**[상관지수]**")

            cell_style = "padding:3px 14px; text-align:center; border-bottom:1px solid #e6e6e6;"
            header_style = cell_style + "font-size:0.75rem; color:#1a1a1a;"
            value_style = cell_style + "font-size:0.82rem; font-weight:600;"

            header_cells = ""
            value_cells = ""
            for r in lag_results:
                label = "동시(0)" if r["lag"] == 0 else f"{r['lag']}개월"
                value_text = "표본부족" if r["corr"] is None else f"{r['corr']:+.2f}"
                header_cells += f"<td style='{header_style}'>{label}</td>"
                value_cells += f"<td style='{value_style}'>{value_text}</td>"

            table_html = (
                f"<table style='border-collapse:collapse; width:auto;'>"
                f"<tr>{header_cells}</tr><tr>{value_cells}</tr></table>"
            )
            st.markdown(table_html, unsafe_allow_html=True)
            st.caption(f"※ lag>0은 '{lead_name}'가 '{lag_name}'를 그만큼 선행한다고 가정했을 때의 상관계수입니다.")

            # 2-1) [그레인저 인과검정] — 상관지수 표와 동일한 방향 판단(lead_name/lag_name) 재사용.
            #      한쪽만 주가지수인 경우, '주가 → 지표' 방향은 실무적으로 참고 가치가 낮아(예: 주가를 보고
            #      원유를 매매하는 경우는 드묾) 생략하고 '지표 → 주가' 방향만 표시. 둘 다 주가이거나 둘 다
            #      주가가 아니면(방향 판단 불가) 기존처럼 양방향 모두 표시.
            direction_determinable = a_is_price != b_is_price
            granger = _compute_granger_causality(series_dict[lead_name], series_dict[lag_name])
            st.markdown("**[그레인저 인과검정]**")

            if granger["n"] < 10:
                st.info("정상화(월간 차분) 후 공통 표본이 너무 적어 그레인저 인과검정을 수행할 수 없습니다.")
            else:
                g_header = "".join(f"<td style='{header_style}'>{lag}개월</td>" for lag in GRANGER_LAGS)

                def _pval_row(pvals: dict) -> str:
                    cells = ""
                    for lag in GRANGER_LAGS:
                        p = pvals.get(lag)
                        if p is None:
                            cells += f"<td style='{value_style}'>표본부족</td>"
                        else:
                            sig = " color:#c0392b;" if p < 0.05 else ""
                            mark = " *" if p < 0.05 else ""
                            cells += f"<td style='{value_style}{sig}'>{p:.3f}{mark}</td>"
                    return cells

                row_label_style = cell_style + "font-size:0.78rem; text-align:left; white-space:nowrap; padding-right:10px;"
                rows_html = f"<tr><td style='{row_label_style}'>{lead_name} → {lag_name}</td>{_pval_row(granger['a_causes_b'])}</tr>"
                if not direction_determinable:
                    rows_html += f"<tr><td style='{row_label_style}'>{lag_name} → {lead_name}</td>{_pval_row(granger['b_causes_a'])}</tr>"

                g_table_html = (
                    f"<table style='border-collapse:collapse; width:auto;'>"
                    f"<tr><td style='{row_label_style}'></td>{g_header}</tr>"
                    f"{rows_html}"
                    f"</table>"
                )
                st.markdown(g_table_html, unsafe_allow_html=True)
                st.caption(
                    f"※ 표시값은 p-value (월간 차분 기준 정상화 후 표본 {granger['n']}개). "
                    "*p<0.05는 유의수준 5%에서 그레인저 인과 관계(=예측력 기여)가 있다는 뜻이며, "
                    "실제 인과관계를 증명하는 것은 아닙니다."
                )
                if direction_determinable:
                    st.caption(
                        f"※ '{lead_name} → {lag_name}' 방향만 표시합니다. 반대 방향(주가 → 지표)은 "
                        "실무적으로 참고할 매매 맥락이 낮아(예: 주가를 보고 원유·통화량을 거래하는 경우는 "
                        "드묾) 생략했습니다."
                    )
                if granger["insufficient"]:
                    st.caption(
                        f"⚠ 표본 {granger['n']}개는 일반적으로 권장되는 최소치({GRANGER_MIN_OBS}개) 미만이라, "
                        "특히 긴 lag(6·12개월)의 결과는 참고용으로만 활용하시기 바랍니다."
                    )
                st.caption(
                    "⚠ 지표 자체의 자기상관(autocorrelation) 때문에 '정확한 시차'를 하나로 특정하기는 어렵습니다. "
                    "여러 lag에서 p<0.05가 반복적으로 나타나는지를 함께 보는 것을 권장합니다."
                )

                # 2-2) [추세의 인과관계] 롤링윈도우 — 관계 자체가 국면에 따라 변하는지 시계열로 확인
                with st.expander("📉 추세의 인과관계 보기 (롤링윈도우)"):
                    st.caption(
                        "※ 전체 기간을 하나로 뭉쳐 계산한 위 표와 달리, 일정 구간(창)을 이동시키며 반복 검정해 "
                        "'관계 자체가 시간에 따라 강해지거나 약해지거나 사라지는지'를 봅니다. "
                        "빨간 점선(0.05) 아래로 내려간 구간이 유의했던 구간입니다. "
                        "아래 슬라이더로 창 크기·이동폭·lag를 바꿔가며 안정적인 신호가 나오는 조합을 찾아보세요."
                    )
                    rc1, rc2, rc3 = st.columns(3)
                    with rc1:
                        roll_window = st.slider(
                            "창 크기(개월)", min_value=24, max_value=96,
                            value=ROLLING_WINDOW_DEFAULT, step=6,
                            key=f"roll_window_{lead_name}_{lag_name}",
                            help="창이 클수록 결과는 안정적이지만 국면 변화에 둔감해지고, 작을수록 민감하지만 표본부족으로 불안정해질 수 있습니다.",
                        )
                    with rc2:
                        roll_step = st.slider(
                            "이동폭(개월)", min_value=1, max_value=12,
                            value=ROLLING_STEP_DEFAULT, step=1,
                            key=f"roll_step_{lead_name}_{lag_name}",
                            help="작을수록 그래프가 촘촘해지지만 인접 창끼리 겹치는 데이터가 많아져 값이 부드럽게(smoothed) 보일 수 있습니다.",
                        )
                    with rc3:
                        roll_lag = st.slider(
                            "lag(개월)", min_value=1, max_value=12,
                            value=ROLLING_LAG_DEFAULT, step=1,
                            key=f"roll_lag_{lead_name}_{lag_name}",
                            help="창 안에서 검정할 시차. 창 크기 대비 너무 크면(경험적으로 창의 1/5 초과) 자유도 부족 우려가 있습니다.",
                        )

                    if roll_lag * 5 > roll_window:
                        st.warning(
                            f"⚠ 창 크기({roll_window}개월) 대비 lag({roll_lag}개월)이 큰 편이라 "
                            "자유도 부족으로 결과가 불안정할 수 있습니다. lag를 줄이거나 창을 늘려보세요."
                        )

                    rolling = _compute_rolling_granger(
                        series_dict[lead_name], series_dict[lag_name],
                        lag=roll_lag, window=roll_window, step=roll_step,
                    )
                    if not rolling["windows"]:
                        st.info(
                            f"정상화 후 표본 {rolling['n']}개가 롤링 창({roll_window}개월)보다 짧아 "
                            "롤링윈도우 분석을 수행할 수 없습니다. 창 크기를 줄이거나 더 긴 기간의 데이터가 필요합니다."
                        )
                    else:
                        fig_roll = _rolling_granger_figure(
                            lead_name, lag_name, rolling, roll_window, roll_step, roll_lag,
                            show_both=not direction_determinable,
                        )
                        st.plotly_chart(fig_roll, use_container_width=True, key=f"roll_chart_{lead_name}_{lag_name}")

                        # 현재 조합에서 유의 구간 비율을 요약 — "안정적인 신호"인지 가늠하는 참고 지표
                        valid_ab = [w["a_causes_b"] for w in rolling["windows"] if w["a_causes_b"] is not None]
                        if valid_ab:
                            sig_ratio_ab = sum(p < 0.05 for p in valid_ab) / len(valid_ab) * 100
                            summary = f"※ 전체 {len(rolling['windows'])}개 창 중 유의(p<0.05) 비율: {lead_name}→{lag_name} {sig_ratio_ab:.0f}%"
                            if not direction_determinable:
                                valid_ba = [w["b_causes_a"] for w in rolling["windows"] if w["b_causes_a"] is not None]
                                if valid_ba:
                                    sig_ratio_ba = sum(p < 0.05 for p in valid_ba) / len(valid_ba) * 100
                                    summary += f" / {lag_name}→{lead_name} {sig_ratio_ba:.0f}%"
                            summary += (
                                ". 이 비율이 극단적으로 높거나(항상 유의) 낮으면(항상 비유의) 국면과 무관한 안정적 "
                                "관계(또는 무관계)로, 중간 어딘가에서 오르내리면 국면에 따라 달라지는 관계로 해석할 수 있습니다."
                            )
                            st.caption(summary)
                        st.caption(
                            "⚠ 이동폭을 창 크기보다 훨씬 작게 설정하면 인접한 창끼리 데이터가 상당 부분 겹칩니다. "
                            "따라서 인접 시점의 p-value가 함께 움직이는 것은 자연스러운 현상이며, 실제로 봐야 할 것은 "
                            "'전체 구간 중 어느 국면에서 유의/비유의가 몰려 있는가'라는 큰 흐름입니다."
                        )

            # 3) [해설] — 동적 관찰(상관지수 표 인용) + 고정 서사(있으면)
            st.markdown("**[해설]**")
            st.markdown("📊 *이번 분석기간 관찰*")
            st.write(_dynamic_lag_observation(lead_name, lag_name, lag_results))

            narrative = get_narrative(name_a, name_b)
            if narrative:
                st.markdown("📚 *학계·전문가 참고 시각*")
                expected = narrative["expected"]
                if expected in ("+", "-"):
                    match = (expected == "+" and result["corr"] >= 0) or (expected == "-" and result["corr"] < 0)
                    match_txt = "이론과 방향 일치" if match else "⚠ 이론과 방향 불일치 (관계가 약화·역전됐을 가능성)"
                    expected_txt = "양(+)의 상관" if expected == "+" else "음(−)의 상관"
                    st.caption(f"이론적으로는 {expected_txt} 관계로 알려져 있습니다 ({match_txt}).")
                elif expected == "contrarian":
                    st.caption("이 지표는 선형 상관보다 **역발상(contrarian) 신호**로 해석되는 지표입니다. "
                                "상관계수 부호보다 극단값(과열/과냉각) 여부를 참고하시는 것이 더 적절합니다.")
                else:  # "context"
                    st.caption("국면에 따라 관계의 방향이 달라질 수 있는 지표 조합입니다.")
                st.write(narrative["text"])
            # 고정 서사가 없으면 이 항목 자체를 생략(근거 없는 즉석 서술 방지)

            st.divider()

    # ── 원본 데이터 테이블 (선택적 확인용) ──
    with st.expander("원본 데이터 보기"):
        st.caption(
            "※ 지표마다 발표 주기가 다릅니다(예: 일간 지수 vs 월간 마진부채). "
            "일간 기준으로 보면 월간 지표는 발표일에만 값이 채워지고 나머지는 빈 값으로 보이는 게 정상입니다."
        )
        combined = pd.DataFrame(series_dict).sort_index()

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            table_freq = st.selectbox(
                "표시 주기",
                ["일간(원본)", "주간", "월간"],
                index=2,  # 월간 지표가 섞여 있을 때 보기 편하도록 기본값을 월간으로
            )
        with col_t2:
            ffill_opt = st.checkbox("빈 값을 직전 값으로 채우기(ffill)", value=True)

        if table_freq == "주간":
            combined_display = combined.resample("W").last()
        elif table_freq == "월간":
            combined_display = combined.resample("ME").last()
        else:
            combined_display = combined

        if ffill_opt:
            combined_display = combined_display.ffill()

        n_rows = st.slider("표시 행 수(최근 N개)", 10, 200, 50)
        st.dataframe(combined_display.tail(n_rows))
        st.download_button(
            "CSV 다운로드 (표시된 주기 기준)",
            combined_display.to_csv().encode("utf-8-sig"),
            file_name="macro_indicators.csv",
        )

    # NOTE: 2단계(상관관계 분석/설명)는 여기에 추후 추가 예정


if __name__ == "__main__":
    main()
