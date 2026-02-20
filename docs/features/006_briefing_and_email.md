# Feature 006: 브리핑 생성 + 이메일 전송

## 개요
AI가 통합한 토픽(MergedTopic)을 브리핑 문서로 변환하고,
HTML 이메일로 전달하는 기능.

## 구현 파일
- `src/infrastructure/delivery/briefing_builder.py` — 브리핑 생성기 (BriefingGenerator 구현)
- `src/infrastructure/delivery/email_sender.py` — 이메일 발송기 (Notifier 구현)
- `src/application/use_cases/generate_briefing.py` — 브리핑 생성 유즈케이스
- `src/application/use_cases/send_briefing.py` — 브리핑 전달 유즈케이스

## 브리핑 생성기

### 입력 → 출력
```
MergedTopic[] → Briefing (items[], content_text, content_html)
```

### 생성 로직
1. 중요도 내림차순 정렬 → max_items 제한 (기본 20)
2. MergedTopic → BriefingItem 변환
3. **[NEW]** 카테고리별 그룹화 (고정 순서: AI → 반도체 → 클라우드 → 빅테크 → 스타트업 → 규제 → **코딩**)
4. 텍스트 + HTML 동시 렌더링

### **[NEW]** 미브리핑 게시물 추적 (briefed_at)
- Post 엔티티에 `briefed_at` 필드 추가
- 브리핑 생성 시 `get_unbriefed()` 로 미브리핑 게시물만 조회 (기존: 시간 범위 쿼리)
- 브리핑 생성 완료 후 포함된 게시물에 `mark_briefed()` 호출
- 동일 게시물이 여러 브리핑에 중복 포함되는 문제 해결

### 출력 형식

#### 텍스트
```
===== 2026-02-19 기술 모닝 브리핑 =====

[AI] (3건)

**ByteDance, 자체 AI 칩 'SeedChip' 개발 본격화**
- ByteDance가 AI 추론용 자체 칩 개발 중...
- 삼성전자와 위탁생산 협상 진행 중...
중요도: ★★★★★ | 출처: X, DCInside

===== 수집 통계 =====
분석 게시물: 247건 | 브리핑 항목: 12건
```

#### HTML
- 반응형 이메일 레이아웃 (max-width: 700px)
- 다크 헤더 (#1a1a2e)
- 카테고리별 섹션 구분
- 각 항목: 왼쪽 파란 보더 카드 스타일
- 중요도 별점 + 출처 메타 정보

### 중요도 별점 매핑
| 점수 | 표시 |
|------|------|
| 0.9+ | ★★★★★ |
| 0.7+ | ★★★★☆ |
| 0.5+ | ★★★☆☆ |
| 0.3+ | ★★☆☆☆ |
| < 0.3 | ★☆☆☆☆ |

## 이메일 발송기

### 기능
- SMTP (TLS) 비동기 전송
- HTML + 텍스트 멀티파트 이메일
- 브리핑 전송 + 시스템 알림 전송
- 활성화/비활성화 설정

### 설정
**[NEW]** 브리핑 시간 변경:
```yaml
briefing:
  daily_time: "09:00"          # [NEW] 06:30 → 09:00
  max_items: 20
  include_stats: true

email:
  enabled: true
  to_addresses:
    - "user@example.com"
```

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password  # Gmail App Password
EMAIL_FROM=your-email@gmail.com
```

### Gmail App Password 설정 방법
1. Google 계정 → 보안 → 2단계 인증 활성화
2. 앱 비밀번호 생성 (메일 / Windows 컴퓨터)
3. 생성된 16자리 코드를 SMTP_PASSWORD에 설정
