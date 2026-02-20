# Optimize Command

Analyze and optimize this codebase for performance. Follow these 3 phases strictly in order.
**All output and explanations MUST be written in Korean (한국어).**

---

## Phase 1: Performance Audit (DO NOT modify any code)

Analyze the code and identify performance bottlenecks. **Do not touch or edit any code in this phase.**

Report findings grouped by severity (심각 / 주의 / 경미):

- **Algorithmic**: O(n²) or worse where O(n) or O(n log n) is possible, unnecessary nested loops
- **Memory**: memory leaks, unbounded caches, large object copies where references suffice, unnecessary allocations in hot paths
- **I/O & Network**: N+1 queries, missing batching, synchronous calls that should be async, unoptimized database queries (missing indexes, full table scans)
- **Rendering** (if frontend): unnecessary re-renders, missing memoization, large bundle sizes, layout thrashing, unoptimized images/assets
- **Concurrency**: blocking main thread, missing parallelism opportunities, race conditions
- **Caching**: repeated expensive computations with same inputs, missing caching layers
- **Data structures**: wrong data structure for the access pattern (e.g., array where Set/Map is better)

---

## Phase 2: Optimization (follow all rules below)

Based on the audit, apply optimizations. You MUST follow every rule listed here.

### Hard Constraints
- Do NOT change any external behavior or API contracts
- Do NOT introduce new libraries unless the performance gain is critical and no built-in alternative exists
- Preserve all existing functionality and error handling
- Every optimization MUST have a clear justification from the audit

### Priority Order (fix in this order)
1. **Quick wins**: dead computations, redundant calls, obvious inefficiencies — fix these first
2. **Algorithmic fixes**: replace inefficient algorithms/data structures
3. **I/O optimization**: batch requests, add async where beneficial, optimize queries
4. **Caching**: add memoization or caching for expensive repeated computations
5. **Lazy loading**: defer expensive initialization, load on demand
6. **Frontend-specific** (if applicable): reduce re-renders, code split, optimize assets

### Rules
- Measure before optimizing: explain the expected impact of each change
- Prefer built-in language features over custom implementations
- Keep optimizations readable — add comments explaining non-obvious optimizations
- Do NOT pre-optimize things that are not in the audit findings

---

## Phase 3: Optimization Report

After optimization, provide a report in Korean using the following format:

```
### 최적화 결과

| # | 위치 | 문제점 | 적용한 수정 | 예상 효과 |
|---|------|--------|------------|----------|
| 1 | | | | |
| 2 | | | | |

### 카테고리별 변경사항
- **알고리즘**: (변경 목록)
- **I/O & 네트워크**: (변경 목록)
- **메모리**: (변경 목록)
- **캐싱**: (변경 목록)
- **기타**: (변경 목록)

### 트레이드오프
- (가독성, 메모리, 복잡도 등 발생한 트레이드오프)

### 미적용 항목
- (진단에서 발견했지만 의도적으로 수정하지 않은 항목과 사유)
```