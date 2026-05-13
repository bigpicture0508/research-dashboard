#!/usr/bin/env python3
"""
리서치 대시보드 전용 Google Sheets 헬퍼
"""
import sys
import json
from pathlib import Path
from datetime import datetime

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    print("설치 필요: pip install gspread google-auth")
    sys.exit(1)

SHEET_ID   = "1lVy8DX0NFdL3YsjNEy9LMR8XpY5FHR3oZ1Fjts_AUwo"
CREDS_PATH = Path(__file__).parent.parent / "credentials" / "service-account.json"
SCOPES     = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TAB_RESEARCH        = "리서치"
TAB_RESEARCH_RESULT = "리서치결과"
TAB_ACCESS_LOG      = "접근로그"
TAB_DRAFTS          = "임시저장"
TAB_VIDEO_SOURCE    = "영상소스"

MATERIAL_LINK_START = 5   # F열 (0-base), 재료링크 시작 (D=직원확장포인트, E=확장포인트2)
MATERIAL_LINK_COUNT = 50

HEADERS = {
    TAB_RESEARCH: [
        "번호", "제목", "유튜브링크",
        "키워드1", "키워드2", "키워드3", "키워드4", "키워드5",
        "추가키워드", "처리상태", "배정자",
        "제출갯수", "완료일시", "마무리일시", "수정허용", "영상포인트",
    ],
    TAB_RESEARCH_RESULT: [
        "제품번호", "제품명", "직원이름", "링크", "플랫폼", "제출일시",
    ],
    TAB_ACCESS_LOG: [
        "일시", "직원이름", "행동", "상세",
    ],
    TAB_DRAFTS: (
        ["직원이름", "제품번호", "저장일시", "확장포인트"] +
        [f"링크{i}" for i in range(1, 16)]
    ),
    TAB_VIDEO_SOURCE: (
        ["썸네일", "유튜브링크", "제목", "직원_확장포인트", "확장포인트2"] +
        [f"재료링크{i}" for i in range(1, MATERIAL_LINK_COUNT + 1)]
    ),
}

# 컬럼 인덱스 (0-based)
# A=번호 B=제목 C=유튜브링크 D~H=키워드1~5 I=추가키워드
# J=처리상태 K=배정자 L=제출갯수 M=완료일시 N=마무리일시 O=수정허용 P=영상포인트


_client_cache = {}

def get_client(force_refresh=False):
    key = str(CREDS_PATH)
    if force_refresh or key not in _client_cache:
        creds = Credentials.from_service_account_file(str(CREDS_PATH), scopes=SCOPES)
        _client_cache[key] = gspread.authorize(creds)
    return _client_cache[key]


def _get_ws(tab_name=TAB_RESEARCH):
    """워크시트 반환. SSL 끊김 시 자동 재연결."""
    try:
        return get_client().open_by_key(SHEET_ID).worksheet(tab_name)
    except Exception as e:
        if "ssl" in str(e).lower() or "eof" in str(e).lower() or "connection" in str(e).lower():
            return get_client(force_refresh=True).open_by_key(SHEET_ID).worksheet(tab_name)
        raise


def _ensure_tab(sh, tab_name):
    try:
        return sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        headers = HEADERS.get(tab_name, [])
        ws = sh.add_worksheet(title=tab_name, rows=5000, cols=max(len(headers), 2))
        if headers:
            ws.append_row(headers)
        return ws


def _detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u:                               return "TikTok"
    if "xiaohongshu" in u or "xhslink" in u:           return "샤오홍슈"
    if "douyin.com" in u:                               return "도우인"
    return "기타"


def _parse_row(i, row):
    while len(row) < 16:
        row.append("")
    try:
        extra = json.loads(row[8]) if row[8].strip() else {}
    except Exception:
        extra = {}
    cnt = row[11].strip()
    return {
        "row":           i,
        "number":        row[0].strip(),
        "title":         row[1].strip(),
        "url":           row[2].strip(),
        "keywords":      [row[j].strip() for j in range(3, 8)],
        "extra":         extra,
        "status":        row[9].strip(),
        "assignee":      row[10].strip(),
        "submit_count":  int(cnt) if cnt.isdigit() else 0,
        "done_at":       row[12].strip(),
        "finished_at":   row[13].strip(),
        "revision_open": row[14].strip() == "Y",
        "video_point":   row[15].strip(),
    }


# ─── 읽기 ────────────────────────────────────────────────────

def read_all():
    ws = _get_ws()
    rows = ws.get_all_values()
    return [_parse_row(i, list(r)) for i, r in enumerate(rows[1:], 2) if r and r[2].strip()]


def get_staff_worked_urls(assignee: str) -> set:
    """직원이 마무리 완료한 유튜브 URL 집합 (수정허용 제외)."""
    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(TAB_RESEARCH)
    except Exception:
        return set()
    rows = ws.get_all_values()
    worked = set()
    for row in rows[1:]:
        p = _parse_row(0, list(row))
        if p["assignee"] == assignee and p["finished_at"] and not p["revision_open"]:
            if p["url"]:
                worked.add(p["url"].strip())
    return worked


def get_my_submissions(product_number: str, assignee: str, hide_url: bool = True) -> list:
    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(TAB_RESEARCH_RESULT)
    except Exception:
        return []
    rows = ws.get_all_values()
    items = [
        {
            "link":         r[3] if len(r) > 3 else "",
            "platform":     r[4] if len(r) > 4 else "",
            "submitted_at": r[5] if len(r) > 5 else "",
        }
        for r in rows[1:]
        if len(r) >= 3 and r[0].strip() == str(product_number) and r[2].strip() == assignee
    ]
    if hide_url:
        for item in items:
            item["link"] = ""
    return items


def get_payroll_summary() -> list:
    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(TAB_RESEARCH)
    except Exception:
        return []
    rows = ws.get_all_values()
    result = []
    for i, row in enumerate(rows[1:], 2):
        p = _parse_row(i, list(row))
        if p["assignee"]:
            result.append({
                "number":       p["number"],
                "title":        p["title"],
                "assignee":     p["assignee"],
                "submit_count": p["submit_count"],
                "done":         p["submit_count"] >= 10,
                "done_at":      p["done_at"],
                "finished_at":  p["finished_at"],
            })
    return result


# ─── 쓰기 ────────────────────────────────────────────────────

def renumber_all():
    """A열 번호를 1부터 순서대로 재정렬"""
    ws = _get_ws()
    rows = ws.get_all_values()
    updates = []
    num = 1
    for i, row in enumerate(rows[1:], 2):
        if row and len(row) > 2 and row[2].strip():
            updates.append({"range": f"A{i}", "values": [[str(num)]]})
            num += 1
    if updates:
        ws.batch_update(updates)


def fill_numbers_and_titles(rows_info: list):
    """번호/제목 없는 행에 자동으로 채움. rows_info: [(row_num, number, title), ...]"""
    ws = _get_ws()
    updates = []
    for row_num, number, title in rows_info:
        updates.append({"range": f"A{row_num}:B{row_num}", "values": [[number, title]]})
    if updates:
        ws.batch_update(updates)


def append_row(number: str, title: str, url: str) -> int:
    ws = _get_ws()
    ws.append_row([number, title, url] + [""] * 12)
    return len(ws.get_all_values())


def write_keywords(row_num: int, keywords: list, extra: dict = None):
    ws = _get_ws()
    kws = (keywords + [""] * 5)[:5]
    extra_str = json.dumps(extra or {}, ensure_ascii=False)
    ws.update(range_name=f"D{row_num}:J{row_num}", values=[kws + [extra_str, "완료"]])


def claim(row_num: int, name: str) -> bool:
    ws = _get_ws()
    # 현재 배정자 확인 (1회 read)
    current = ws.cell(row_num, 11).value
    if current and current.strip():
        return False
    # 배정 write (1회 write) — verify 생략으로 속도 향상
    ws.update_cell(row_num, 11, name)
    return True


def unclaim(row_num: int):
    ws = _get_ws()
    ws.update_cell(row_num, 11, "")


def _add_to_video_source(sh, youtube_url: str, title: str, material_url: str, worker_ext_point: str = ""):
    """영상소스 탭에서 유튜브링크 행 찾아 빈 재료링크 슬롯에 추가. 없으면 새 행 생성.
    worker_ext_point: 직원이 입력한 확장포인트 → D열(직원_확장포인트)에 저장.
    """
    import time as _t
    ws = _ensure_tab(sh, TAB_VIDEO_SOURCE)
    rows = ws.get_all_values()

    # 유튜브링크 기준으로 행 탐색 (B열 = index 1)
    target_row = None
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > 1 and r[1].strip() == youtube_url.strip():
            target_row = i
            row_data = r
            break

    if target_row is None:
        # 새 행 추가 (썸네일 수식, 직원_확장포인트 포함)
        import re as _re
        vid_m = _re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", youtube_url)
        thumb = f'=IMAGE("https://img.youtube.com/vi/{vid_m.group(1)}/0.jpg")' if vid_m else ""
        # A=썸네일 B=유튜브링크 C=제목 D=직원_확장포인트 E=확장포인트2(빈값) F~=재료링크
        new_row = [thumb, youtube_url, title, worker_ext_point, "", material_url] + [""] * (MATERIAL_LINK_COUNT - 1)
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        return

    # 직원_확장포인트 업데이트 (D열=index 3, 기존 값이 없을 때만)
    existing_ext = row_data[3].strip() if len(row_data) > 3 else ""
    if worker_ext_point and not existing_ext:
        ws.update_cell(target_row, 4, worker_ext_point)
        _t.sleep(0.1)
    elif worker_ext_point and existing_ext:
        # 기존 값에 줄바꿈으로 추가
        ws.update_cell(target_row, 4, existing_ext + "\n" + worker_ext_point)
        _t.sleep(0.1)

    # 빈 재료링크 슬롯 탐색 (F열=index 5 부터)
    end = MATERIAL_LINK_START + MATERIAL_LINK_COUNT
    for col_idx in range(MATERIAL_LINK_START, end):
        val = row_data[col_idx].strip() if col_idx < len(row_data) else ""
        if not val:
            ws.update_cell(target_row, col_idx + 1, material_url)
            _t.sleep(0.1)
            return


def submit_link(row_num: int, product_number: str, product_title: str,
                assignee: str, link: str, worker_ext_point: str = "") -> dict:
    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    ws_result = _ensure_tab(sh, TAB_RESEARCH_RESULT)
    now_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
    platform = _detect_platform(link)
    ws_result.append_row([product_number, product_title, assignee, link, platform, now_str])

    all_rows = ws_result.get_all_values()
    count = sum(
        1 for r in all_rows[1:]
        if len(r) >= 3 and r[0].strip() == str(product_number) and r[2].strip() == assignee
    )

    ws = sh.worksheet(TAB_RESEARCH)
    ws.update_cell(row_num, 12, count)

    done_at = ws.cell(row_num, 13).value or ""
    if count >= 10 and not done_at:
        done_at = now_str
        ws.update_cell(row_num, 13, done_at)

    # 영상소스 탭에 재료링크 + 직원_확장포인트 자동 추가
    try:
        youtube_url = ws.cell(row_num, 3).value or ""  # C열 = 유튜브링크
        if youtube_url:
            _add_to_video_source(sh, youtube_url, product_title, link, worker_ext_point)
    except Exception:
        pass

    return {"count": count, "done": count >= 10, "done_at": done_at}


def finish(row_num: int) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws = _get_ws()
    ws.update(range_name=f"N{row_num}:O{row_num}", values=[[now_str, ""]])
    return now_str


def save_draft(assignee: str, product_number: str, links: list,
               video_point: str = "", expansion_point: str = ""):
    """링크 + 확장포인트 임시저장.
    열 순서: A=직원이름 B=제품번호 C=저장일시 D=확장포인트 E~S=링크1~15"""
    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = _ensure_tab(sh, TAB_DRAFTS)
    # 헤더 D열이 확장포인트인지 확인 후 아니면 업데이트
    header = ws.row_values(1)
    if len(header) >= 4 and header[3] != "확장포인트":
        ws.update(range_name="A1:T1", values=[["직원이름", "제품번호", "저장일시", "확장포인트"] + [f"링크{i}" for i in range(1, 16)]])
    rows = ws.get_all_values()
    target_row = None
    for i, r in enumerate(rows[1:], 2):
        if len(r) >= 2 and r[0].strip() == assignee and r[1].strip() == str(product_number):
            target_row = i
            break
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    padded = (links + [""] * 15)[:15]
    row_data = [assignee, str(product_number), now_str, expansion_point] + padded
    end_col = "T"
    if target_row:
        ws.update(range_name=f"A{target_row}:{end_col}{target_row}", values=[row_data])
    else:
        ws.append_row(row_data)


def load_draft(assignee: str, product_number: str) -> dict:
    """임시저장 불러오기. {"links": [...], "video_point": "..."}"""
    try:
        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = _ensure_tab(sh, TAB_DRAFTS)
        rows = ws.get_all_values()
        for r in rows[1:]:
            if len(r) >= 2 and r[0].strip() == assignee and r[1].strip() == str(product_number):
                expansion_point = r[3].strip() if len(r) > 3 else ""
                links = (list(r[4:19]) + [""] * 15)[:15]
                return {"links": links, "expansion_point": expansion_point}
    except Exception:
        pass
    return {"links": [""] * 15, "expansion_point": ""}


def clear_draft(assignee: str, product_number: str):
    """제출 완료 후 임시저장 삭제."""
    try:
        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = _ensure_tab(sh, TAB_DRAFTS)
        rows = ws.get_all_values()
        for i, r in enumerate(rows[1:], 2):
            if len(r) >= 2 and r[0].strip() == assignee and r[1].strip() == str(product_number):
                ws.delete_rows(i)
                return
    except Exception:
        pass


def write_video_point(row_num: int, text: str):
    ws = _get_ws()
    ws.update_cell(row_num, 16, text)


def allow_revision(row_num: int):
    ws = _get_ws()
    ws.update(range_name=f"N{row_num}:O{row_num}", values=[["", "Y"]])


def write_log(name: str, action: str, detail: str = ""):
    try:
        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = _ensure_tab(sh, TAB_ACCESS_LOG)
        ws.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, action, detail])
    except Exception:
        pass
