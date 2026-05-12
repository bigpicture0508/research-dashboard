#!/usr/bin/env python3
"""
YouTube 링크 → 원본 대본 자동 추출

- 자막(자동 생성 포함) → 한국어 우선
- 자막 없으면 yt-dlp의 자동 자막으로 폴백
- 결과: video_id / title / description / channel / transcript

사용법:
  python tools/youtube_extractor.py <YouTube URL>
    → JSON 한 건을 stdout에 출력

  python tools/youtube_extractor.py fill-pending
    → output/pending.json 의 미처리 항목 중
      url은 있고 script는 비어있는 행에 자막을 채워서
      구글 시트 입력 탭의 '원본대본' 컬럼에 직접 업데이트

라이브러리:
  pip install youtube-transcript-api yt-dlp
"""
import sys
import re
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
    )
except ImportError:
    print("설치 필요: pip install youtube-transcript-api")
    sys.exit(1)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None  # 폴백 없이도 동작 가능

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ─── 비디오 ID 추출 ───────────────────────────────────────
_YT_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([A-Za-z0-9_-]{11})",
]


def parse_video_id(url):
    if not url:
        return None
    url = url.strip()
    # 이미 11자 ID만 들어온 경우
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    for pat in _YT_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ─── 자막 추출 ─────────────────────────────────────────────
def fetch_transcript(video_id, languages=("ko", "en")):
    """youtube-transcript-api v1.x 기준. 실패하면 None 반환."""
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages))
        # fetched는 snippet iterable
        text = " ".join(snippet.text.strip() for snippet in fetched if snippet.text.strip())
        return text or None
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception as e:
        print(f"⚠️  자막 추출 실패 ({type(e).__name__}): {e}", file=sys.stderr)
        return None


# ─── 메타데이터 (yt-dlp) ──────────────────────────────────
def fetch_metadata(url):
    """제목/설명/채널명. yt-dlp 미설치 시 빈 dict."""
    if yt_dlp is None:
        return {}
    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title":       info.get("title", ""),
            "description": info.get("description", "") or "",
            "channel":     info.get("uploader", "") or info.get("channel", ""),
            "duration":    info.get("duration", 0),
        }
    except Exception as e:
        print(f"⚠️  메타데이터 추출 실패: {e}", file=sys.stderr)
        return {}


# ─── 메인: URL → dict ──────────────────────────────────────
def extract(url):
    video_id = parse_video_id(url)
    if not video_id:
        return {"error": f"YouTube URL/ID 파싱 실패: {url}"}

    transcript = fetch_transcript(video_id)
    meta = fetch_metadata(url)

    return {
        "url":         url,
        "video_id":    video_id,
        "title":       meta.get("title", ""),
        "description": meta.get("description", ""),
        "channel":     meta.get("channel", ""),
        "duration":    meta.get("duration", 0),
        "transcript":  transcript or "",
        "ok":          bool(transcript),
    }


# ─── 시트 입력 탭에 자막 채우기 ────────────────────────────
def fill_pending():
    """output/pending.json의 url-only 항목에 자막을 추출해서
    구글 시트 입력 탭의 '원본대본' 셀에 직접 업데이트.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from sheets_helper import get_client, SHEET_ID, TAB_INPUT

    pending_file = OUTPUT_DIR / "pending.json"
    if not pending_file.exists():
        print("❌ output/pending.json 없음. 먼저 'sheets_helper.py read' 실행.")
        sys.exit(1)

    pending = json.loads(pending_file.read_text())
    targets = [p for p in pending if p.get("url") and not p.get("script")]
    if not targets:
        print("⏭  자막 채울 항목 없음 (모두 대본 있음 또는 URL 없음)")
        return

    gc = get_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(TAB_INPUT)

    filled = 0
    failed = []
    for p in targets:
        row = p["row"]
        url = p["url"]
        print(f"📥 row {row} 추출 중: {url}")
        result = extract(url)
        if result.get("ok") and result.get("transcript"):
            # 입력 탭 컬럼 2 = 원본대본
            ws.update_cell(row, 2, result["transcript"])
            print(f"  ✅ {len(result['transcript'])}자 채움")
            # pending.json도 갱신
            p["script"] = result["transcript"]
            filled += 1
        else:
            print(f"  ❌ 실패 (자막 없음 또는 비공개)")
            failed.append({"row": row, "url": url})

    # pending.json 업데이트
    pending_file.write_text(json.dumps(pending, ensure_ascii=False, indent=2))
    print(f"\n📊 채움 {filled}개 / 실패 {len(failed)}개")
    if failed:
        print("❌ 실패 목록:")
        for f in failed:
            print(f"  - row {f['row']}: {f['url']}")


# ─── CLI ───────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    arg = sys.argv[1]
    if arg == "fill-pending":
        fill_pending()
    elif arg in ("-h", "--help", "help"):
        print(__doc__)
    else:
        # URL 한 건 추출
        result = extract(arg)
        print(json.dumps(result, ensure_ascii=False, indent=2))
