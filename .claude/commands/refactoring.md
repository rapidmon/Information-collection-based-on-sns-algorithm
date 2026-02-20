# Refactoring Command

Refactor the codebase by following these 3 phases strictly in order.
**All output and explanations MUST be written in Korean (한국어).**

---

## Phase 1: Diagnosis (DO NOT modify any code)

Analyze the code and report the following. **Do not touch or edit any code in this phase.**

- Dead code: functions, variables, imports that are never called or used
- Duplicate logic: functions/methods with 70%+ overlapping logic
- SRP violations: functions doing 3+ distinct responsibilities
- Over-abstraction: unnecessary wrappers, middle layers, or deep inheritance
- Single-use abstractions: classes/interfaces used in only one place
- Bloated files: any file exceeding 200 lines

---

## Phase 2: Refactoring (follow all rules below)

Based on the diagnosis, refactor the code. You MUST follow every rule listed here.

### Hard Constraints (NEVER violate these)
- Do NOT add any new features or behavior
- Do NOT introduce new libraries or packages
- Do NOT introduce new design patterns
- Do NOT add abstraction layers that didn't exist before
- The total line count MUST decrease after refactoring

### Rule 1: Delete First
- Remove all dead code immediately
- Remove all commented-out code
- Remove all unused imports and dependencies

### Rule 2: Simplify First
- Single-use class → convert to plain function
- Single-use utility function → inline it
- Unnecessary wrappers/abstraction layers → remove them (YAGNI)
- Deep inheritance → prefer composition or plain functions

### Rule 3: Deduplicate (DRY)
- Merge similar functions into one with parameters for branching
- Extract repeated code patterns into shared utility functions

### Rule 4: Structure
- Keep each file under 200 lines; split by responsibility if exceeded
- One function = one responsibility (SRP)
- Use clear naming: function name alone should explain its purpose

---

## Phase 3: Change Report

After refactoring, provide a comparison report in Korean using the following format:

```
### 리팩토링 결과

| 항목 | Before | After | 변화 |
|------|--------|-------|------|
| 총 줄 수 | | | |
| 파일 수 | | | |
| 함수/메서드 수 | | | |

### 삭제된 항목
- (삭제된 함수, 클래스, 파일 목록과 사유)

### 통합된 항목
- (예: 기존 A + B + C → 새로운 D로 통합)

### 기능 변경 여부
- (변경된 부분 명시. 없으면 "없음")

### 주요 개선 사항
- (핵심 개선 포인트 요약)
```