#!/usr/bin/env python3
"""
유튜브 링크 → 키워드 추출 (Claude Haiku)
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import anthropic
except ImportError:
    print("설치 필요: pip install anthropic")
    sys.exit(1)

from youtube_extractor import extract as yt_extract

MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """아래 유튜브 영상 대본을 분석해서 틱톡/샤오홍슈/도우인 경쟁 영상 검색에 쓸 키워드를 추출하세요.

영상 제목: {title}

대본:
{transcript}

다음 JSON 형식만 반환하세요 (설명 없이):
{{
  "main": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
  "extra": {{
    "제품/카테고리": ["...", "..."],
    "사용상황/용도": ["...", "..."],
    "타겟고객": ["...", "..."],
    "문제/솔루션": ["...", "..."],
    "해시태그형": ["...", "..."]
  }}
}}

규칙:
- main: 중요도 순 5개, 실제 검색에 쓸 구체적 단어/구
- extra 각 카테고리: 3~5개씩, main과 겹치지 않는 다양한 관점
- 한국어 또는 중국어(간체) 혼용 가능
- JSON만 반환"""


def _parse(text: str):
    try:
        if "```" in text:
            for p in text.split("```"):
                p = p.strip().lstrip("json").strip()
                if p.startswith("{"):
                    text = p
                    break
        data = json.loads(text)
        main  = [str(k).strip() for k in data.get("main", [])[:5]]
        extra = {
            str(cat): [str(k).strip() for k in kws if str(k).strip()]
            for cat, kws in data.get("extra", {}).items()
            if isinstance(kws, list)
        }
        return main, extra
    except Exception:
        return [], {}


def extract_keywords(transcript: str, title: str = "") -> tuple:
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": _PROMPT.format(
            title=title or "(제목없음)",
            transcript=transcript[:4000],
        )}],
    )
    return _parse(msg.content[0].text.strip())


def process_url(url: str, title: str = "") -> dict:
    result = yt_extract(url)
    if not result.get("ok") or not result.get("transcript"):
        return {"ok": False, "error": "자막 추출 실패 (비공개 또는 자막 없음)"}
    transcript     = result["transcript"]
    resolved_title = result.get("title") or title
    main, extra    = extract_keywords(transcript, resolved_title)
    return {
        "ok":             True,
        "title":          resolved_title,
        "transcript_len": len(transcript),
        "keywords":       main,
        "extra":          extra,
    }
