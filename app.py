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

from data_sources import list_indicator_names, get_series, normalize, fetch_by_source
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
            min_value=pd.to_datetime("1990-01-01"),  # 미지정 시 기본값-10년으로 자동 제한되는 것을 방지
            max_value=pd.to_datetime("today"),
        )
    with col2:
        end_date = st.date_input(
            "종료일",
            value=pd.to_datetime("today"),
            min_value=pd.to_datetime("1990-01-01"),
            max_value=pd.to_datetime("today"),
        )

    # ── 지표 선택 (카테고리별 그룹핑된 멀티셀렉트) ──
    grouped = list_indicator_names()
    all_names = [name for names in grouped.values() for name in names]

    # ── 사용자가 화면에서 즉석으로 추가한 지표 (세션 한정) ──
    if "custom_indicators" not in st.session_state:
        st.session_state.custom_indicators = {}  # {표시이름: {"source":..., "code":...}}

    with st.expander("➕ 지표 직접 추가 (FRED 코드 / 야후파이낸스 티커)"):
        st.caption(
            "여기서 추가한 지표는 **이번 접속에서만** 선택 목록에 나타납니다. "
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
                        st.success(f"'{custom_display_name}' 추가 완료! 아래 선택 목록에서 고르실 수 있습니다.")
                except Exception as e:
                    st.error(f"조회 실패: {e}")

        if st.session_state.custom_indicators:
            st.caption("현재 세션에 추가된 지표: " + ", ".join(st.session_state.custom_indicators.keys()))
            if st.button("추가 지표 전체 삭제"):
                st.session_state.custom_indicators = {}
                st.rerun()

    custom_names = list(st.session_state.custom_indicators.keys())
    all_names_with_custom = all_names + custom_names

    st.caption("카테고리: " + " · ".join(grouped.keys()) + "  (※ 좌우 Y축 각 2개씩, 최대 4개 지표 권장)")
    selected = st.multiselect(
        "비교할 지표를 2개 이상 선택하세요 (최대 4개 권장)",
        options=all_names_with_custom,
        default=["나스닥100", "하이일드 스프레드(HY OAS, CDS프록시)"],
        max_selections=4,
    )

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
            if name in st.session_state.custom_indicators:
                meta = st.session_state.custom_indicators[name]
                try:
                    s = fetch_by_source(meta["source"], meta["code"], start=str(start_date))
                except Exception as e:
                    st.warning(f"'{name}' 데이터 로딩 실패: {e}")
                    continue
            else:
                s = get_series(name, start=str(start_date))
            s = s[s.index <= pd.to_datetime(end_date)]
            if s.empty:
                st.warning(f"'{name}' 데이터를 가져오지 못했습니다.")
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
                mode="lines", name=name, line=dict(color=color),
            ))
        yaxis_conf = dict(title="정규화 지수 (시작점=100)")
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
                mode="lines", name=name, line=dict(color=color), yaxis=yref,
            ))

            axis_conf = dict(
                title=dict(text=name, font=dict(color=color)),
                tickfont=dict(color=color),
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
                f"**[숫자 분석]** 기간: {result['start'].strftime('%Y-%m')} ~ {result['end'].strftime('%Y-%m')} "
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
                    st.markdown(f"**[서사]** 이론적으로는 {expected_txt} 관계로 알려져 있습니다 ({match_txt}).")
                elif expected == "contrarian":
                    st.markdown("**[서사]** 이 지표는 선형 상관보다 **역발상(contrarian) 신호**로 해석되는 지표입니다. "
                                 "상관계수 부호보다 극단값(과열/과냉각) 여부를 참고하시는 것이 더 적절합니다.")
                else:  # "context"
                    st.markdown("**[서사]** 국면에 따라 관계의 방향이 달라질 수 있는 지표 조합입니다.")
                st.write(narrative["text"])
            else:
                st.markdown("**[서사]**")
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
