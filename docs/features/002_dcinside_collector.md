# Feature 002: DCInside 수집기

## 개요
DCInside 특이점이온다 갤러리(마이너 갤러리)에서 게시물을 수집한다.

**[NEW]** HTTP 스크래핑 → CDP (Playwright) 기반으로 변경. 사용자 Chrome에 CDP로 연결하여 데스크톱 페이지를 파싱.

## 구현 파일
- `src/infrastructure/collectors/base.py` — 수집기 공통 베이스 클래스
- `src/infrastructure/collectors/dcinside_collector.py` — DCInside 수집기

## 수집 대상
- 갤러리: `thesingularity` (특이점이온다 마이너 갤러리)
- **[NEW]** URL: 데스크톱 개념글 탭 (`https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&sort_type=N&search_head=27&page=1`)

## 동작 방식

### 수집 흐름
**[NEW]** HTTP → CDP + BeautifulSoup 하이브리드로 변경:
```
1. CDP로 사용자 Chrome에 연결
2. 이미 열려있는 개념글 탭을 찾음
3. 탭의 HTML을 가져와 BeautifulSoup으로 데스크톱 목록 파싱
4. 최대 20건 게시물 추출 (공지/설문/AD/뉴스 제외)
5. 각 게시물 상세 페이지로 이동하여 본문 추출
6. 상세 페이지 확인 후 개념글 페이지로 복귀
7. Post 도메인 엔티티로 변환하여 반환
```

### 파싱 전략
**[NEW]** 모바일 페이지 → 데스크톱 페이지로 변경:
- **데스크톱 목록 셀렉터**: `tr.ub-content` (게시물 행)
- **제목**: `td.gall_tit a` (링크 텍스트)
- **작성자**: `td.gall_writer` (`data-nick` 속성)
- **댓글 수**: `.reply_numbox` 내 텍스트
- **게시물 번호**: `tr[data-no]` 속성
- **상세 본문**: `.write_div` > `#viewContent` (폴백 체인)
- **이미지**: `<img>` 태그에서 `src` 또는 `data-src` 추출

### 안티 차단 전략
**[NEW]** User-Agent 로테이션 불필요 (사용자 Chrome 그대로 사용):
- 상세 페이지 방문 간 1.5~3.0초 랜덤 딜레이
- CDP로 실제 사용자 브라우저를 사용하므로 차단 리스크 최소

### 중복 방지
- `external_id`: `dc_{gallery_id}_{post_no}` 형식
- 수집 사이클 내 `seen_ids` 세트로 중복 방지
- DB 저장 시 `external_id`를 Firestore 문서 ID로 사용하여 중복 방지

## 설정 (config/settings.yaml)
**[NEW]** 수집 주기 및 방식 변경:
```yaml
collection:
  dcinside:
    enabled: true
    interval_minutes: 180      # [NEW] 30 → 180분
    gallery_id: "thesingularity"
    gallery_type: "mgallery"
    pages_to_scrape: 3
    request_delay_min: 1.5
    request_delay_max: 3.0
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
- 광고/공지/설문/뉴스 글 자동 필터링
- **[NEW]** 1페이지에서 최대 20건만 수집 (과도한 수집 방지)
- **[NEW]** 상세 페이지 확인 후 반드시 개념글 페이지로 복귀
- DCInside 구조 변경 시 셀렉터 업데이트 필요
