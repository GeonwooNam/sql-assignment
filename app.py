"""
Olist 인사이트 셀프체크
YBIGTA SQL 3주차 과제용. 제출 전 스스로 점검하는 도구이며, 이 점수는 성적이 아니다.
"""

import csv
import html
import io
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import openai
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

# ── 비용 손잡이 ────────────────────────────────────────────────
# 모델을 바꾸면 PRICES 의 키만 맞으면 비용 표시도 따라간다.
# Terra 를 쓰는 이유: 구형 GPT-5.5 와 값이 절반인데 벤치마크는 위다.
# 구형(gpt-5.5 / 5.4 / 5)은 같은 값에 성능만 낮으므로 쓸 이유가 없다.
MODEL = "gpt-5.6-terra"

# 추론 깊이. none / minimal / low / medium / high / xhigh / max
# 비용의 약 80%가 출력 토큰이고 추론 토큰도 출력으로 과금되므로,
# 예산을 아껴야 하면 모델을 내리기 전에 여기부터 medium 으로 내려보고
# 같은 제출물을 2~3번 돌려 점수가 흔들리는지 확인할 것.
EFFORT = "high"

# $/1M 토큰 — (입력, 캐시된 입력, 출력)
# OpenAI 는 1024토큰 이상 동일 프리픽스를 자동 캐싱한다. 캐시 '쓰기' 추가금은 없다.
PRICES = {
    "gpt-5.6-sol": (5.0, 0.5, 30.0),
    "gpt-5.6-terra": (2.5, 0.25, 15.0),
    "gpt-5.6-luna": (1.0, 0.1, 6.0),
}
PRICE_IN, PRICE_CACHED, PRICE_OUT = PRICES[MODEL]

# 추론 토큰도 이 한도를 함께 쓴다. 한도에 걸려 잘리면 추론 토큰 값은 그대로
# 청구되고 결과는 못 받으므로, 넉넉하게 두는 편이 오히려 싸다.
MAX_OUTPUT_TOKENS = 12000

MAX_CALLS_PER_SESSION = 20

# 제출 로그. Streamlit Cloud 에서는 프로세스가 하나라 여러 학회원의 제출이 이 파일에
# 함께 쌓이지만, **재배포·리부트 때 사라진다.** 영구 보존이 필요하면 secrets 에
# LOG_WEBHOOK_URL 을 넣어 Google Sheets 등으로 함께 흘려보낸다. (README 참고)
LOG_PATH = Path(__file__).parent / "submissions.jsonl"

# 충전액. 발제자 패널의 예산 소진율 표시에만 쓴다. 학회원에게는 보이지 않는다.
BUDGET_USD = 15.0
LIMITS = {"name": 20, "question": 300, "sql": 4000, "result": 3000, "insight": 3000}

st.set_page_config(page_title="Olist 인사이트 셀프체크", page_icon="🔎", layout="centered")

# 입력창이 내용에 따라 늘어나게 한다. Streamlit 의 height 는 고정값이라
# 긴 쿼리·인사이트를 쓸 때 좁은 창 안에서 스크롤하며 작성하게 되어 불편하다.
#
# field-sizing: content 가 내용만큼 늘려주고, min/max-height 로 하한과 상한을 잡는다.
# 상한에 닿으면 그때부터 스크롤이므로 페이지가 끝없이 길어지지는 않는다.
# 이 속성을 모르는 구형 브라우저에서는 min-height 로 고정되어 예전과 같이 동작한다.
#
# 위젯별 하한은 key 로 구분한다 (Streamlit 이 컨테이너에 st-key-<key> 클래스를 붙인다).
st.markdown(
    """
    <style>
      /* 늘어나는 동작만 여기서 정한다. 높이 하한·상한은 아래 위젯별 규칙이 전담한다.
         (여기에 min-height 를 두면 특정성이 (0,1,2)라 (0,1,1)인 .st-key-* 규칙을
          이겨버려서 위젯별 높이가 먹지 않는다. 한 번 그 함정에 빠졌다.) */
      div[data-testid="stTextArea"] textarea {
        field-sizing: content;
        height: auto !important;
        line-height: 1.5;
      }
      /* 질문은 딱 1줄에서 시작해 길어지면 늘어난다. rem 로 어림하면 글꼴·여백에 따라
         어긋나므로 lh(줄높이 단위)로 1줄을 지정한다. 앞 선언은 lh 미지원 브라우저용 대비값. */
      .st-key-question textarea { min-height: 2.4rem !important; max-height: 12rem !important; }
      .st-key-question textarea { min-height: calc(1lh + 1rem) !important; }
      /* 쿼리와 인사이트는 길어지기 쉬우므로 처음부터 넉넉하게, 상한도 높게 */
      .st-key-sql textarea     { min-height: 13rem !important; max-height: 44rem !important; }
      .st-key-insight textarea { min-height: 13rem !important; max-height: 44rem !important; }
      .st-key-result textarea  { min-height: 9rem  !important; max-height: 32rem !important; }
      /* 쿼리·결과는 표와 코드라 고정폭 글꼴이 읽기 쉽다 */
      .st-key-sql textarea, .st-key-result textarea {
        font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        font-size: 0.86rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── 상태 ────────────────────────────────────────────────────────
st.session_state.setdefault("unlocked", False)
st.session_state.setdefault("admin_ok", False)
st.session_state.setdefault("calls", 0)
st.session_state.setdefault("spent", 0.0)
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


if "OPENAI_API_KEY" not in st.secrets:
    st.error(
        "OPENAI_API_KEY 가 설정되지 않았습니다.\n\n"
        '- 로컬: `.streamlit/secrets.toml` 에 `OPENAI_API_KEY = "sk-..."` 한 줄 추가\n'
        "- Streamlit Cloud: 앱 → Settings → Secrets 에 같은 줄 추가"
    )
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
    st.caption(f"모델: `{MODEL}` · effort `{EFFORT}`")
    # API 사용액은 일부러 학회원에게 보여주지 않는다. 돈이 보이면 재시도를 아끼게 되는데,
    # 이 도구의 가치는 고쳐서 다시 돌리는 반복에 있다. 금액은 발제자 패널에만 띄운다.

    # 발제자만 제출 로그를 본다. ADMIN_CODE 를 설정하지 않으면 패널 자체가 안 뜬다.
    admin_code = st.secrets.get("ADMIN_CODE", "")
    if admin_code:
        st.divider()
        entered = st.text_input("admin", type="password", key="admin_input")
        if entered:
            st.session_state.admin_ok = entered.strip() == admin_code
            if not st.session_state.admin_ok:
                st.error("코드가 맞지 않습니다.")


# ── 입력 ────────────────────────────────────────────────────────
st.title("🔎 Olist 인사이트 셀프체크")
st.caption(
    "질문 · 쿼리 · 실행 결과 · 인사이트를 넣으면 항목별로 몇 단계인지, "
    "한 단계 올리려면 뭘 해야 하는지 알려줍니다."
)

# st.info 의 파란 배경은 너무 튀어서, 테두리만 있는 중립 박스로 둔다.
with st.container(border=True):
    st.markdown(
        f"""**부담 없이 여러 번 돌려보세요.** 값이 저렴한 API로 결제해 두었으니 비용은
신경 쓰지 않으셔도 됩니다. **인당 {MAX_CALLS_PER_SESSION}번 정도까지** 가능합니다.

한 번에 잘 나오지 않는 게 정상입니다. 피드백을 보고 한 번 더 고쳐보세요."""
    )

name = st.text_input(
    "1. 이름",
    max_chars=LIMITS["name"],
)

# 한 문장이지만 길어질 수 있어 text_area 로 둔다. text_input 은 한 줄이라 늘어나지 않는다.
# 하한을 두 줄 높이로 낮게 잡아, 짧게 쓰면 한 줄 입력처럼 보인다.
question = st.text_area(
    "2. 무엇을 알아보려 했나요?",
    key="question",
    placeholder="예: 배송이 예상보다 늦은 주문은 리뷰 점수가 실제로 더 낮은가?",
    max_chars=LIMITS["question"],
)

# height 는 주지 않는다. 위 CSS 의 min/max-height 가 하한과 상한을 맡고,
# 그 사이에서는 입력 내용에 따라 창이 늘어난다.
sql = st.text_area(
    "3. 작성한 쿼리",
    key="sql",
    placeholder="SELECT c.customer_state, AVG(...) \nFROM olist_raw.orders AS o\nJOIN ...",
)
result = st.text_area(
    "4. 쿼리 실행 결과",
    key="result",
    placeholder="BigQuery 결과 표를 그대로 복사해서 붙여넣으세요. 행이 많으면 상위 10~20행이면 충분합니다.",
)
insight = st.text_area(
    "5. 도출한 인사이트",
    key="insight",
    placeholder=(
        "이 수치에서 무엇을 발견했는지, 왜 그렇다고 보는지, "
        "그래서 무엇을 하자는 것인지 써주세요.\n\n"
        "쿼리에서 판단이 필요했던 지점(취소 주문을 뺐다 / LEFT JOIN을 쓴 이유 / "
        "주문 단위로 셌다 등)이 있었다면 왜 그렇게 했는지도 적어주세요. "
        "쿼리 설계 판단 항목의 최고 단계 조건입니다."
    ),
)

# 다섯 항목 전부 채워야 버튼이 열린다. 하나라도 비면 어느 항목인지 이름으로 알려준다.
# 클릭 후 경고를 띄우는 대신 버튼을 잠그는 쪽을 택했다 — 빈 채로 호출되면
# 채점기가 근거 사슬을 판정할 수 없어 API 비용만 나가고 결과는 무의미하다.
FIELDS = (
    ("이름", name),
    ("질문", question),
    ("쿼리", sql),
    ("실행 결과", result),
    ("인사이트", insight),
)
missing = [label for label, value in FIELDS if not value.strip()]

go = st.button(
    "채점하기",
    type="primary",
    width="stretch",
    disabled=bool(missing),
)
if missing:
    st.caption(f"아직 비어 있는 항목: **{', '.join(missing)}** — 다 채우면 버튼이 열립니다.")


# ── 채점 ────────────────────────────────────────────────────────
def build_user_message(question: str, sql: str, result: str, insight: str) -> str:
    return (
        "아래 제출물을 루브릭에 따라 채점해줘.\n"
        "먼저 <query> 가 실제로 무엇을 세는지 한 문장으로 번역하고, "
        "그걸 <question> 과 나란히 비교한 뒤에 점수를 매겨라.\n\n"
        "<submission>\n"
        f"<question>\n{question.strip()}\n</question>\n\n"
        f"<query>\n{sql.strip()}\n</query>\n\n"
        f"<query_result>\n{result.strip()}\n</query_result>\n\n"
        f"<insight>\n{insight.strip()}\n</insight>\n"
        "</submission>"
    )


def grade(question: str, sql: str, result: str, insight: str) -> dict:
    client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    # 추론 모델은 Responses API 를 쓰는 쪽이 권장 경로다.
    # 시스템 프롬프트는 instructions 로 넘겨 입력 맨 앞에 놓는다. 매 호출 동일하므로
    # (2,892토큰 > 1024토큰) OpenAI 자동 프롬프트 캐싱이 걸린다.
    resp = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=[
            {"role": "user", "content": build_user_message(question, sql, result, insight)}
        ],
        reasoning={"effort": EFFORT},
        text={
            "format": {
                "type": "json_schema",
                "name": "grade",
                "schema": GRADE_SCHEMA,
                "strict": True,
            }
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", "") or "알 수 없음"
        if reason == "max_output_tokens":
            raise RuntimeError("응답이 잘렸습니다. 제출물을 조금 줄여서 다시 시도해주세요.")
        raise RuntimeError(f"응답이 완료되지 않았습니다 (사유: {reason}).")

    # 거절은 message 항목 안의 content 파트로 온다.
    for item in resp.output:
        for part in getattr(item, "content", None) or []:
            if part.type == "refusal":
                raise RuntimeError(f"채점기가 응답을 거절했습니다: {part.refusal}")

    data = json.loads(resp.output_text)
    data["_cost"] = call_cost(resp.usage)
    return data


def _detail(details, field: str) -> int:
    """usage 하위 details 는 SDK 버전에 따라 객체이거나 dict 다. 양쪽 다 받는다."""
    if details is None:
        return 0
    if isinstance(details, dict):
        return int(details.get(field, 0) or 0)
    return int(getattr(details, field, 0) or 0)


def call_cost(u) -> float:
    """이번 호출의 대략적인 비용($).

    OpenAI 의 input_tokens 는 캐시 적중분을 **포함한** 총량이므로 빼내서 따로 곱한다.
    output_tokens 는 추론 토큰을 이미 포함하므로 따로 더하지 않는다.
    """
    total_in = int(getattr(u, "input_tokens", 0) or 0)
    cached = _detail(getattr(u, "input_tokens_details", None), "cached_tokens")
    fresh_in = max(0, total_in - cached)
    out = int(getattr(u, "output_tokens", 0) or 0)
    return (fresh_in * PRICE_IN + cached * PRICE_CACHED + out * PRICE_OUT) / 1e6


def total_of(data: dict) -> int:
    """총점은 모델이 아니라 여기서 계산한다. 항목 점수는 만점 범위로 자른다."""
    return sum(max(0, min(mx, int(data[key]["score"]))) for key, _, mx in DIMENSIONS)


def log_submission(
    name: str, question: str, sql: str, result: str, insight: str, data: dict
) -> None:
    """제출물 한 건을 기록한다. 기록이 실패해도 채점 결과는 그대로 보여준다."""
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "name": name.strip(),
        "total": data["_total"],
        **{key: data[key]["score"] for key, _, _ in DIMENSIONS},
        "question": question.strip(),
        "sql": sql.strip(),
        "query_result": result.strip(),
        "insight": insight.strip(),
        "one_line": data.get("one_line", ""),
        "query_translation": data.get("query_translation", ""),
        "generic_check": data.get("generic_check", ""),
        "cost_usd": round(data.get("_cost", 0.0), 5),
    }
    line = json.dumps(row, ensure_ascii=False)

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 쓰기 불가 환경이어도 채점은 계속된다

    url = st.secrets.get("LOG_WEBHOOK_URL", "")
    if url:
        try:
            req = urllib.request.Request(
                url,
                data=line.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001 - 기록 실패가 채점을 막아서는 안 된다
            pass


def _log_fields(rows: list[dict]) -> list[str]:
    """컬럼 순서를 고정한다. 루브릭 개정으로 옛 행에 없는 키가 있어도 견디게
    실제 등장한 키의 합집합을 쓴다."""
    preferred = [
        "time", "name", "total",
        *[k for k, _, _ in DIMENSIONS],
        "question", "sql", "query_result", "insight",
        "one_line", "query_translation", "generic_check", "cost_usd",
    ]
    seen = {k for r in rows for k in r}
    return [k for k in preferred if k in seen] + sorted(seen - set(preferred))


def log_as_csv(rows: list[dict]) -> bytes:
    """엑셀에서 한글이 깨지지 않도록 BOM 을 붙인 UTF-8 CSV."""
    fields = _log_fields(rows)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fields})
    return buf.getvalue().encode("utf-8-sig")


def log_as_html(rows: list[dict]) -> bytes:
    """읽기용 제출 모음. 브라우저에서 열어 Ctrl+P → PDF 로 저장할 수 있다.
    (Streamlit Cloud 에는 Chrome 이 없어 서버에서 PDF 를 직접 만들 수 없다.)"""
    e = html.escape
    labels = {k: lb for k, lb, _ in DIMENSIONS}
    maxes = {k: mx for k, _, mx in DIMENSIONS}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>Olist 인사이트 제출 모음</title>
<style>
  body {{ font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
         max-width: 900px; margin: 0 auto; padding: 32px; line-height: 1.6; color: #222; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 17px; margin-top: 36px; background: #f4f4f6;
        padding: 8px 12px; border-left: 4px solid #666; }}
  h3 {{ font-size: 14px; margin: 18px 0 6px; color: #555; }}
  pre {{ background: #f7f7f9; border: 1px solid #e2e2e6; border-radius: 4px;
         padding: 12px; overflow-x: auto; font-size: 12px; white-space: pre-wrap; }}
  table.scores {{ border-collapse: collapse; font-size: 12px; margin: 10px 0; }}
  table.scores td, table.scores th {{ border: 1px solid #ddd; padding: 4px 10px; }}
  table.scores th {{ background: #f4f4f6; white-space: nowrap; }}
  .meta {{ color: #777; font-size: 12px; }}
  .total {{ font-size: 15px; font-weight: 700; }}
  .sub {{ page-break-inside: avoid; page-break-after: always; }}
  .sub:last-child {{ page-break-after: auto; }}
  @media print {{ body {{ padding: 0; }} h2 {{ background: none; }} }}
</style></head><body>
<h1>Olist 인사이트 셀프체크 — 제출 모음</h1>
<p class="meta">내려받은 시각 {e(stamp)} · 총 {len(rows)}건 · 최신순</p>"""]

    for i, r in enumerate(rows, 1):
        keys = [k for k in labels if k in r]
        head = "".join(f"<th>{e(labels[k])}</th>" for k in keys)
        cells = "".join(f"<td>{e(str(r[k]))} / {maxes[k]}</td>" for k in keys)
        parts.append(f"""
<div class="sub">
<h2>{i}. {e(str(r.get('name', '(이름 없음)')))}
    &nbsp;<span class="total">{e(str(r.get('total', '?')))} / 100</span></h2>
<p class="meta">{e(str(r.get('time', '')))}</p>
<table class="scores"><tr>{head}</tr><tr>{cells}</tr></table>
<h3>질문</h3><p>{e(str(r.get('question', '')))}</p>
<h3>쿼리</h3><pre>{e(str(r.get('sql', '')))}</pre>
<h3>실행 결과</h3><pre>{e(str(r.get('query_result', '')))}</pre>
<h3>인사이트</h3><p>{e(str(r.get('insight', ''))).replace(chr(10), '<br>')}</p>
<h3>채점기가 읽은 쿼리</h3>
<p class="meta">{e(str(r.get('query_translation', '')))}</p>
</div>""")

    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def read_log() -> list[dict]:
    """기록된 제출을 최신순으로 읽는다. 발제자 패널에서만 쓴다."""
    if not LOG_PATH.exists():
        return []
    rows = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows))


if go:
    problems = []
    # 버튼이 잠겨 있으므로 정상 경로에서는 도달하지 않는다. 안전망으로만 둔다.
    if missing:
        problems.append(f"비어 있는 항목이 있습니다: {', '.join(missing)}")
    for key, field in (
        ("name", name),
        ("question", question),
        ("sql", sql),
        ("result", result),
        ("insight", insight),
    ):
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
                data = grade(question, sql, result, insight)
                data["_total"] = total_of(data)
        except openai.RateLimitError as e:
            # OpenAI 는 크레딧 소진도 429 로 준다. 문구가 전혀 다르므로 갈라준다.
            if getattr(e, "code", "") == "insufficient_quota":
                st.error("발제자의 API 크레딧이 소진되었습니다. 발제자에게 알려주세요.")
            else:
                st.error("요청이 몰렸습니다. 30초 뒤에 다시 시도해주세요.")
        except openai.AuthenticationError:
            st.error("API 키가 유효하지 않습니다. 발제자에게 알려주세요.")
        except openai.APIStatusError as e:
            st.error(f"API 오류 ({e.status_code}). 잠시 후 다시 시도해주세요.")
        except openai.APIConnectionError:
            st.error("네트워크 연결에 실패했습니다.")
        except (RuntimeError, json.JSONDecodeError, StopIteration, KeyError, ValueError) as e:
            st.error(f"채점 결과를 읽지 못했습니다: {e}")
        else:
            data["_name"] = name.strip()
            st.session_state.calls += 1
            st.session_state.spent += data.get("_cost", 0.0)
            st.session_state.history.append(data["_total"])
            st.session_state.latest = data
            log_submission(name, question, sql, result, insight, data)
            # 사이드바는 스크립트 위쪽에서 이미 그려졌으므로 방금 늘린 횟수·사용액이
            # 반영되지 않는다. 다시 돌려야 사이드바가 최신 상태로 보인다.
            st.rerun()


# ── 결과 ────────────────────────────────────────────────────────
data = st.session_state.get("latest")
if data:
    st.divider()

    if data.get("_name"):
        st.markdown(f"### {data['_name']} 님의 채점 결과")

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

    if data.get("generic_check"):
        st.subheader("이 글이 정말 이 데이터를 보고 쓴 것인가요?")
        st.caption(
            "① 숫자가 반대로 나왔어도 그대로 쓸 수 있는 문장이 있는지 "
            "② 이 데이터를 실제로 만진 증거가 있는지 — 4단계는 이 검사를 통과해야 나옵니다."
        )
        st.warning(data["generic_check"])

    st.subheader("질문과 쿼리가 같은 것을 묻고 있나요?")
    st.markdown("**이 쿼리가 실제로 세고 있는 것**")
    st.code(data["query_translation"], language="text", wrap_lines=True)
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


# ── 제출 로그 (발제자용) ────────────────────────────────────────
if st.session_state.admin_ok:
    st.divider()
    rows = read_log()
    st.subheader(f"제출 로그 — {len(rows)}건")

    spent = sum(float(r.get("cost_usd", 0) or 0) for r in rows)
    left = max(0.0, BUDGET_USD - spent)
    per_call = spent / len(rows) if rows else 0.0
    a, b, c = st.columns(3)
    a.metric("사용액", f"${spent:.2f}", help=f"충전액 ${BUDGET_USD:.0f} 기준")
    b.metric("남은 예산", f"${left:.2f}", f"-{spent / BUDGET_USD * 100:.1f}%", delta_color="off")
    c.metric("회당 평균", f"${per_call:.4f}" if rows else "—",
             help="이 값으로 실제 회당 비용을 확인할 것. 예상은 $0.056.")
    st.progress(min(1.0, spent / BUDGET_USD))
    if rows:
        st.caption(
            f"이 속도면 남은 예산으로 약 {int(left / per_call):,}회 더 가능합니다. "
            "단 아래 로그가 초기화되면 이 합계도 함께 초기화되므로, "
            "정확한 잔액은 OpenAI 대시보드가 기준입니다."
        )

    if not rows:
        st.info("아직 이 인스턴스에 기록된 제출이 없습니다.")
    else:
        stamp = datetime.now().strftime("%Y%m%d")
        d1, d2, d3 = st.columns(3)
        d1.download_button(
            "📊 CSV (엑셀)",
            data=log_as_csv(rows),
            file_name=f"제출모음_{stamp}.csv",
            mime="text/csv",
            width="stretch",
            help="엑셀에서 바로 열립니다 (한글 깨짐 방지 처리됨)",
        )
        d2.download_button(
            "📄 HTML (인쇄·PDF)",
            data=log_as_html(rows),
            file_name=f"제출모음_{stamp}.html",
            mime="text/html",
            width="stretch",
            help="브라우저로 열어 Ctrl+P → 'PDF로 저장' 하면 PDF가 됩니다",
        )
        d3.download_button(
            "🗄 JSONL (원본)",
            data=LOG_PATH.read_bytes(),
            file_name=f"제출모음_{stamp}.jsonl",
            mime="application/x-ndjson",
            width="stretch",
            help="한 줄에 한 건. 나중에 다시 불러 쓰기 좋은 형식",
        )
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption("셀을 클릭하면 전체 내용이 펼쳐집니다.")
    st.warning(
        "이 파일은 **재배포·리부트 때 사라집니다.** 영구 보존이 필요하면 "
        "`LOG_WEBHOOK_URL` 시크릿을 설정하세요 (README 참고). "
        + ("현재 설정됨 ✅" if st.secrets.get("LOG_WEBHOOK_URL") else "현재 설정 안 됨 ⚠️")
    )
