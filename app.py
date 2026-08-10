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

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data_sources import list_indicator_names, get_series, normalize

st.set_page_config(page_title="거시경제 지표 대시보드", layout="wide")


def main():
    st.header("📊 거시경제 지표 비교")

    # ── 기간 선택 ──────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("시작일", pd.to_datetime("2015-01-01"))
    with col2:
        end_date = st.date_input("종료일", pd.to_datetime("today"))

    # ── 지표 선택 (카테고리별 그룹핑된 멀티셀렉트) ──
    grouped = list_indicator_names()
    all_names = [name for names in grouped.values() for name in names]

    st.caption("카테고리: " + " · ".join(grouped.keys()) + "  (※ 좌우 Y축 각 2개씩, 최대 4개 지표 권장)")
    selected = st.multiselect(
        "비교할 지표를 2개 이상 선택하세요 (최대 4개 권장)",
        options=all_names,
        default=["나스닥100", "하이일드 스프레드(HY OAS, CDS프록시)"],
        max_selections=4,
    )

    # ── 정규화 옵션 (단위가 다른 지표를 같은 스케일로 비교) ──
    normalize_opt = st.checkbox(
        "시작점 기준 정규화 (단위가 다른 지표를 100 기준으로 비교)",
        value=False,
        help="예: 지수(2500) vs 금리(4.5%)처럼 스케일이 다른 지표를 같은 그래프에서 형태 비교할 때 사용",
    )

    if len(selected) < 1:
        st.info("지표를 최소 1개 이상 선택해주세요.")
        return

    # ── 데이터 로딩 ──────────────────────────────────
    series_dict = {}
    with st.spinner("데이터 불러오는 중..."):
        for name in selected:
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
        fig.update_layout(
            yaxis=dict(title="정규화 지수 (시작점=100)"),
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
            if slot["anchor"] == "free":
                axis_conf.update(anchor="free", overlaying="y", position=slot["position"])
            elif slot["key"] != "yaxis":
                axis_conf.update(overlaying="y")

            layout_axes[slot["key"]] = axis_conf

        fig.update_layout(
            xaxis=dict(domain=[left_domain, right_domain]),
            **layout_axes,
        )

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

    # ── 원본 데이터 테이블 (선택적 확인용) ──
    with st.expander("원본 데이터 보기"):
        combined = pd.DataFrame(series_dict)
        st.dataframe(combined.tail(50))
        st.download_button(
            "CSV 다운로드",
            combined.to_csv().encode("utf-8-sig"),
            file_name="macro_indicators.csv",
        )

    # NOTE: 2단계(상관관계 분석/설명)는 여기에 추후 추가 예정


if __name__ == "__main__":
    main()
