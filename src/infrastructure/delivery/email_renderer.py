"""Morning Commit 이메일 HTML 렌더러.

브리핑(병합 항목) + 큐레이션(독자층별) + 로고 → 발송용 HTML.
미리보기 빌더와 실제 발송 파이프라인이 공유한다.
"""

from __future__ import annotations

from src.domain.entities import Briefing
from src.domain.services.ai_processor import Curation, normalize_topic_bullets
from src.infrastructure.delivery.categories import CATEGORY_EMOJI, CATEGORY_KO, VALID_BRIEFING_CATEGORIES

CAT_ORDER = list(VALID_BRIEFING_CATEGORIES)
# 이모지 + 한국어 라벨 (단일 소스에서 조합)
CAT_LABEL = {
    key: f"{CATEGORY_EMOJI.get(key, '')} {ko}".strip()
    for key, ko in CATEGORY_KO.items()
}


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _imp_pill(score: float) -> str:
    s = score or 0
    if s >= 0.9:
        return ('<span style="display:inline-block;background:#fef3f2;color:#d92d20;'
                'font-size:11px;font-weight:700;padding:3px 11px;border-radius:999px;margin-bottom:11px;">핵심</span>')
    if s >= 0.8:
        return ('<span style="display:inline-block;background:#eff8ff;color:#1570ef;'
                'font-size:11px;font-weight:700;padding:3px 11px;border-radius:999px;margin-bottom:11px;">주목</span>')
    return ""


def render_email_html(briefing: Briefing, curation: Curation, logo_src: str,
                      date_str: str | None = None) -> str:
    """발송용 HTML 생성. logo_src는 'cid:logo' 또는 'data:image/png;base64,...'."""
    if date_str is None:
        d = briefing.period_end or briefing.generated_at
        date_str = d.strftime("%Y. %m. %d") if hasattr(d, "strftime") else str(d)[:10]

    by_cat: dict[str, list] = {}
    for it in briefing.items:
        if it.category_name not in VALID_BRIEFING_CATEGORIES:
            continue
        by_cat.setdefault(it.category_name, []).append(it)
    for v in by_cat.values():
        v.sort(key=lambda x: x.importance_score or 0, reverse=True)

    P = []
    P.append(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;background:#e9ebef;font-family:-apple-system,'Malgun Gothic','Noto Sans KR',sans-serif;color:#16181d;">
<div style="max-width:680px;margin:0 auto;background:#f6f7f9;">
<div style="background:#fff;padding:30px 24px 18px;text-align:center;border-bottom:1px solid #edeef1;">
  <img src="{logo_src}" alt="Morning Commit" style="max-width:560px;width:80%;height:auto;">
  <div style="font-size:13px;color:#98a2b3;margin-top:14px;letter-spacing:0.02em;">{_esc(date_str)}</div>
</div>
<div style="padding:26px 24px;background:#f6f7f9;">""")

    # 전체 큐레이션 — 박스 없는 에디토리얼 리드
    if curation and (curation.title or curation.paragraphs):
        paras = "".join(
            f'<p style="font-size:14.5px;line-height:1.82;color:#3a4351;margin:0 0 14px;">{_esc(p)}</p>'
            for p in curation.paragraphs
        )
        P.append(f"""<div style="padding:0 2px 26px;margin-bottom:28px;border-bottom:1px solid #e4e7eb;">
  <div style="font-size:18.5px;font-weight:800;color:#16181d;margin-bottom:15px;line-height:1.4;letter-spacing:-0.01em;">{_esc(curation.title)}</div>
  {paras}
</div>""")

    cats = curation.categories if curation else {}
    for cat in CAT_ORDER:
        its = by_cat.get(cat, [])
        if not its:
            continue
        P.append('<div style="margin-bottom:34px;">')
        P.append(f'<div style="font-size:16px;font-weight:800;color:#16181d;margin-bottom:14px;">'
                 f'{CAT_LABEL.get(cat, cat)} <span style="color:#b0b7c3;font-weight:600;font-size:14px;">({len(its)}건)</span></div>')
        cc = cats.get(cat)
        if cc and (cc.hook or cc.bullets):
            bl = "".join(f'<div style="font-size:13px;color:#5a6473;line-height:1.65;">· {_esc(x)}</div>' for x in cc.bullets)
            ins = f'<div style="font-size:12.5px;color:#5a6473;margin-top:8px;">💡 {_esc(cc.insight)}</div>' if cc.insight else ""
            P.append(f"""<div style="background:#f3f7ff;border:1px solid #e0eaff;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
      <div style="font-size:14px;font-weight:700;color:#1d4ed8;margin-bottom:7px;">“{_esc(cc.hook)}”</div>{bl}{ins}</div>""")
        for it in its:
            # 구조화 불릿 우선, 없으면(구버전) body에서 폴백
            raw_lines = list(it.body_bullets) if it.body_bullets else [
                l.strip().lstrip("- ").strip() for l in (it.body or "").split("\n") if l.strip()
            ]
            lines = normalize_topic_bullets(raw_lines)
            bh = "".join(f'<div style="font-size:13.5px;line-height:1.72;color:#475467;margin:0 0 5px;padding-left:15px;text-indent:-9px;"><span style="color:#cdd3dc;">•</span> {_esc(l)}</div>' for l in lines)
            urls = it.source_urls or []
            chips = "".join(f'<a href="{u}" style="display:inline-block;background:#f1f5fb;color:#3b82f6;font-size:11px;font-weight:600;padding:4px 11px;border-radius:999px;text-decoration:none;margin:0 6px 6px 0;">원문 {i}</a>' for i, u in enumerate(urls, 1))
            P.append(f"""<div style="background:#ffffff;border:1px solid #eceef2;border-radius:16px;padding:20px 22px;margin-bottom:13px;box-shadow:0 1px 3px rgba(16,24,40,0.05);">
          {_imp_pill(it.importance_score)}
          <div style="font-size:16px;font-weight:700;color:#16181d;line-height:1.46;letter-spacing:-0.01em;margin-bottom:11px;">{_esc(it.headline)}</div>
          {bh}
          <div style="margin-top:15px;padding-top:13px;border-top:1px solid #f1f3f6;">
            <span style="font-size:11.5px;color:#98a2b3;">출처 {len(urls)}개</span>
            <div style="margin-top:9px;">{chips}</div>
          </div></div>""")
        P.append("</div>")

    if curation and curation.kick:
        kick = _esc(curation.kick).replace(". ", ".<br>", 1)
        P.append(f"""<div style="border-top:2px solid #e3e6ea;margin-top:6px;padding:30px 0 8px;text-align:center;">
  <div style="font-size:22px;font-weight:800;line-height:1.5;color:#16181d;letter-spacing:-0.01em;">{kick}</div></div>""")

    P.append("</div></div></body></html>")
    return "".join(P)
