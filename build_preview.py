"""Morning Commit 이메일 디자인 미리보기 빌더 (개발자 에디션).

실행: .venv/Scripts/python.exe build_preview.py  → morning_commit_preview.html 생성
06-30 실제 데이터(Firestore)를 우리가 합의한 디자인으로 조립한 디자인 목업.
"""
import base64, asyncio
from src.infrastructure.config.settings import Settings
from src.infrastructure.database.firebase_client import init_firebase, get_firestore_client
from src.infrastructure.database.repositories.briefing_repo import FirestoreBriefingRepository

with open("Logo.png", "rb") as f:
    logo_b64 = base64.b64encode(f.read()).decode()

s = Settings()
init_firebase(credential_path=s.firebase_credential_path, project_id=s.firebase_project_id or None)
repo = FirestoreBriefingRepository(get_firestore_client())
b = asyncio.run(repo.get_latest())
items = b.items
for it in items:
    if "Azure" in it.headline and "Claude" in it.headline:
        it.category_name = "AI"  # 경계 규칙: 모델 가용성은 AI

CAT_ORDER = ["AI", "Semiconductor", "Cloud", "BigTech", "Startup", "Regulation", "Coding"]
CAT_KO = {"AI": "🧠 AI", "Semiconductor": "🔬 반도체", "Cloud": "☁️ 클라우드·인프라",
          "BigTech": "🏢 빅테크", "Startup": "🚀 스타트업", "Regulation": "⚖️ 규제/정책", "Coding": "💻 코딩"}
B2 = {
 "AI": ("성능 자랑하더니, 못 풀게 막혔다",
   ["GPT-5.6 Sol, Terminal-Bench 91.9% 신기록 — 그런데 정부 단계 배포로만 공개",
    "1분기 글로벌 AI 매출 250억$로 인프라 감가상각 첫 초과(거품론 반전)",
    "Claude, Azure 공식 출시 / DeepSeek 추론 85%↑ 오픈 / Amazon·Anthropic 토큰 가격 재협상"],
   "경쟁의 축이 '성능'에서 접근권·수익성으로 이동."),
 "Semiconductor": ("국가도 시장도 반도체에 올인",
   ["청와대 10년 4,755조 베팅(삼성 2,655조·SK 2,100조)",
    "SK하이닉스 나스닥 ADR로 45.5조 조달·상반기 주가 +310%(삼성 +183%)",
    "Nvidia, 칩설계 자동화 AI 'HORIZON' 시험 100% 통과"],
   "공급 주도 호황 + 설계 자동화 → 밸류체인 장기 모멘텀."),
 "Cloud": ("AI는 결국 전력 싸움 — 데이터센터·전력 확보전",
   ["GS, 동해 발전소 옆 AI 데이터센터 조성", "LS ELECTRIC, 美 유타 전력설비 6배 증설(2,500억)"],
   "모델·칩만큼이나 'AI를 돌릴 전력·데이터센터'가 병목이자 기회."),
 "BigTech": ("AI가 바꾼 가격표", ["Apple, 메모리값 상승으로 Mac·iPad 최대 25% 인상"],
   "AI 인프라 비용이 소비자 가격으로 전이되기 시작."),
 "Startup": ("로켓 회사가 위성 회사를 삼켰다", ["Rocket Lab, Iridium 약 80억$ 인수 (가입자 255만·매출 8.7억$)"],
   "발사체+위성망 수직계열화 — 우주통신 재편 신호."),
 "Regulation": ("이번엔 미국 정부가 브레이크",
   ["AI 17년 백도어 자체 발견 → 미국 정부, 최신 모델 공개 제한 / 미국 백악관 GPT-5.6 속도조절 요청",
    "삼성·SK·마이크론 DRAM 담합(500~700%↑) 집단소송",
    "대만 당국 NVIDIA 우회수출 압수수색 / Anthropic, 알리바바 무단접근 미국 정부 고발"],
   "접근·수출 통제가 제품 로드맵의 실질 변수."),
 "Coding": ("이제 빌드도 AI 속도",
   ["Microsoft VS Code, TypeScript 7 통합 — 빌드 속도 향상",
    "유료 API 없이 쓰는 크롤링·브라우저 자동화 GitHub 오픈소스 10선"],
   "에이전트 워크플로우 + 비용/속도 최적화가 생산성 기준."),
}
TOP_TITLE = "🛠️ 코드 짜는 실력만으론 부족해졌다 — 지금 안 익히면 뒤처지는 것들"
TOP_P1 = ("이번 주 개발자 책상 위가 조용히 바뀌었습니다. GPT-5.6 코딩 에이전트(Sol)는 터미널 작업을 91.9% 해내고, "
 "VS Code엔 TypeScript 7이 통합돼 빌드가 빨라졌으며, Microsoft Copilot은 AGENTS.md까지 지원하기 시작했죠. "
 "에이전트가 '도구'에서 '같이 일하는 동료'로 넘어가는 흐름입니다. 오픈소스도 풍년이었습니다 — 유료 API 없이 "
 "크롤링·자동화 GitHub 10선, 추론 85%↑ 무료 공개 DeepSeek, MIT 임베딩 BGE-VL, 자연어→SQL DB-GPT, "
 "Vercel 에이전트 프레임워크 eve까지. 돈 안 들이고도 실무급 스택을 조립할 재료가 쏟아졌습니다.")
TOP_P2 = ("핵심은 경쟁력의 무게중심이 '코드를 짜는 능력'에서 '에이전트·오픈소스를 조합해 비용과 속도를 최적화하는 "
 "능력'으로 옮겨갔다는 점입니다. Amazon·Anthropic이 Claude 계약을 토큰 기반 가격으로 다시 짤 만큼 화두는 '비용'이고, "
 "추론 서빙 스택 llm-d가 처리량을 3배로 끌어올린 것처럼 인프라 효율을 이해하는 사람이 귀해집니다. 실전 조언 한 줄 — "
 "포트폴리오에 'AI 에이전트로 OO를 자동화하고 토큰 비용을 N% 줄였다'가 적히는 순간, 그게 곧 스펙이 되는 시대입니다.")
BOTTOM_KICK = "AI는 개발자를 대체하지 않는다.<br>'AI를 잘 쓰는 개발자'가 대체할 뿐이다."

def esc(t): return (t or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def imp_pill(sc):
    sc = sc or 0
    if sc >= 0.9: return '<span style="display:inline-block;background:#fef3f2;color:#d92d20;font-size:11px;font-weight:700;padding:3px 11px;border-radius:999px;margin-bottom:11px;">핵심</span>'
    if sc >= 0.8: return '<span style="display:inline-block;background:#eff8ff;color:#1570ef;font-size:11px;font-weight:700;padding:3px 11px;border-radius:999px;margin-bottom:11px;">주목</span>'
    return ''

by_cat = {}
for it in items: by_cat.setdefault(it.category_name or "AI", []).append(it)
for v in by_cat.values(): v.sort(key=lambda x: x.importance_score or 0, reverse=True)

P = []
P.append(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;background:#e9ebef;font-family:-apple-system,'Malgun Gothic','Noto Sans KR',sans-serif;color:#16181d;">
<div style="max-width:680px;margin:0 auto;background:#f6f7f9;">
<div style="background:#fff;padding:30px 24px 18px;text-align:center;border-bottom:1px solid #edeef1;">
  <img src="data:image/png;base64,{logo_b64}" alt="Morning Commit" style="max-width:560px;width:80%;height:auto;">
  <div style="font-size:13px;color:#98a2b3;margin-top:14px;letter-spacing:0.02em;">2026. 06. 30</div>
</div>
<div style="padding:26px 24px;background:#f6f7f9;">""")
# 전체 큐레이션 — 박스 없이 에디토리얼 리드 (얇은 하단 구분선만)
P.append(f"""<div style="padding:0 2px 26px;margin-bottom:28px;border-bottom:1px solid #e4e7eb;">
  <div style="font-size:18.5px;font-weight:800;color:#16181d;margin-bottom:15px;line-height:1.4;letter-spacing:-0.01em;">{esc(TOP_TITLE)}</div>
  <p style="font-size:14.5px;line-height:1.82;color:#3a4351;margin:0 0 14px;">{esc(TOP_P1)}</p>
  <p style="font-size:14.5px;line-height:1.82;color:#3a4351;margin:0;">{esc(TOP_P2)}</p>
</div>""")
for cat in CAT_ORDER:
    its = by_cat.get(cat, [])
    if not its: continue
    hook, bullets, insight = B2.get(cat, ("", [], ""))
    P.append('<div style="margin-bottom:34px;">')
    P.append(f'<div style="font-size:16px;font-weight:800;color:#16181d;margin-bottom:14px;">{CAT_KO.get(cat,cat)} <span style="color:#b0b7c3;font-weight:600;font-size:14px;">({len(its)}건)</span></div>')
    bl = "".join(f'<div style="font-size:13px;color:#5a6473;line-height:1.65;">· {esc(x)}</div>' for x in bullets)
    P.append(f"""<div style="background:#f3f7ff;border:1px solid #e0eaff;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
      <div style="font-size:14px;font-weight:700;color:#1d4ed8;margin-bottom:7px;">“{esc(hook)}”</div>{bl}
      <div style="font-size:12.5px;color:#5a6473;margin-top:8px;">💡 {esc(insight)}</div></div>""")
    for it in its:
        lines = [l.strip().lstrip("- ").strip() for l in (it.body or "").split("\n") if l.strip()]
        bh = "".join(f'<div style="font-size:13.5px;line-height:1.72;color:#475467;margin:0 0 5px;padding-left:15px;text-indent:-9px;"><span style="color:#cdd3dc;">•</span> {esc(l)}</div>' for l in lines)
        urls = it.source_urls or []
        chips = "".join(f'<a href="{u}" style="display:inline-block;background:#f1f5fb;color:#3b82f6;font-size:11px;font-weight:600;padding:4px 11px;border-radius:999px;text-decoration:none;margin:0 6px 6px 0;">원문 {i}</a>' for i,u in enumerate(urls,1))
        P.append(f"""<div style="background:#ffffff;border:1px solid #eceef2;border-radius:16px;padding:20px 22px;margin-bottom:13px;box-shadow:0 1px 3px rgba(16,24,40,0.05);">
          {imp_pill(it.importance_score)}
          <div style="font-size:16px;font-weight:700;color:#16181d;line-height:1.46;letter-spacing:-0.01em;margin-bottom:11px;">{esc(it.headline)}</div>
          {bh}
          <div style="margin-top:15px;padding-top:13px;border-top:1px solid #f1f3f6;">
            <span style="font-size:11.5px;color:#98a2b3;">출처 {len(urls)}개</span>
            <div style="margin-top:9px;">{chips}</div>
          </div></div>""")
    P.append("</div>")
P.append(f"""<div style="border-top:2px solid #e3e6ea;margin-top:6px;padding:30px 0 8px;text-align:center;">
  <div style="font-size:22px;font-weight:800;line-height:1.5;color:#16181d;letter-spacing:-0.01em;">{BOTTOM_KICK}</div></div>""")
P.append("</div></div></body></html>")
open("morning_commit_preview.html","w",encoding="utf-8").write("".join(P))
print("생성 완료: morning_commit_preview.html |", len(items), "항목")
