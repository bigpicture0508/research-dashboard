#!/usr/bin/env python3
"""
유튜브 링크 → 키워드 추출 (Google Gemini Flash)
"""
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import google.generativeai as genai
except ImportError:
    print("설치 필요: pip install google-generativeai")
    sys.exit(1)

from youtube_extractor import extract as yt_extract

MODEL = "gemini-1.5-flash"

_PROMPT = """아래 유튜브 영상 대본을 분석해서 틱톡/샤오홍슈/도우인 경쟁 영상 검색에 쓸 키워드를 추출하세요.

영상 제목: {title}

대본:
{transcript}

다음 JSON 형식만 반환하세요 (설명 없이):
{{
  "main": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
  "extra": {{
    "产品/类别": ["...", "..."],
    "使用场景": ["...", "..."],
    "目标客户": ["...", "..."],
    "问题/解决方案": ["...", "..."],
    "hashtag": ["...", "..."]
  }}
}}

규칙:
- main: 중요도 순 5개, 반드시 중국어(간체) 또는 영어만 사용 (한국어 금지)
- extra 각 카테고리: 3~5개씩, 반드시 중국어(간체) 또는 영어만 사용 (한국어 금지)
- 틱톡/샤오홍슈/도우인 실제 검색에 쓸 구체적 단어/구
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


def _get_api_key():
    try:
        import streamlit as st
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return os.environ.get("GEMINI_API_KEY", "")


def extract_keywords(transcript: str, title: str = "") -> tuple:
    api_key = _get_api_key()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL)
    response = model.generate_content(_PROMPT.format(
        title=title or "(제목없음)",
        transcript=transcript[:4000],
    ))
    return _parse(response.text.strip())


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
