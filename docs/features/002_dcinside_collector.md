# Feature 002: DCInside 수집기

## 개요
DCInside 특이점이온다 갤러리(마이너 갤러리)에서 게시물을 HTTP 스크래핑으로 수집한다.
로그인 불필요, 모바일 페이지 파싱 방식.

## 구현 파일
- `src/infrastructure/collectors/base.py` — 수집기 공통 베이스 클래스
- `src/infrastructure/collectors/dcinside_collector.py` — DCInside 수집기

## 수집 대상
- 갤러리: `thesingularity` (특이점이온다 마이너 갤러리)
- URL: `https://m.dcinside.com/board/thesingularity`

## 동작 방식

### 수집 흐름
```
1. 모바일 목록 페이지 순회 (1~3페이지)
2. 각 페이지에서 게시물 목록 파싱 (제목, 작성자, 댓글수)
3. 각 게시물 상세 페이지 요청 → 본문 텍스트/HTML/이미지 추출
4. Post 도메인 엔티티로 변환하여 반환
```

### 파싱 전략
- **모바일 페이지 우선**: `m.dcinside.com`은 데스크톱보다 HTML 구조가 단순
- **본문 셀렉터**: `.thum-txtin` > `.writing_view_box` > `#viewContent` (폴백 체인)
- **이미지**: `<img>` 태그에서 `src` 또는 `data-src` 추출

### 안티 차단 전략
- User-Agent 로테이션 (모바일 UA 3종)
- 목록 페이지 간 1.5~3.0초 랜덤 딜레이
- 상세 페이지 요청 간 0.8~1.5초 랜덤 딜레이
- `Referer` 헤더 설정

### 중복 방지
- `external_id`: `dc_{gallery_id}_{post_no}` 형식
- 수집 사이클 내 `seen_ids` 세트로 중복 방지
- DB 저장 시 `external_id` UNIQUE 제약으로 최종 중복 방지

## 설정 (config/settings.yaml)
```yaml
collection:
  dcinside:
    enabled: true
    interval_minutes: 30
    gallery_id: "thesingularity"
    gallery_type: "mgallery"
    pages_to_scrape: 3        # 수집할 페이지 수
    request_delay_min: 1.5    # 요청 간 최소 대기(초)
    request_delay_max: 3.0    # 요청 간 최대 대기(초)
```

## 출력 (Post 엔티티)
```python
Post(
    source="dcinside",
    external_id="dc_thesingularity_12345",
    url="https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=12345",
    author="갤럭시노트",
    content_text="제목\n\n본문 텍스트...",
    content_html="<div>...</div>",
    media_urls=["https://dcimg..."],
    engagement_comments=15,
    collected_at=datetime(2026, 2, 19, ...),
)
```

## 제한사항
- 광고/공지 글 자동 필터링
- DCInside 구조 변경 시 셀렉터 업데이트 필요
- 과도한 요청 시 IP 차단 가능 → 딜레이 준수 필수
