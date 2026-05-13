#!/usr/bin/env python3
"""
리서치 대시보드
실행: streamlit run tools/dashboard.py
"""
import sys
import json
import time
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
)

@st.cache_data(ttl=30)
def read_all():
    return _read_all_raw()

def clear_cache():
    read_all.clear()

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
             ("my_name",""),("login_at",0.0),("pin_tries",0),("locked_until",0.0)]:
    if k not in st.session_state:
        st.session_state[k] = v

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
                st.session_state.update(authenticated=True, is_admin=False,
                                        my_name=name, login_at=_now(), pin_tries=0)
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
                st.session_state.update(authenticated=True, is_admin=True,
                                        my_name="관리자", login_at=_now())
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
        with st.expander("🔢 번호 재정렬만 하기"):
            st.caption("제품이 중간에 삭제된 경우 번호를 1부터 다시 정렬합니다.")
            if st.button("번호 재정렬 실행"):
                from research_sheets import renumber_all
                renumber_all()
                st.success("재정렬 완료!")

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
                            extra = t_my_item.get("extra", {})
                            if extra:
                                st.markdown("---")
                                for cat, cat_kws in extra.items():
                                    if not cat_kws: continue
                                    st.markdown(f"*{cat}*")
                                    ecols = st.columns(min(len(cat_kws), 4))
                                    for ei, kw in enumerate(cat_kws):
                                        with ecols[ei % 4]:
                                            if st.button(kw, key=f"t_ex_{cat}_{ei}", use_container_width=True):
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

        st.markdown("**🔍 검색 열기**")
        ba, bb, bc = st.columns(3)
        with ba:
            if st.button(f"1순위: {kws[0]}", key="ms1", type="primary", use_container_width=True):
                write_log(name, "검색오픈", f"제품{num}/1/{kws[0]}")
                st.session_state.open_js = open_js(kws[0])
                st.rerun()
        with bb:
            if len(kws) > 1 and st.button(f"2순위: {kws[1]}", key="ms2", use_container_width=True):
                write_log(name, "검색오픈", f"제품{num}/2/{kws[1]}")
                st.session_state.open_js = open_js(kws[1])
                st.rerun()
        with bc:
            if len(kws) > 2 and st.button(f"3순위: {kws[2]}", key="ms3", use_container_width=True):
                write_log(name, "검색오픈", f"제품{num}/3/{kws[2]}")
                st.session_state.open_js = open_js(kws[2])
                st.rerun()

        # 1~3순위 전체 열기
        if st.button("🚀 1~3순위 전체 열기 (틱톡+샤오홍슈+도우인 동시)", key="ms_all", use_container_width=True):
            top3 = kws[:3]
            all_urls = []
            for kw in top3:
                all_urls += list(make_urls(kw).values())
            js = "<script>" + "\n".join(f'window.open("{u}","_blank");' for u in all_urls) + "</script>"
            write_log(name, "전체검색오픈", f"제품{num}/1~3순위")
            st.session_state.open_js = js
            st.rerun()

        with st.expander("키워드 선택 검색"):
            write_log(name, "키워드열람", f"제품{num}")
            for rank, kw in enumerate(kws, 1):
                cb, cl = st.columns([2, 5])
                with cb:
                    if st.button(f"{rank}순위: {kw}", key=f"kw{rank}"):
                        write_log(name, "검색오픈", f"제품{num}/{rank}/{kw}")
                        st.session_state.open_js = open_js(kw)
                        st.rerun()
                with cl:
                    st.markdown(" | ".join(f"[{n}]({u})" for n, u in make_urls(kw).items()))

            extra = my_item.get("extra", {})
            if extra:
                st.markdown("---")
                st.markdown("**관점별 추가 키워드** (AI가 대본을 분석해 분류)")
                for cat, cat_kws in extra.items():
                    if not cat_kws: continue
                    st.markdown(f"*{cat}*")
                    cols = st.columns(min(len(cat_kws), 4))
                    for ci2, kw in enumerate(cat_kws):
                        with cols[ci2 % 4]:
                            if st.button(kw, key=f"ex_{cat}_{ci2}", use_container_width=True):
                                write_log(name, "검색오픈", f"추가/{kw}")
                                st.session_state.open_js = open_js(kw)
                                st.rerun()

        # 직접 키워드 검색
        with st.expander("✏️ 직접 키워드 검색"):
            custom_kw = st.text_input("검색어 직접 입력", placeholder="예: facial mist, 补水喷雾", key="custom_kw")
            if st.button("🔍 직접 검색", key="custom_search", disabled=not custom_kw.strip()):
                write_log(name, "직접검색", f"{custom_kw}")
                st.session_state.open_js = open_js(custom_kw.strip())
                st.rerun()

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
