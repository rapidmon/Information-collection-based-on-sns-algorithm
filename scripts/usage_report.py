"""로그에 쌓인 [usage] 줄을 집계해 OpenAI 토큰 사용량을 모델별로 보고한다.

openai_processor._call_api 가 호출마다 남기는
    [usage] model=... in=... out=... (reasoning=...)
줄을 합산한다. 계측 자체는 이미 있고, 없던 것은 '합산'이라 이 스크립트만 추가했다.

단가는 하드코딩하지 않는다 — 실제 청구액은 platform.openai.com 대시보드가
기준이고, 이 스크립트는 그 금액이 '어디서' 나왔는지를 쪼개 보여주는 용도다.

reasoning 을 따로 보는 이유: 현재 필터/분류가 추론 모델(gpt-5-mini)이라
reasoning 토큰이 출력 요금에 포함된다. out 중 reasoning 비중이 크면
줄일 자리는 '프롬프트'가 아니라 '추론 깊이/작업 난이도'다.

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
)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/app.log")
    if not path.exists():
        print(f"로그 파일이 없다: {path}")
        return 1

    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "in": 0, "out": 0, "reasoning": 0}
    )
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            s = stats[m.group("model")]
            s["calls"] += 1
            s["in"] += int(m.group("in"))
            s["out"] += int(m.group("out"))
            s["reasoning"] += int(m.group("reasoning") or 0)

    if not stats:
        print("[usage] 줄이 없다.")
        print("계측은 openai_processor._call_api 안에 있다 — 로그 경로를 확인할 것.")
        return 1

    print(f"로그: {path}")
    print()
    hdr = f"{'모델':18} {'호출':>7} {'입력tok':>13} {'출력tok':>12} {'추론tok':>12} {'추론비중':>8}"
    print(hdr)
    print("-" * len(hdr))
    tot = {"calls": 0, "in": 0, "out": 0, "reasoning": 0}
    for model, s in sorted(stats.items(), key=lambda kv: -kv[1]["in"]):
        share = 100 * s["reasoning"] / s["out"] if s["out"] else 0
        print(
            f"{model:18} {s['calls']:7,} {s['in']:13,} {s['out']:12,} "
            f"{s['reasoning']:12,} {share:7.1f}%"
        )
        for k in tot:
            tot[k] += s[k]
    print("-" * len(hdr))
    share = 100 * tot["reasoning"] / tot["out"] if tot["out"] else 0
    print(
        f"{'합계':18} {tot['calls']:7,} {tot['in']:13,} {tot['out']:12,} "
        f"{tot['reasoning']:12,} {share:7.1f}%"
    )

    if tot["calls"]:
        print()
        print(f"호출당 평균: 입력 {tot['in']//tot['calls']:,}tok / 출력 {tot['out']//tot['calls']:,}tok")
        print(f"입력:출력 비 = {tot['in'] / max(tot['out'], 1):.1f} : 1")

    print()
    print("해석 가이드")
    print("  · 입력이 출력을 압도하면 → 줄일 자리는 '보내는 양'이다")
    print("    (batch_size·content_text 캡·processing_interval).")
    print("  · out 중 추론 비중이 크면 → 프롬프트를 줄여도 안 준다.")
    print("    추론 모델을 비추론 모델로 바꾸는 쪽이 지렛대다.")
    print("  · 실제 청구액은 platform.openai.com 대시보드가 기준이다.")
    print("  · Claude CLI 경로(작문·큐레이션)는 여기 안 잡힌다 — 구독 한도 쪽이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
