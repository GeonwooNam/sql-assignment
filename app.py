"""
Olist 인사이트 셀프체크
YBIGTA SQL 3주차 과제용. 제출 전 스스로 점검하는 도구이며, 이 점수는 성적이 아니다.
"""

import json

import anthropic
import streamlit as st

from rubric import (
    DIMENSIONS,
    GRADE_SCHEMA,
    RECOMMEND_LINE,
    RUBRIC_TEXT,
    SCHEMA_BLOCK,
    SYSTEM_PROMPT,
    TOTAL_MAX,
)

MODEL = "claude-opus-5"
MAX_CALLS_PER_SESSION = 20
LIMITS = {"sql": 4000, "result": 3000, "insight": 3000}

st.set_page_config(page_title="Olist 인사이트 셀프체크", page_icon="🔎", layout="centered")


# ── 상태 ────────────────────────────────────────────────────────
st.session_state.setdefault("unlocked", False)
st.session_state.setdefault("calls", 0)
st.session_state.setdefault("history", [])


# ── 접속 코드 ───────────────────────────────────────────────────
# Streamlit Community Cloud 앱은 URL을 아는 누구나 접속할 수 있다.
# API 키가 발제자 것이므로 최소한의 문지기를 둔다.
def gate() -> bool:
    expected = st.secrets.get("ACCESS_CODE", "")
    if not expected:  # 코드를 설정하지 않았으면 통과
        return True
    if st.session_state.unlocked:
        return True

    st.title("🔎 Olist 인사이트 셀프체크")
    st.caption("YBIGTA SQL 3주차")
    code = st.text_input("접속 코드", type="password", placeholder="학회 공지의 코드를 입력하세요")
    if code:
        if code.strip() == expected:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("코드가 맞지 않습니다.")
    return False


if not gate():
    st.stop()


# ── 사이드바 ────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("이 도구에 대해")
    st.markdown(
        f"""
제출 전 **셀프체크 도구**입니다.

- 여기 점수는 **성적이 아닙니다.**
- 점수보다 아래 **항목별 근거와 피드백**을 보세요. 그게 본체입니다.
- **{RECOMMEND_LINE}점 이상**이면 제출해도 될 수준이라는 대략의 신호입니다.
- 몇 번이든 고쳐서 다시 돌려도 됩니다.
        """
    )
    st.divider()
    st.subheader("배점")
    st.markdown(
        "\n".join(f"- **{label}** {mx}점" for _, label, mx in DIMENSIONS)
        + f"\n- **합계 {TOTAL_MAX}점**"
    )
    with st.expander("항목별 채점 기준 (전문)"):
        st.markdown(RUBRIC_TEXT)
    with st.expander("테이블 스키마"):
        st.code(SCHEMA_BLOCK, language="text")
    st.divider()
    st.caption(f"이번 세션 채점 횟수: {st.session_state.calls} / {MAX_CALLS_PER_SESSION}")


# ── 입력 ────────────────────────────────────────────────────────
st.title("🔎 Olist 인사이트 셀프체크")
st.caption(
    "쿼리 · 실행 결과 · 인사이트를 넣으면 항목별로 몇 단계인지, "
    "한 단계 올리려면 뭘 해야 하는지 알려줍니다."
)

sql = st.text_area(
    "1. 작성한 쿼리",
    height=200,
    placeholder="SELECT c.customer_state, AVG(...) \nFROM olist_raw.orders AS o\nJOIN ...",
)
result = st.text_area(
    "2. 쿼리 실행 결과",
    height=140,
    placeholder="BigQuery 결과 표를 그대로 복사해서 붙여넣으세요. 행이 많으면 상위 10~20행이면 충분합니다.",
)
insight = st.text_area(
    "3. 도출한 인사이트",
    height=200,
    placeholder=(
        "이 수치에서 무엇을 발견했는지, 왜 그렇다고 보는지, "
        "그래서 무엇을 하자는 것인지 써주세요."
    ),
)

go = st.button("채점하기", type="primary", use_container_width=True)


# ── 채점 ────────────────────────────────────────────────────────
def build_user_message(sql: str, result: str, insight: str) -> str:
    return (
        "아래 제출물을 루브릭에 따라 채점해줘.\n\n"
        "<submission>\n"
        f"<query>\n{sql.strip()}\n</query>\n\n"
        f"<query_result>\n{result.strip()}\n</query_result>\n\n"
        f"<insight>\n{insight.strip()}\n</insight>\n"
        "</submission>"
    )


def grade(sql: str, result: str, insight: str) -> dict:
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(sql, result, insight)}],
        output_config={"format": {"type": "json_schema", "schema": GRADE_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("채점기가 이 제출물에 대한 응답을 거절했습니다. 내용을 확인해주세요.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("응답이 잘렸습니다. 제출물을 조금 줄여서 다시 시도해주세요.")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def total_of(data: dict) -> int:
    """총점은 모델이 아니라 여기서 계산한다. 항목 점수는 만점 범위로 자른다."""
    return sum(max(0, min(mx, int(data[key]["score"]))) for key, _, mx in DIMENSIONS)


if go:
    problems = []
    if not sql.strip():
        problems.append("쿼리를 입력해주세요.")
    if not insight.strip():
        problems.append("인사이트를 입력해주세요.")
    if not result.strip():
        problems.append("쿼리 실행 결과를 붙여넣어야 근거 정합성을 볼 수 있습니다.")
    for key, field in (("sql", sql), ("result", result), ("insight", insight)):
        if len(field) > LIMITS[key]:
            problems.append(f"{key} 가 너무 깁니다 ({len(field)}자 / 최대 {LIMITS[key]}자).")
    if st.session_state.calls >= MAX_CALLS_PER_SESSION:
        problems.append("이번 세션 채점 횟수를 모두 썼습니다. 페이지를 새로고침하면 초기화됩니다.")

    if problems:
        for p in problems:
            st.warning(p)
    else:
        try:
            with st.spinner("채점 중입니다. 20~40초 걸립니다..."):
                data = grade(sql, result, insight)
                data["_total"] = total_of(data)
        except anthropic.RateLimitError:
            st.error("요청이 몰렸습니다. 30초 뒤에 다시 시도해주세요.")
        except anthropic.APIStatusError as e:
            st.error(f"API 오류 ({e.status_code}). 잠시 후 다시 시도해주세요.")
        except anthropic.APIConnectionError:
            st.error("네트워크 연결에 실패했습니다.")
        except (RuntimeError, json.JSONDecodeError, StopIteration, KeyError, ValueError) as e:
            st.error(f"채점 결과를 읽지 못했습니다: {e}")
        else:
            st.session_state.calls += 1
            st.session_state.history.append(data["_total"])
            st.session_state.latest = data


# ── 결과 ────────────────────────────────────────────────────────
data = st.session_state.get("latest")
if data:
    st.divider()

    total = data["_total"]
    col_a, col_b = st.columns([1, 3])
    with col_a:
        hist = st.session_state.history
        delta = hist[-1] - hist[-2] if len(hist) > 1 else None
        st.metric(f"합계 / {TOTAL_MAX}", f"{total}", delta=delta)
    with col_b:
        st.progress(total / TOTAL_MAX)
        st.write(f"**{data['one_line']}**")
        if total >= RECOMMEND_LINE:
            st.caption(f"✅ 제출 권장선({RECOMMEND_LINE}점)을 넘었습니다.")
        else:
            st.caption(f"제출 권장선은 {RECOMMEND_LINE}점입니다. 아래에서 약한 항목부터 보세요.")

    st.caption("이 점수는 성적이 아닙니다. 아래 항목별 근거와 피드백이 본체입니다.")

    st.subheader("항목별 점수")
    for key, label, mx in DIMENSIONS:
        d = data[key]
        score = max(0, min(mx, int(d["score"])))
        head, bar = st.columns([2, 3])
        with head:
            st.markdown(f"**{label}**  ·  {score} / {mx}  ·  {d['level']}단계")
        with bar:
            st.progress(score / mx)
        st.caption(d["reason"])
        if d.get("evidence"):
            with st.expander(f"{label} — 판정 근거로 본 부분"):
                st.markdown(f"> {d['evidence']}")
        st.write("")

    st.subheader("쿼리 점검")
    st.info(data["query_check"])

    st.subheader("잘한 점")
    st.success(data["good"])

    st.subheader("보완할 점")
    st.warning(data["feedback"])

    if data.get("next_questions"):
        st.subheader("한 걸음 더")
        for q in data["next_questions"]:
            st.markdown(f"- {q}")

    if len(st.session_state.history) > 1:
        with st.expander("점수 변화"):
            st.line_chart(st.session_state.history)
