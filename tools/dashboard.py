#!/usr/bin/env python3
"""
리서치 대시보드
실행: streamlit run tools/dashboard.py
"""
import sys
import json
import time
import hmac
import hashlib
import tempfile
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# ─── Credentials (로컬 or Streamlit Cloud Secrets) ────────────
_ROOT      = Path(__file__).parent.parent
_CREDS     = _ROOT / "credentials" / "service-account.json"

if not _CREDS.exists():
    try:
        _sa  = dict(st.secrets["gcp_service_account"])
        _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                           delete=False, encoding="utf-8")
        json.dump(_sa, _tmp); _tmp.close()
        _CREDS = Path(_tmp.name)
    except Exception:
        st.error("Google 인증 정보가 없습니다. Secrets에 gcp_service_account를 설정해주세요.")
        st.stop()

sys.path.insert(0, str(Path(__file__).parent))
import research_sheets as rs
rs.CREDS_PATH = _CREDS

from research_sheets import (
    read_all as _read_all_raw, claim, unclaim, submit_link, finish,
    allow_revision, get_staff_worked_urls,
    get_my_submissions, get_payroll_summary, write_log,
    write_keywords, append_row, renumber_all, fill_numbers_and_titles,
    write_video_point,
)

@st.cache_data(ttl=30)
def read_all():
    return _read_all_raw()

def clear_cache():
    read_all.clear()

# ─── 토큰 ────────────────────────────────────────────────────
def _token_secret():
    try:
        return str(st.secrets["admin_password"])
    except Exception:
        return "fallback_secret"

def _make_token(name: str, is_admin: bool, login_at: float) -> str:
    payload = f"{name}|{int(is_admin)}|{int(login_at)}"
    sig = hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]  # type: ignore
    return f"{payload}|{sig}"

def _verify_token(token: str):
    try:
        parts = token.split("|")
        if len(parts) != 4:
            return None
        name, is_admin_s, login_at_s, sig = parts
        payload = f"{name}|{is_admin_s}|{login_at_s}"
        expected = hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        login_at = float(login_at_s)
        if _now() - login_at > SESSION_HOURS * 3600:
            return None
        return {"name": name, "is_admin": is_admin_s == "1", "login_at": login_at}
    except Exception:
        return None

# ─── 상수 ────────────────────────────────────────────────────
SESSION_HOURS = 2
MAX_PIN_TRIES = 5
LOCKOUT_MINS  = 30
TARGET_LINKS  = 10

PLATFORMS = {
    "TikTok":   "https://www.tiktok.com/search?q={q}",
    "샤오홍슈": "https://www.xiaohongshu.com/search_result?keyword={q}",
    "도우인":   "https://www.douyin.com/search/{q}",
}

def make_urls(kw):
    q = quote(kw)
    return {n: t.format(q=q) for n, t in PLATFORMS.items()}

def open_js(kw):
    urls = make_urls(kw)
    return "<script>" + "\n".join(f'window.open("{u}","_blank");' for u in urls.values()) + "</script>"

def _get_triplets(item_extra: dict, kws: list):
    """extra JSON에서 main_detail, extra 트리플렛 추출. 구형 포맷 호환."""
    main_detail = item_extra.get("main_detail", [])
    extra_list  = item_extra.get("extra", [])
    # 구형: main_detail 없으면 kws 문자열로 폴백
    if not main_detail:
        main_detail = [{"zh": k, "en": "", "ko": ""} for k in kws]
    # extra 구형 dict 포맷 호환
    if isinstance(extra_list, dict):
        extra_list = [{"zh": k, "en": "", "ko": ""} for v in extra_list.values() for k in v if k]
    return main_detail, extra_list

# ─── 페이지 ──────────────────────────────────────────────────
st.set_page_config(page_title="리서치 대시보드", page_icon="🔍", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
body { -webkit-user-select:none; user-select:none; }
a, button { -webkit-user-select:auto; user-select:auto; }
.watermark {
    position:fixed; top:50%; left:50%;
    transform:translate(-50%,-50%) rotate(-30deg);
    font-size:4rem; font-weight:900;
    color:rgba(180,180,180,0.13);
    pointer-events:none; z-index:9999;
    white-space:nowrap; user-select:none;
}
</style>""", unsafe_allow_html=True)

# ─── 세션 초기화 ─────────────────────────────────────────────
def _now(): return time.time()

for k, v in [("open_js",""),("authenticated",False),("is_admin",False),
             ("my_name",""),("login_at",0.0),("pin_tries",0),("locked_until",0.0),
             ("_token_loaded", False)]:
    if k not in st.session_state:
        st.session_state[k] = v

# query_params 토큰으로 세션 복원 (새로고침 대응)
if not st.session_state._token_loaded:
    st.session_state._token_loaded = True
    try:
        token = st.query_params.get("t", "")
        if token and not st.session_state.authenticated:
            info = _verify_token(token)
            if info:
                st.session_state.update(
                    authenticated=True,
                    is_admin=info["is_admin"],
                    my_name=info["name"],
                    login_at=info["login_at"],
                )
    except Exception:
        pass

if st.session_state.open_js:
    components.html(st.session_state.open_js, height=0)
    st.session_state.open_js = ""

# 세션 만료
if st.session_state.authenticated:
    if _now() - st.session_state.login_at > SESSION_HOURS * 3600:
        name = st.session_state.my_name
        write_log(name, "세션만료")
        for k in ["authenticated","is_admin","my_name","login_at"]:
            st.session_state[k] = False if isinstance(st.session_state[k], bool) else (
                "" if isinstance(st.session_state[k], str) else 0.0)
        st.query_params.clear()
        st.warning(f"{SESSION_HOURS}시간 후 자동 로그아웃되었습니다. 다시 로그인해주세요.")
        st.rerun()

# ══════════════════════════════════════════════════════════════
# 로그인
# ══════════════════════════════════════════════════════════════
if not st.session_state.authenticated:
    st.title("🔐 리서치 대시보드")

    if _now() < st.session_state.locked_until:
        remain = int((st.session_state.locked_until - _now()) / 60) + 1
        st.error(f"⛔ PIN 오류 초과 — {remain}분 후 재시도 가능합니다.")
        st.stop()

    tab_s, tab_a = st.tabs(["직원 로그인", "관리자 로그인"])

    with tab_s:
        inp_name = st.text_input("이름", placeholder="예) 김민지")
        inp_pin  = st.text_input("PIN", type="password", placeholder="관리자에게 받은 PIN")
        if st.button("로그인", type="primary", key="s_login"):
            name = inp_name.strip(); pin = inp_pin.strip()
            try:
                pins = dict(st.secrets.get("staff_pins", {}))
            except Exception:
                pins = {}
            if not name or not pin:
                st.warning("이름과 PIN을 모두 입력해주세요.")
            elif pins.get(name) == pin:
                login_at = _now()
                st.session_state.update(authenticated=True, is_admin=False,
                                        my_name=name, login_at=login_at, pin_tries=0)
                st.query_params["t"] = _make_token(name, False, login_at)
                write_log(name, "로그인")
                st.rerun()
            else:
                st.session_state.pin_tries += 1
                left = MAX_PIN_TRIES - st.session_state.pin_tries
                write_log(name or "?", "로그인실패", f"{st.session_state.pin_tries}회")
                if st.session_state.pin_tries >= MAX_PIN_TRIES:
                    st.session_state.locked_until = _now() + LOCKOUT_MINS * 60
                    st.error(f"⛔ {MAX_PIN_TRIES}회 오류 — {LOCKOUT_MINS}분 잠금")
                else:
                    st.error(f"이름 또는 PIN이 올바르지 않습니다. (남은 시도: {left}회)")

    with tab_a:
        pw = st.text_input("관리자 비밀번호", type="password", key="a_pw")
        if st.button("관리자 로그인", type="primary", key="a_login"):
            try:
                correct = str(st.secrets["admin_password"]).strip()
            except Exception:
                correct = ""
            if pw.strip() == correct and correct:
                login_at = _now()
                st.session_state.update(authenticated=True, is_admin=True,
                                        my_name="관리자", login_at=login_at)
                st.query_params["t"] = _make_token("관리자", True, login_at)
                write_log("관리자", "로그인")
                st.rerun()
            else:
                write_log("관리자", "로그인실패")
                st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ══════════════════════════════════════════════════════════════
# 인증 완료
# ══════════════════════════════════════════════════════════════
name      = st.session_state.my_name
is_admin  = st.session_state.is_admin
remain_h  = max(0, SESSION_HOURS - ((_now() - st.session_state.login_at) / 3600))

c_title, c_status, c_out = st.columns([5, 3, 1])
with c_title:
    st.title("관리자 대시보드" if is_admin else f"🔍 {name}님의 리서치")
with c_status:
    st.caption(f"{'👑 관리자' if is_admin else '👤'} **{name}** | 잔여 {remain_h:.1f}h")
with c_out:
    if st.button("로그아웃"):
        write_log(name, "로그아웃")
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.query_params.clear()
        st.rerun()

if not is_admin:
    st.markdown(f'<div class="watermark">{name}</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# 관리자
# ══════════════════════════════════════════════════════════════
if is_admin:
    tab_reg, tab_status, tab_payroll, tab_log, tab_preview = st.tabs(
        ["📥 링크 등록", "📋 제품 현황", "💰 급여 집계", "🔒 접근 로그", "👀 직원 뷰"])

    # ── 링크 등록 ────────────────────────────────────────────
    with tab_reg:
        st.header("유튜브 링크 등록")

        # 개별 등록
        c1, c2 = st.columns([1, 3])
        with c1: inp_no    = st.text_input("제품 번호", placeholder="1")
        with c2: inp_title = st.text_input("제목", placeholder="경쟁사A — 수분크림")
        inp_url = st.text_input("유튜브 링크", placeholder="https://www.youtube.com/watch?v=...")

        if st.button("🚀 추출 실행", type="primary", disabled=not inp_url.strip()):
            if not inp_no.strip():
                st.warning("제품 번호를 입력해주세요.")
            else:
                with st.spinner("대본 추출 중..."):
                    try:
                        row_num = append_row(inp_no.strip(), inp_title.strip(), inp_url.strip())
                    except Exception as e:
                        st.error(f"시트 행 추가 실패: {e}"); st.stop()
                with st.spinner("키워드 추출 중..."):
                    try:
                        from research_extractor import process_url
                        out = process_url(inp_url.strip(), inp_title.strip())
                    except Exception as e:
                        st.error(f"추출 오류: {e}"); st.stop()
                if not out["ok"]:
                    st.error(f"❌ {out.get('error')}")
                else:
                    write_keywords(row_num, out["keywords"], out["extra"])
                    write_log("관리자", "링크등록", f"제품{inp_no} {inp_url[:50]}")
                    st.success(f"✅ 저장 완료 — {' / '.join(out['keywords'])}")
                    ec = sum(len(v) for v in out["extra"].values())
                    st.info(f"추가 키워드 {ec}개 저장")
                    with st.expander("미리보기"):
                        for i, k in enumerate(out["keywords"], 1):
                            st.markdown(f"- {i}순위: **{k}**")
                        for cat, kws in out["extra"].items():
                            st.markdown(f"- **{cat}**: {', '.join(kws)}")

        st.divider()

        # 일괄 추출
        st.subheader("📋 시트 대기중 일괄 추출")
        st.caption("링크만 C열에 넣으면 번호/제목 자동 생성 + 키워드 추출까지 한 번에 처리합니다.")
        try:
            all_rows = read_all()
            pending = [r for r in all_rows if r["status"] != "완료" and r["url"]]
        except Exception:
            pending = []

        if pending:
            st.info(f"대기 중인 항목: **{len(pending)}개** (예상 소요: 약 {len(pending) * 5 // 60 + 1}분)")
            if st.button(f"⚡ 대기중 {len(pending)}개 일괄 추출 시작", type="primary"):
                from research_extractor import process_url as _pu
                from research_sheets import renumber_all, fill_numbers_and_titles

                # 1단계: 번호/제목 없는 행 먼저 채우기
                fill_updates = []
                existing_nums = [int(r["number"]) for r in all_rows if r["number"].isdigit()]
                next_num = max(existing_nums, default=0) + 1
                for row in pending:
                    num = row["number"] if row["number"] else str(next_num)
                    title = row["title"]
                    if not row["number"] or not row["title"]:
                        # 유튜브 제목 미리 가져오기
                        if not title:
                            try:
                                from youtube_extractor import extract as _yt
                                meta = _yt(row["url"])
                                title = meta.get("title") or f"영상{num}"
                            except Exception:
                                title = f"영상{num}"
                        fill_updates.append((row["row"], num, title))
                        row["number"] = num
                        row["title"] = title
                        if not row["number"]:
                            next_num += 1

                if fill_updates:
                    fill_numbers_and_titles(fill_updates)

                # 2단계: 키워드 추출
                ok_cnt = 0; err_cnt = 0
                prog = st.progress(0, text="준비 중...")
                log_area = st.empty()
                logs = []
                for i, row in enumerate(pending, 1):
                    prog.progress(i / len(pending), text=f"[{i}/{len(pending)}] {row['title'][:20]}")
                    out = _pu(row["url"], row["title"])
                    if out["ok"]:
                        if not row["title"] or row["title"].startswith("영상"):
                            row["title"] = out.get("title") or row["title"]
                        write_keywords(row["row"], out["keywords"], out["extra"])
                        write_log("관리자", "일괄추출", f"제품{row['number']}")
                        logs.append(f"✅ {row['number']}번 {row['title'][:15]}: {' / '.join(out['keywords'][:2])}")
                        ok_cnt += 1
                    else:
                        logs.append(f"❌ {row['number']}번: {out.get('error')}")
                        err_cnt += 1
                    log_area.text("\n".join(logs[-10:]))
                    if i < len(pending):
                        time.sleep(4.5)

                # 3단계: 번호 재정렬
                renumber_all()
                prog.progress(1.0, text="완료!")
                st.success(f"완료 {ok_cnt}개 / 실패 {err_cnt}개 — 번호 자동 재정렬 완료")
        else:
            st.success("✅ 대기 중인 항목이 없습니다.")

        # 번호 재정렬 단독 버튼
        st.markdown("---")
        st.caption("제품이 중간에 삭제된 경우 번호를 1부터 다시 정렬합니다.")
        if st.button("🔢 번호 재정렬 실행", key="renumber_btn"):
            renumber_all()
            clear_cache()
            st.success("재정렬 완료!")
            st.rerun()

    # ── 제품 현황 ────────────────────────────────────────────
    with tab_status:
        st.header("제품 현황")
        if st.button("🔄 새로고침", key="s_ref"): clear_cache(); st.rerun()
        try:
            all_data = read_all()
        except Exception as e:
            st.error(f"로드 실패: {e}"); all_data = []
        if all_data:
            import pandas as pd
            st.dataframe(pd.DataFrame([{
                "번호": d["number"], "제목": d["title"],
                "처리상태": d["status"], "배정자": d["assignee"] or "미배정",
                "제출갯수": d["submit_count"], "마무리": "✅" if d["finished_at"] else "",
            } for d in all_data]), use_container_width=True)

            st.markdown("---")
            st.subheader("배정 관리")
            for item in [d for d in all_data if d["assignee"]]:
                c1, c2, c3 = st.columns([5, 1, 1])
                tag = "🔄 수정중" if item["revision_open"] else "✅ 완료" if item["finished_at"] else "⏳ 진행중"
                with c1:
                    st.markdown(f"**{item['number']}번** {item['title']} → *{item['assignee']}* ({item['submit_count']}개) {tag}")
                with c2:
                    if item["finished_at"] and not item["revision_open"]:
                        if st.button("✏️ 수정허용", key=f"rev_{item['row']}", use_container_width=True):
                            allow_revision(item["row"])
                            write_log("관리자", "수정허용", f"제품{item['number']} {item['assignee']}")
                            clear_cache(); st.rerun()
                    else:
                        st.button("수정허용", key=f"rev_{item['row']}", disabled=True, use_container_width=True)
                with c3:
                    if st.button("초기화", key=f"rst_{item['row']}", use_container_width=True):
                        unclaim(item["row"])
                        write_log("관리자", "배정초기화", f"제품{item['number']}")
                        clear_cache(); st.rerun()
        else:
            st.info("등록된 제품이 없습니다.")

    # ── 급여 집계 ────────────────────────────────────────────
    with tab_payroll:
        st.header("💰 급여 집계")
        if st.button("🔄 새로고침", key="p_ref"): clear_cache(); st.rerun()
        try:
            payroll = get_payroll_summary()
        except Exception as e:
            payroll = []; st.warning(str(e))
        if payroll:
            import pandas as pd
            df = pd.DataFrame(payroll).rename(columns={
                "number":"제품번호","title":"제품명","assignee":"직원이름",
                "submit_count":"제출갯수","done":"완료여부","done_at":"완료일시"})
            df["완료여부"] = df["완료여부"].map({True:"✅ 완료", False:"⏳ 진행중"})
            st.dataframe(df[["직원이름","제품번호","제품명","제출갯수","완료여부","완료일시"]],
                         use_container_width=True)
            summary = (df[df["완료여부"]=="✅ 완료"].groupby("직원이름").size()
                       .reset_index(name="완료건수").sort_values("완료건수", ascending=False))
            if not summary.empty:
                st.markdown("**직원별 완료 건수**")
                st.dataframe(summary, use_container_width=True, hide_index=True)

            st.markdown("---")
            st.subheader("제출 링크 열람 (관리자 전용)")
            names = list(df["직원이름"].unique())
            sel_n = st.selectbox("직원", names, key="ps_n")
            sel_p = st.selectbox("제품번호", list(df[df["직원이름"]==sel_n]["제품번호"].unique()), key="ps_p")
            if st.button("링크 목록 열람"):
                links = get_my_submissions(sel_p, sel_n, hide_url=False)
                if links:
                    for i, lnk in enumerate(links, 1):
                        st.markdown(f"{i}. [{lnk['platform']}] [{lnk['link'][:70]}]({lnk['link']}) *{lnk['submitted_at']}*")
                else:
                    st.caption("제출 링크 없음")
        else:
            st.info("집계 데이터 없음")

    # ── 접근 로그 ────────────────────────────────────────────
    with tab_log:
        st.header("🔒 접근 로그")
        if st.button("🔄 새로고침", key="l_ref"): clear_cache(); st.rerun()
        try:
            import gspread
            gc = rs.get_client()
            sh = gc.open_by_key(rs.SHEET_ID)
            ws = sh.worksheet(rs.TAB_ACCESS_LOG)
            rows = ws.get_all_values()
            if len(rows) > 1:
                import pandas as pd
                df_log = pd.DataFrame(rows[1:], columns=rows[0])
                st.dataframe(df_log.iloc[::-1].head(300), use_container_width=True)
            else:
                st.info("로그 없음")
        except Exception as e:
            st.warning(f"로그 탭 없음 (첫 사용 후 자동 생성): {e}")

    # ── 직원 뷰 ──────────────────────────────────────────────
    with tab_preview:
        st.header("👀 직원 테스트 모드")
        st.caption("직원 이름을 선택하면 해당 직원 시점으로 완전히 테스트할 수 있습니다.")

        try:
            all_pins = dict(st.secrets.get("staff_pins", {}))
        except Exception:
            all_pins = {}
        staff_names = list(all_pins.keys())

        if not staff_names:
            st.warning("Secrets에 staff_pins가 없습니다.")
        else:
            test_name = st.selectbox("테스트할 직원 선택", staff_names, key="test_name")
            if st.button("🎭 이 직원으로 테스트 시작", type="primary"):
                st.session_state.test_mode = test_name
                st.rerun()

            if st.session_state.get("test_mode"):
                tname = st.session_state.test_mode
                st.info(f"**{tname}** 시점으로 테스트 중 — 실제 시트에 반영됩니다.")
                if st.button("❌ 테스트 종료"):
                    del st.session_state["test_mode"]
                    st.rerun()

                st.divider()

                # 직원 뷰 전체 렌더링
                try:
                    t_data = read_all()
                except Exception as e:
                    st.error(f"로드 실패: {e}"); t_data = []

                t_completed = [
                    d for d in t_data
                    if d["status"] == "완료" and any(d["keywords"])
                    and (not d["finished_at"] or d["revision_open"])
                ]
                t_my_item = next((d for d in t_completed if d["assignee"] == tname), None)
                try:
                    t_worked = get_staff_worked_urls(tname)
                except Exception:
                    t_worked = set()

                if t_my_item:
                    kws   = [k for k in t_my_item["keywords"] if k]
                    num   = t_my_item["number"] or "-"
                    title = t_my_item["title"] or "(제목없음)"
                    with st.container(border=True):
                        st.markdown(f"**{num}번** — {title}")
                        if st.button("🔍 영상 찾기 (1순위)", key="t_ms", type="primary"):
                            st.session_state.open_js = open_js(kws[0])
                            st.rerun()
                        with st.expander("키워드 선택 검색"):
                            for rank, kw in enumerate(kws, 1):
                                if st.button(f"{rank}순위: {kw}", key=f"t_kw{rank}"):
                                    st.session_state.open_js = open_js(kw)
                                    st.rerun()
                            t_extra = t_my_item.get("extra", {})
                            t_extra_kws = [k for v in t_extra.values() for k in v if k]
                            if t_extra_kws:
                                st.markdown("---")
                                st.markdown("**추가키워드** (제품 다른 표현)")
                                tecols = st.columns(min(len(t_extra_kws), 4))
                                for ei, kw in enumerate(t_extra_kws):
                                    with tecols[ei % 4]:
                                        if st.button(kw, key=f"t_ex_{ei}", use_container_width=True):
                                            st.session_state.open_js = open_js(kw)
                                            st.rerun()
                        st.divider()
                        sc = t_my_item["submit_count"]
                        st.markdown("**📎 링크 제출**")
                        if sc >= TARGET_LINKS:
                            st.success(f"✅ {sc}개 완료")
                        else:
                            st.progress(min(sc / TARGET_LINKS, 1.0), text=f"{sc} / {TARGET_LINKS}개")
                        with st.form(key=f"t_sf_{t_my_item['row']}", clear_on_submit=True):
                            t_link = st.text_input("링크 붙여넣기", placeholder="https://...")
                            if st.form_submit_button("제출", type="primary", use_container_width=True):
                                if t_link.strip():
                                    res = submit_link(t_my_item["row"], t_my_item["number"],
                                                      t_my_item["title"], tname, t_link.strip())
                                    st.success(f"저장 완료 — {res['count']}개")
                                    clear_cache(); st.rerun()
                        st.divider()
                        tf1, tf2 = st.columns(2)
                        with tf1:
                            if st.button("🏁 마무리하기", key="t_fin", type="primary",
                                         disabled=sc < TARGET_LINKS, use_container_width=True):
                                finish(t_my_item["row"])
                                st.success("마무리 완료!")
                                clear_cache(); st.rerun()
                        with tf2:
                            if st.button("↩️ 반납하기", key="t_unc", use_container_width=True):
                                unclaim(t_my_item["row"])
                                clear_cache(); st.rerun()
                else:
                    t_available = [d for d in t_completed if not d["assignee"] and d["url"] not in t_worked]
                    if t_available:
                        st.subheader("📋 작업할 제품 선택")
                        for item in t_available:
                            with st.container(border=True):
                                ca, cb = st.columns([5, 2])
                                with ca: st.markdown(f"**{item['number']}번** — {item['title'] or '(제목없음)'}")
                                with cb:
                                    if st.button("선택하기", key=f"t_cl_{item['row']}", type="primary", use_container_width=True):
                                        ok = claim(item["row"], tname)
                                        if ok:
                                            clear_cache(); st.rerun()
                                        else:
                                            st.warning("방금 다른 사람이 선택했습니다.")
                                            clear_cache(); st.rerun()
                    else:
                        st.info("선택 가능한 제품이 없습니다.")
    st.stop()

# ══════════════════════════════════════════════════════════════
# 직원
# ══════════════════════════════════════════════════════════════
st_autorefresh(interval=10000, key="staff_refresh")

try:
    data = read_all()
except Exception as e:
    st.error(f"시트 로드 실패: {e}"); st.stop()

completed = [
    d for d in data
    if d["status"] == "완료" and any(d["keywords"])
    and (not d["finished_at"] or d["revision_open"])
]
my_item = next((d for d in completed if d["assignee"] == name), None)

try:
    worked_urls = get_staff_worked_urls(name)
except Exception:
    worked_urls = set()

# ── 내 제품 ──────────────────────────────────────────────────
if my_item:
    if my_item["revision_open"]:
        st.subheader("✏️ 수정 중인 제품")
        st.info("관리자가 수정 기회를 부여했습니다.")
    else:
        st.subheader("✅ 내 작업 제품")

    kws    = [k for k in my_item["keywords"] if k]
    top_kw = kws[0]
    num    = my_item["number"] or "-"
    title  = my_item["title"]  or "(제목없음)"

    with st.container(border=True):
        ci, cs = st.columns([4, 2])
        with ci:
            st.markdown(f"**{num}번** — {title}")
            st.caption(f"1순위: {top_kw}")
        with cs:
            # 원본 유튜브 보기
            yt_url = my_item.get("url", "")
            if yt_url:
                st.link_button("▶ 원본 유튜브 보기", yt_url, use_container_width=True)

        extra_json  = my_item.get("extra", {})
        main_detail, extra_list = _get_triplets(extra_json, kws)

        st.markdown("**🔍 검색 열기**")

        # 1~3순위 빠른 버튼
        for rank in range(1, 4):
            if rank > len(main_detail): break
            t = main_detail[rank - 1]
            zh, en = t.get("zh",""), t.get("en","")
            label_zh = zh or en
            label_en = en or zh
            col_rank, col_zh, col_en, col_all = st.columns([1, 2, 2, 2])
            with col_rank:
                st.markdown(f"**{rank}순위**")
            with col_zh:
                if label_zh and st.button(label_zh, key=f"kw_zh_{rank}", use_container_width=True):
                    write_log(name, "검색오픈", f"제품{num}/{rank}/{label_zh}")
                    st.session_state.open_js = open_js(label_zh)
                    st.rerun()
            with col_en:
                if label_en and st.button(label_en, key=f"kw_en_{rank}", use_container_width=True):
                    write_log(name, "검색오픈", f"제품{num}/{rank}/{label_en}")
                    st.session_state.open_js = open_js(label_en)
                    st.rerun()
            with col_all:
                if st.button(f"↗ 전체열기", key=f"kw_all_{rank}", use_container_width=True):
                    urls = list(make_urls(label_zh).values()) + (list(make_urls(label_en).values()) if label_en != label_zh else [])
                    write_log(name, "검색오픈", f"제품{num}/{rank}/전체")
                    st.session_state.open_js = "<script>" + "\n".join(f'window.open("{u}","_blank");' for u in urls) + "</script>"
                    st.rerun()

        # 1~3순위 전체 열기
        if st.button("🚀 1~3순위 전체 열기 (틱톡+샤오홍슈+도우인 동시)", key="ms_all", use_container_width=True):
            all_urls = []
            for t in main_detail[:3]:
                for kw in [t.get("zh",""), t.get("en","")]:
                    if kw: all_urls += list(make_urls(kw).values())
            write_log(name, "전체검색오픈", f"제품{num}/1~3순위")
            st.session_state.open_js = "<script>" + "\n".join(f'window.open("{u}","_blank");' for u in all_urls) + "</script>"
            st.rerun()

        # 4~5순위 + 추가키워드
        with st.expander("4~5순위 및 추가키워드"):
            for rank in range(4, 6):
                if rank > len(main_detail): break
                t = main_detail[rank - 1]
                zh, en = t.get("zh",""), t.get("en","")
                c1, c2, c3 = st.columns([1, 2, 2])
                with c1: st.markdown(f"**{rank}순위**")
                with c2:
                    if zh and st.button(zh, key=f"kw45_zh_{rank}", use_container_width=True):
                        st.session_state.open_js = open_js(zh); st.rerun()
                with c3:
                    if en and st.button(en, key=f"kw45_en_{rank}", use_container_width=True):
                        st.session_state.open_js = open_js(en); st.rerun()

            if extra_list:
                st.markdown("---")
                st.markdown("**추가키워드** (제품 다른 표현)")
                for ci2, t in enumerate(extra_list):
                    zh, en = t.get("zh",""), t.get("en","")
                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    with ec1:
                        if zh and st.button(zh, key=f"ex_zh_{ci2}", use_container_width=True):
                            write_log(name, "검색오픈", f"추가/{zh}")
                            st.session_state.open_js = open_js(zh); st.rerun()
                    with ec2:
                        if en and st.button(en, key=f"ex_en_{ci2}", use_container_width=True):
                            write_log(name, "검색오픈", f"추가/{en}")
                            st.session_state.open_js = open_js(en); st.rerun()
                    with ec3:
                        if st.button("↗", key=f"ex_all_{ci2}", use_container_width=True):
                            urls = list(make_urls(zh).values()) if zh else []
                            if en and en != zh: urls += list(make_urls(en).values())
                            write_log(name, "검색오픈", f"추가/{zh}/전체")
                            st.session_state.open_js = "<script>" + "\n".join(f'window.open("{u}","_blank");' for u in urls) + "</script>"
                            st.rerun()

        # 직접 키워드 검색
        with st.expander("✏️ 직접 키워드 검색"):
            custom_kw = st.text_input("검색어 직접 입력", placeholder="예: facial mist, 补水喷雾", key="custom_kw")
            if st.button("🔍 직접 검색", key="custom_search", disabled=not custom_kw.strip()):
                write_log(name, "직접검색", f"{custom_kw}")
                st.session_state.open_js = open_js(custom_kw.strip())
                st.rerun()

        st.divider()

        # 영상포인트 메모
        st.markdown("**📝 영상포인트**")
        vp_current = my_item.get("video_point", "")
        vp_key = f"vp_{my_item['row']}"
        if vp_key not in st.session_state:
            st.session_state[vp_key] = vp_current
        vp_text = st.text_area("영상 속 제품 특징 메모", value=st.session_state[vp_key],
                               placeholder="예) 마스크팩 위에 덧바르는 수분 미스트, 냉장보관 강조, 30대 여성 타겟...",
                               key=f"vp_area_{my_item['row']}", height=100,
                               label_visibility="collapsed")
        if st.button("💾 영상포인트 저장", key=f"vp_save_{my_item['row']}"):
            write_video_point(my_item["row"], vp_text.strip())
            st.session_state[vp_key] = vp_text.strip()
            clear_cache()
            write_log(name, "영상포인트저장", f"제품{my_item['number']}")
            st.success("저장됐습니다.")

        st.divider()

        # 링크 제출
        sc      = my_item["submit_count"]
        done_at = my_item["done_at"]
        st.markdown("**📎 링크 제출** (최대 15개 한번에)")
        if sc >= TARGET_LINKS:
            st.success(f"✅ {sc}개 완료 — {done_at}")
        else:
            st.progress(min(sc / TARGET_LINKS, 1.0), text=f"{sc} / {TARGET_LINKS}개")

        with st.form(key=f"sf_{my_item['row']}", clear_on_submit=True):
            link_inputs = []
            for li in range(15):
                lv = st.text_input(f"링크 {li+1}", placeholder="https://...", key=f"link_{li}",
                                   label_visibility="collapsed" if li > 0 else "visible")
                link_inputs.append(lv)
            if st.form_submit_button("📤 한번에 제출", type="primary", use_container_width=True):
                valid_links = [l.strip() for l in link_inputs if l.strip()]
                if valid_links:
                    with st.spinner(f"{len(valid_links)}개 저장 중..."):
                        res = None
                        for lnk in valid_links:
                            res = submit_link(my_item["row"], my_item["number"],
                                              my_item["title"], name, lnk)
                            write_log(name, "링크제출", f"제품{num}/{lnk[:50]}")
                    if res and res["done"] and sc < TARGET_LINKS:
                        st.balloons()
                        st.success(f"🎉 {TARGET_LINKS}개 달성! 급여 대상 등록 완료.")
                    else:
                        st.success(f"{len(valid_links)}개 저장 완료 — 현재 {res['count']}개")
                    clear_cache(); st.rerun()

        with st.expander(f"제출 내역 ({sc}개)"):
            links = get_my_submissions(my_item["number"], name, hide_url=True)
            if links:
                for idx, lnk in enumerate(links, 1):
                    st.markdown(f"- **#{idx}** [{lnk['platform']}] {lnk['submitted_at']}")
            else:
                st.caption("아직 없음")

        st.divider()
        cf, cr = st.columns([3, 2])
        with cf:
            if st.button("🏁 작업 마무리하기", key="fin", type="primary",
                         disabled=sc < TARGET_LINKS, use_container_width=True):
                finished_at = finish(my_item["row"])
                write_log(name, "마무리", f"제품{num}/{sc}개")
                st.success(f"✅ 마무리 완료! ({finished_at})")
                clear_cache(); st.rerun()
            if sc < TARGET_LINKS:
                st.caption(f"{TARGET_LINKS - sc}개 더 제출하면 마무리 가능")
        with cr:
            if st.button("↩️ 반납하기", key="unc", use_container_width=True):
                write_log(name, "반납", f"제품{num}")
                unclaim(my_item["row"])
                clear_cache(); st.rerun()

    st.divider()

# ── 선택 가능한 제품 ──────────────────────────────────────────
available   = [d for d in completed if not d["assignee"]]
selectable  = [d for d in available if d["url"] not in worked_urls]

if not my_item:
    if selectable:
        st.subheader("📋 작업할 제품 선택")
        st.caption("하나를 선택하면 내 작업 화면으로 전환됩니다.")
        for item in selectable:
            num = item["number"] or "-"; title = item["title"] or "(제목없음)"
            with st.container(border=True):
                ci2, cb2 = st.columns([5, 2])
                with ci2: st.markdown(f"**{num}번** — {title}")
                with cb2:
                    if st.button("선택하기", key=f"cl_{item['row']}",
                                 type="primary", use_container_width=True):
                        ok = claim(item["row"], name)
                        if ok:
                            write_log(name, "제품선택", f"제품{num}")
                            clear_cache(); st.rerun()
                        else:
                            st.warning("방금 다른 사람이 먼저 선택했습니다.")
                            clear_cache(); st.rerun()
    else:
        st.info("현재 선택 가능한 제품이 없습니다. 잠시 후 다시 확인해주세요.")

st.divider()
st.caption(f"10초 자동 갱신 | 세션 {SESSION_HOURS}h 후 만료")
