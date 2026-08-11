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
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from scipy.stats import pearsonr

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


def _interpret_strength(corr: float) -> str:
    a = abs(corr)
    if a >= 0.7:
        return "강한 상관"
    elif a >= 0.4:
        return "중간 정도 상관"
    elif a >= 0.2:
        return "약한 상관"
    return "거의 상관 없음"


def main():
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

            # 1) 객관적 숫자 분석 (기간 명시)
            strength = _interpret_strength(result["corr"])
            sign = "+" if result["corr"] >= 0 else "−"
            st.markdown(
                f"**[분석기간]** {result['start'].strftime('%Y-%m')} ~ {result['end'].strftime('%Y-%m')} "
                f"(월간 환산 표본 {result['n']}개) · 상관계수: **{sign}{abs(result['corr']):.2f}** ({strength})"
            )
            st.caption(
                "※ 월말 기준으로 리샘플링한 값으로 계산됩니다. 상관계수는 두 지표가 같은 기간 동안 "
                "함께 움직인 정도만을 나타내며, 인과관계를 의미하지 않습니다."
            )

            # 2) 서사 (큐레이션 DB 우선, 없으면 일반 안내)
            narrative = get_narrative(name_a, name_b)
            if narrative:
                expected = narrative["expected"]
                if expected in ("+", "-"):
                    match = (expected == "+" and result["corr"] >= 0) or (expected == "-" and result["corr"] < 0)
                    match_txt = "이론과 방향 일치" if match else "⚠ 이론과 방향 불일치 (관계가 약화·역전됐을 가능성)"
                    expected_txt = "양(+)의 상관" if expected == "+" else "음(−)의 상관"
                    st.markdown(f"**[설명]** 이론적으로는 {expected_txt} 관계로 알려져 있습니다 ({match_txt}).")
                elif expected == "contrarian":
                    st.markdown("**[설명]** 이 지표는 선형 상관보다 **역발상(contrarian) 신호**로 해석되는 지표입니다. "
                                 "상관계수 부호보다 극단값(과열/과냉각) 여부를 참고하시는 것이 더 적절합니다.")
                else:  # "context"
                    st.markdown("**[설명]** 국면에 따라 관계의 방향이 달라질 수 있는 지표 조합입니다.")
                st.write(narrative["text"])
            else:
                st.markdown("**[설명]**")
                st.caption(
                    "이 조합에 대해 업계에서 널리 인정된 정성적 서사는 별도로 정리되어 있지 않습니다. "
                    "위 상관계수만으로 인과관계를 단정하지 말고, 다른 지표와 함께 교차 검증하시기 바랍니다."
                )

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
