# Feature 003: 브라우저 매니저 + X(Twitter) 수집기

## 개요
Playwright 기반 X(Twitter) 알고리즘 피드 수집기.

**[NEW]** BrowserManager(세션 관리자) → CDP 연결 방식으로 변경. 사용자의 Chrome 브라우저에 `--remote-debugging-port=9222`로 직접 연결.

## 구현 파일
- **[NEW]** `browser_manager.py` 제거됨 — CDP 연결로 대체 (각 수집기가 직접 CDP 연결)
- `src/infrastructure/collectors/twitter_collector.py` — X 수집기

## 브라우저 매니저 → CDP 연결

**[NEW]** 기존 BrowserManager 대신 CDP(Chrome DevTools Protocol) 방식:

### 핵심 변경사항
- **세션 지속 불필요**: 사용자가 이미 로그인한 Chrome 브라우저를 그대로 사용
- **안티봇 우회 불필요**: 실제 사용자 브라우저이므로 playwright-stealth 불필요
- **수동 로그인 불필요**: Chrome에서 미리 로그인해두면 됨
- **멀티 플랫폼**: 각 플랫폼 탭이 Chrome에 이미 열려있는 상태에서 수집

### CDP 연결 방식
```
1. Chrome을 디버그 모드로 실행: chrome.exe --remote-debugging-port=9222
2. Twitter, Threads, LinkedIn에 수동 로그인
3. Playwright가 CDP로 연결: browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
4. 기존 탭 찾기 또는 새 탭 생성하여 수집
```

### 최초 설정 (1회)
```
1. Chrome을 --remote-debugging-port=9222 로 실행
2. Twitter, Threads, LinkedIn에 직접 로그인
3. DCInside 특이점이온다 갤러리 개념글 탭도 열어둠
4. 이후 py -3.12 main.py serve 실행
```

## X(Twitter) 수집기

### 수집 방식: GraphQL 인터셉트 (기본)

Twitter는 타임라인을 GraphQL API로 로드. 네트워크 응답을 인터셉트하면
구조화된 JSON을 직접 획득 가능.

```
1. CDP로 사용자 Chrome에 연결
2. 기존 Twitter 탭 찾기 또는 새 탭 생성
3. page.on("response") 핸들러 등록
4. x.com/home 접속
5. "HomeTimeline" 포함 URL의 응답을 캡처
6. 스크롤하여 추가 데이터 로드
7. 캡처된 JSON에서 트윗 데이터 추출
```

### GraphQL 응답 구조
```
data → home → home_timeline_urt → instructions → entries[]
  → content → itemContent → tweet_results → result
    → core → user_results (사용자 정보)
    → legacy (텍스트, 인게이지먼트, 미디어)
    → views (조회수)
```

### 추출 데이터
| 필드 | 소스 |
|------|------|
| tweet_id | `legacy.id_str` |
| text | `legacy.full_text` |
| author | `user_legacy.name` / `screen_name` |
| likes | `legacy.favorite_count` |
| retweets | `legacy.retweet_count` |
| replies | `legacy.reply_count` |
| views | `result.views.count` |
| media | `extended_entities.media[].media_url_https` |
| time | `legacy.created_at` |

### DOM 파싱 폴백
GraphQL 인터셉트 실패 시 DOM 파싱으로 폴백:
- `article[data-testid="tweet"]` — 트윗 컨테이너
- `[data-testid="tweetText"]` — 텍스트
- `[data-testid="User-Name"]` — 작성자
- `a[href*="/status/"]` — 트윗 링크

### 세션 만료 감지
페이지 로드 후 URL에 `login` 또는 `flow`가 포함되면 `SessionExpiredError` 발생.

## 설정
**[NEW]** 수집 주기 변경:
```yaml
collection:
  twitter:
    enabled: true
    interval_minutes: 10         # [NEW] 30 → 10분
    scroll_rounds: 8
    scroll_delay_min: 2.0
    scroll_delay_max: 4.0
    use_graphql_interception: true  # false 시 DOM 폴백
```
