"""로그에 쌓인 [usage] 줄을 집계해 LLM 토큰 사용량을 모델별로 보고한다.

두 백엔드가 같은 포맷으로 남기는 줄을 합산한다:
    OpenAI (openai_processor._call_api):
        [usage] model=... in=... out=... (reasoning=...)
    Claude CLI (claude_code_processor._run_claude):
        [usage] model=... in=... out=... (cache_read=... cache_write=...)

단가는 하드코딩하지 않는다 — OpenAI 실제 청구액은 platform.openai.com
대시보드가 기준이고, Claude CLI 줄은 현금이 아니라 '구독 한도' 소비다.
이 스크립트는 그 소비가 '어디서' 나왔는지를 쪼개 보여주는 용도다.

reasoning 을 따로 보는 이유: 추론 모델(gpt-5-mini)은 reasoning 토큰이 출력
요금에 포함된다. out 중 reasoning 비중이 크면 줄일 자리는 '프롬프트'가
아니라 '추론 깊이/작업 난이도'다.

cache 를 따로 보는 이유: Claude CLI는 호출당 고정 오버헤드(~23k)가 거의
전부 cache_read 로 잡히고, 한도 가중치가 다르다(cache_read ~0.1x,
cache_write ~1.25x). in+out 만 보면 오버헤드가 실제보다 10배 커 보인다.

사용법:
    python scripts/usage_report.py                # logs/app.log
    python scripts/usage_report.py path/to.log
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

LINE = re.compile(
    r"\[usage\]\s+model=(?P<model>\S+)\s+in=(?P<in>\d+)\s+out=(?P<out>\d+)"
    r"(?:\s+\(reasoning=(?P<reasoning>\d+)\))?"
    r"(?:\s+\(cache_read=(?P<cache_read>\d+)\s+cache_write=(?P<cache_write>\d+)\))?"
)

_KEYS = ("in", "out", "reasoning", "cache_read", "cache_write")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/app.log")
    if not path.exists():
        print(f"로그 파일이 없다: {path}")
        return 1

    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, **{k: 0 for k in _KEYS}}
    )
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            s = stats[m.group("model")]
            s["calls"] += 1
            for k in _KEYS:
                s[k] += int(m.group(k) or 0)

    if not stats:
        print("[usage] 줄이 없다.")
        print("계측은 openai_processor._call_api / claude_code_processor._run_claude 에 있다"
              " — 로그 경로를 확인할 것.")
        return 1

    print(f"로그: {path}")
    print()
    hdr = (
        f"{'모델':22} {'호출':>6} {'입력tok':>12} {'출력tok':>11} "
        f"{'추론tok':>11} {'캐시읽기':>12} {'캐시쓰기':>11}"
    )
    print(hdr)
    print("-" * len(hdr))
    tot = {"calls": 0, **{k: 0 for k in _KEYS}}
    for model, s in sorted(stats.items(), key=lambda kv: -kv[1]["in"]):
        print(
            f"{model:22} {s['calls']:6,} {s['in']:12,} {s['out']:11,} "
            f"{s['reasoning']:11,} {s['cache_read']:12,} {s['cache_write']:11,}"
        )
        for k in tot:
            tot[k] += s[k]
    print("-" * len(hdr))
    print(
        f"{'합계':22} {tot['calls']:6,} {tot['in']:12,} {tot['out']:11,} "
        f"{tot['reasoning']:11,} {tot['cache_read']:12,} {tot['cache_write']:11,}"
    )

    if tot["calls"]:
        print()
        print(f"호출당 평균: 입력 {tot['in']//tot['calls']:,}tok / 출력 {tot['out']//tot['calls']:,}tok")
        if tot["out"]:
            print(f"추론 비중(출력 대비): {100 * tot['reasoning'] / tot['out']:.1f}%")

    print()
    print("해석 가이드")
    print("  · gpt-* 줄은 현금(종량 과금), claude-* 줄은 구독 한도 소비다.")
    print("  · claude 한도 등가는 대략 in + out + 0.1×캐시읽기 + 1.25×캐시쓰기.")
    print("    캐시읽기가 커 보여도 실부담은 1/10 — in/out이 큰 단계부터 줄일 것.")
    print("  · 입력이 출력을 압도하면 → 줄일 자리는 '보내는 양'이다")
    print("    (batch_size·content_text 캡·processing_interval·프리필터).")
    print("  · out 중 추론 비중이 크면 → 프롬프트를 줄여도 안 준다.")
    print("    추론을 끄거나(lean) 비추론 모델로 바꾸는 쪽이 지렛대다.")
    print("  · OpenAI 실제 청구액은 platform.openai.com 대시보드가 기준이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
