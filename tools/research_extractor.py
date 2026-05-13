#!/usr/bin/env python3
"""
유튜브 링크 → 키워드 추출 (Claude Haiku)
배치 실행: python tools/research_extractor.py
"""
import sys
import json
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import anthropic
except ImportError:
    print("설치 필요: pip install anthropic")
    sys.exit(1)

from youtube_extractor import extract as yt_extract

MODEL = "claude-haiku-4-5-20251001"
DELAY_BETWEEN = 1.0
MAX_RETRY = 3
RETRY_WAIT = 10

_PROMPT = """아래 유튜브 영상 대본을 분석해서 틱톡/샤오홍슈/도우인 경쟁 영상 검색에 쓸 키워드를 추출하세요.

영상 제목: {title}

대본:
{transcript}

다음 JSON 형식만 반환하세요 (설명 없이):
{{
  "main": [
    {{"zh": "中文검색어", "en": "English search term", "ko": "한국어 의미 설명"}},
    {{"zh": "...", "en": "...", "ko": "..."}},
    {{"zh": "...", "en": "...", "ko": "..."}},
    {{"zh": "...", "en": "...", "ko": "..."}},
    {{"zh": "...", "en": "...", "ko": "..."}}
  ],
  "extra": [
    {{"zh": "中文", "en": "English", "ko": "한국어 의미"}},
    {{"zh": "...", "en": "...", "ko": "..."}},
    {{"zh": "...", "en": "...", "ko": "..."}}
  ]
}}

규칙:
- main: 중요도 순 5개 키워드, 각각 zh(중국어간체)/en(영어)/ko(한국어 뜻) 세트로
- extra: 제품의 다른 표현/유사어 3~8개, 각각 zh/en/ko 세트로
- zh: 틱톡/샤오홍슈/도우인 실제 검색에 쓸 중국어 단어/구
- en: 영어는 반드시 띄어쓰기 포함 (예: "smoke exhaust fan" O, "SmokeExhaustFan" X)
- ko: 검색어가 무슨 뜻인지 한국어로 짧게 설명 (검색용 아님)
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

        def _to_triplet(item):
            if isinstance(item, dict):
                return {
                    "zh": str(item.get("zh", "")).strip(),
                    "en": str(item.get("en", "")).strip(),
                    "ko": str(item.get("ko", "")).strip(),
                }
            # 구형 문자열 포맷 호환
            return {"zh": str(item).strip(), "en": "", "ko": ""}

        main_raw = data.get("main", [])[:5]
        main_triplets = [_to_triplet(m) for m in main_raw]
        main = [t["zh"] or t["en"] for t in main_triplets]  # D~H열용 단일 문자열

        extra_raw = data.get("extra", [])
        if isinstance(extra_raw, list):
            extra_triplets = [_to_triplet(e) for e in extra_raw]
        else:
            # 구형 dict 포맷 호환
            extra_triplets = []
            for kws in extra_raw.values():
                for k in kws:
                    if k:
                        extra_triplets.append({"zh": str(k), "en": "", "ko": ""})

        extra = {"main_detail": main_triplets, "extra": extra_triplets}
        return main, extra
    except Exception:
        return [], {}


def _get_api_key():
    try:
        import streamlit as st
        return str(st.secrets.get("ANTHROPIC_API_KEY", ""))
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY", "")


def extract_keywords(transcript: str, title: str = "") -> tuple:
    api_key = _get_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(1, MAX_RETRY + 1):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": _PROMPT.format(
                    title=title or "(제목없음)",
                    transcript=transcript[:4000],
                )}],
            )
            return _parse(msg.content[0].text.strip())
        except Exception as e:
            msg_str = str(e).lower()
            if "429" in msg_str or "rate" in msg_str or "overloaded" in msg_str:
                wait = RETRY_WAIT * attempt
                print(f"  ⚠️  한도 초과 — {wait}초 대기 후 재시도 ({attempt}/{MAX_RETRY})")
                time.sleep(wait)
            else:
                print(f"  ❌ API 오류: {e}")
                return [], {}
    return [], {}


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


if __name__ == "__main__":
    from research_sheets import read_all, write_keywords

    pending = [r for r in read_all() if r["status"] != "완료"]
    total   = len(pending)

    if total == 0:
        print("✅ 처리할 항목이 없습니다.")
        sys.exit(0)

    print(f"📋 대기 중인 항목: {total}개")
    ok_count = err_count = 0

    for i, row in enumerate(pending, 1):
        print(f"[{i}/{total}] #{row['number']} {(row['title'] or row['url'])[:30]}")
        result = process_url(row["url"], row["title"])
        if result["ok"]:
            write_keywords(row["row"], result["keywords"], result["extra"])
            print(f"  ✅ {' / '.join(result['keywords'])}")
            ok_count += 1
        else:
            print(f"  ❌ {result.get('error')}")
            err_count += 1
        if i < total:
            time.sleep(DELAY_BETWEEN)

    print(f"완료 {ok_count}개 / 실패 {err_count}개")
