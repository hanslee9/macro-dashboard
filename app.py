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

    st.caption("카테고리: " + " · ".join(grouped.keys()))
    selected = st.multiselect(
        "비교할 지표를 2개 이상 선택하세요",
        options=all_names,
        default=["나스닥100", "하이일드 스프레드(HY OAS, CDS프록시)"],
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

    # ── 그래프 (정규화 모드: 단일 Y축 / 원본 모드: 좌우 2개 Y축) ──
    fig = go.Figure()

    if normalize_opt:
        for name, s in series_dict.items():
            fig.add_trace(go.Scatter(x=s.index, y=normalize(s), mode="lines", name=name))
        fig.update_layout(yaxis_title="정규화 지수 (시작점=100)")

    else:
        # 첫 번째 지표는 좌축, 나머지는 우축에 배치 (기존 나스닥100 vs 마진부채 차트 스타일)
        names = list(series_dict.keys())
        first = names[0]
        fig.add_trace(go.Scatter(
            x=series_dict[first].index, y=series_dict[first],
            mode="lines", name=first, yaxis="y1"
        ))
        for name in names[1:]:
            fig.add_trace(go.Scatter(
                x=series_dict[name].index, y=series_dict[name],
                mode="lines", name=name, yaxis="y2"
            ))
        fig.update_layout(
            yaxis=dict(title=first, side="left"),
            yaxis2=dict(title=" / ".join(names[1:]) if len(names) > 1 else "",
                        overlaying="y", side="right"),
        )

    fig.update_layout(
        height=550,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=40, r=40, t=40, b=40),
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
