# Handoff Command

Summarize the current session so it can be resumed on another machine.
**All output MUST be written in Korean (한국어).**

Follow these steps in order:

---

## Step 1: Generate Session Summary

Analyze the entire conversation history and create a structured summary covering:

### 프로젝트 상태
- Current branch and last commit
- Which files were created, modified, or deleted in this session

### 완료된 작업
- List every task completed in this session with brief descriptions

### 진행 중인 작업
- Any unfinished tasks, including where you stopped and what remains
- Known bugs or issues discovered but not yet fixed

### 핵심 결정사항
- Important design decisions made during this session and their rationale
- Any trade-offs or constraints agreed upon

### 다음 단계
- Concrete next steps to continue from where we left off
- Priority order if multiple tasks remain

### 주의사항
- Gotchas, edge cases, or warnings for the next session
- Any environment-specific details (e.g., env vars, local config, dependencies)

---

## Step 2: Save to File

Save the summary to `.claude/context/session-<TODAY_DATE>.md` where `<TODAY_DATE>` is in `YYYY-MM-DD` format.

If the directory `.claude/context/` does not exist, create it.

If a file for today already exists, append a counter: `session-2026-02-21-2.md`.

---

## Step 3: Update CLAUDE.md

Check if `CLAUDE.md` exists in the project root.

- If it exists: update ONLY the `## Latest Session` section (create it if missing). Do NOT modify any other sections.
- If it does not exist: create it with the section below.

Write the following:

```markdown
## Latest Session
- Date: <TODAY_DATE>
- Summary: .claude/context/session-<TODAY_DATE>.md
- Status: <한 줄 요약 of current state>
```

---

## Step 4: Confirm

Print a short confirmation message in Korean:

```
✅ 세션 핸드오프 완료
📄 저장 위치: .claude/context/session-<DATE>.md
💡 다른 컴퓨터에서 git pull 후 Claude Code를 실행하면 자동으로 이어갈 수 있습니다.
```