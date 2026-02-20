# 008. Firebase Firestore DB 전환

## 개요

기존 SQLite + SQLAlchemy 기반 데이터베이스를 **Firebase Firestore**로 전환.
클린 아키텍처 덕분에 도메인/애플리케이션 레이어 변경 없이 인프라스트럭처 레이어의 Repository 구현체만 교체.

## 변경 사유

- SQLite는 로컬 파일 DB로 설정 불필요하지만, 사용자가 Firebase에 더 익숙
- Firestore는 무료 티어(Spark 플랜)로 소규모 프로젝트에 적합
- NoSQL 문서형 DB로 스키마 변경이 자유로움

## Firestore 컬렉션 구조

```
posts/
  └── {external_id}          # 문서 ID = external_id (중복 방지)
      ├── source: string
      ├── url: string
      ├── author: string
      ├── content_text: string
      ├── engagement_*: number
      ├── published_at: timestamp
      ├── collected_at: timestamp
      ├── summary: string (AI 생성)
      ├── importance_score: number
      ├── is_relevant: boolean
      ├── category_names: array<string>
      ├── **[NEW]** briefed_at: timestamp (브리핑 포함 시 마킹)
      ├── content_hash: string
      ├── dedup_cluster_id: number
      └── raw_data: map

briefings/
  └── {auto_id}
      ├── title: string
      ├── briefing_type: string
      ├── generated_at: timestamp
      ├── period_start/end: timestamp
      ├── content_html/text: string
      ├── email_sent: boolean
      └── items: array<map>     # BriefingItem 임베딩
          └── { headline, body, category_name, importance_score, ... }

categories/
  └── {name}                  # 문서 ID = 카테고리 name
      ├── name: string
      ├── name_ko: string
      └── color: string

collection_runs/
  └── {auto_id}
      ├── source: string
      ├── started_at: timestamp
      ├── completed_at: timestamp
      ├── status: string
      ├── posts_collected: number
      └── error_message: string
```

## 변경된 파일

### 새로 생성
- `src/infrastructure/database/firebase_client.py` — Firebase 앱 초기화 + Firestore 클라이언트

### 재작성 (SQLAlchemy → Firestore)
- `src/infrastructure/database/repositories/post_repo.py` — `FirestorePostRepository`
- `src/infrastructure/database/repositories/briefing_repo.py` — `FirestoreBriefingRepository`
- `src/infrastructure/database/repositories/category_repo.py` — `FirestoreCategoryRepository`
- `src/infrastructure/database/repositories/collection_run_repo.py` — `FirestoreCollectionRunRepository`

### 수정
- `src/infrastructure/config/settings.py` — `DATABASE_URL` 제거, `FIREBASE_CREDENTIAL_PATH`/`FIREBASE_PROJECT_ID` 추가
- `src/infrastructure/config/container.py` — Firestore DB 주입으로 변경
- `main.py` — `init_db()` → `init_firebase()` + `get_firestore_client()`
- `requirements.txt` — `sqlalchemy`, `aiosqlite`, `alembic` 제거 → `firebase-admin` 추가
- `.env.example` — Firebase 환경변수 추가
- `config/settings.yaml` — `database` 섹션 제거

### 삭제
- `src/infrastructure/database/models.py` — SQLAlchemy ORM 모델 (더 이상 불필요)
- `src/infrastructure/database/session.py` — SQLAlchemy 비동기 세션 (더 이상 불필요)
- `src/infrastructure/database/mapper.py` — 도메인↔ORM 매퍼 (더 이상 불필요)

## 핵심 구현 패턴

### 비동기 래핑
firebase-admin SDK는 동기 전용이므로 `asyncio.to_thread()`로 모든 Firestore 호출을 비동기 래핑:

```python
async def save(self, post: Post) -> Post:
    def _save():
        doc_ref = self._col().document(post.external_id)
        doc_ref.set(_post_to_dict(post))
        post.id = doc_ref.id
        return post
    return await asyncio.to_thread(_save)
```

### 배치 쓰기
Firestore 배치는 최대 500건이므로, 400건 단위로 분할하여 안전하게 배치 쓰기:

```python
async def save_many(self, posts: list[Post]) -> list[Post]:
    def _save_batch():
        batch = self._db.batch()
        for i, post in enumerate(posts):
            if i > 0 and i % 400 == 0:
                batch.commit()
                batch = self._db.batch()
            doc_ref = self._col().document(post.external_id)
            batch.set(doc_ref, _post_to_dict(post))
        batch.commit()
    await asyncio.to_thread(_save_batch)
```

### 중복 방지
- **posts**: `external_id`를 문서 ID로 사용 → 동일 게시물은 자동으로 덮어쓰기
- **categories**: `name`을 문서 ID로 사용

## Firebase 설정 방법

1. [Firebase Console](https://console.firebase.google.com/)에서 프로젝트 생성
2. Firestore Database 생성 (테스트 모드 또는 프로덕션 모드)
3. 프로젝트 설정 → 서비스 계정 → 새 비공개 키 생성 → JSON 다운로드
4. 다운로드한 JSON을 프로젝트 루트에 `firebase-service-account.json`으로 저장
5. `.env` 파일 설정:
   ```
   FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
   FIREBASE_PROJECT_ID=your-project-id
   ```

## 필요한 Firestore 인덱스

복합 쿼리에 필요한 인덱스 (Firestore가 자동으로 생성을 제안함):

| 컬렉션 | 필드 | 순서 |
|--------|------|------|
| `collection_runs` | `source` ASC, `status` ASC, `completed_at` DESC | 마지막 성공 조회 |
| `collection_runs` | `source` ASC, `started_at` DESC | 연속 실패 횟수 조회 |
| `posts` | `is_relevant` ASC, `collected_at` DESC | 기간별 관련 게시물 조회 |

> 첫 실행 시 Firestore 콘솔에서 인덱스 생성 링크가 로그에 출력됩니다.

## **[NEW]** 추가된 Repository 메서드

Firebase 전환 이후 추가된 메서드:
- `PostRepository.delete(post_id)` — 단건 삭제
- `PostRepository.delete_many(post_ids)` — 일괄 삭제 (관련 없는 게시물 제거)
- `PostRepository.get_unbriefed(limit)` — 미브리핑 게시물 조회 (`is_relevant==True`, `briefed_at==None`)
- `PostRepository.mark_briefed(post_ids, briefed_at)` — 브리핑 완료 마킹

## **[NEW]** 변경된 레이어 (추가 변경)

Firebase 전환 이후 추가로 변경된 레이어:
- `src/domain/entities/post.py` — `briefed_at` 필드 추가
- `src/application/use_cases/process_posts.py` — 관련 없는 게시물 삭제 로직
- `src/application/use_cases/generate_briefing.py` — `get_unbriefed()` + `mark_briefed()` 사용

## 변경되지 않은 레이어

클린 아키텍처의 이점으로 다음 레이어는 **변경 없음**:
- `src/presentation/` — 웹 대시보드 (라우트, 템플릿)
- `src/infrastructure/collectors/` — 수집기
- `src/infrastructure/delivery/` — 브리핑 빌더, 이메일
