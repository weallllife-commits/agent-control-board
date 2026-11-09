# agent-control-board

로컬 PC(C:\Users\manid)의 프로젝트·에이전트·Orca 세션을 한 장으로 보여주는 관제판. GitHub Pages로 공개한다.

## 배치 규칙

| 항목 | 규칙 |
|---|---|
| 실행 시각 | 매일 08:00 (Windows 작업 스케줄러 `AgentControlBoard-Daily`) |
| 놓친 실행 | PC가 꺼져 있었으면 다음 로그인·부팅 직후 1회 실행 (StartWhenAvailable) |
| 실행 내용 | `python run.py` → 수집 → `data.json`·`docs/index.html` 생성 → 변경 있을 때만 commit·push |
| Orca가 꺼져 있을 때 | 세션 정보는 직전 값을 쓰고 화면 상단에 "이전 값" 표시 |
| 실패 처리 | `logs/run.log`에 기록, 종료 코드 1. 다음 날 자동 재시도 |
| 수동 갱신 | `python run.py` (배포 없이 확인만: `python run.py --no-push`) |
| 커밋 메시지 | `chore: daily snapshot YYYY-MM-DD HH:MM` |

## 상태 기준

- 활성: 폴더 안 파일 마지막 수정 7일 이내 / 최근: 90일 이내 / 휴면: 그 이전. 휴면도 삭제하지 않고 계속 표시한다.
- Claude 동시 세션이 `claude_session_limit`(기본 2)을 넘으면 경보.

## 설정 (`projects.json`)

- `projects`: 이름, 소속 조직(`org`), 실행 명령, 에이전트 체인. 새 폴더는 자동 발견되어 "미분류"로 표시되니 여기서 조직과 체인을 채운다.
- `orgs`: 표시 순서 (멋쟁이사자처럼 → 위올라잇마이펫 → 위올라이프 → 공통·개인 → 기타).
- `pool`: 전역 에이전트(~/.claude/agents) 팀 묶음.
- `stray_patterns` / `keep_repos`: Orca에 잘못 등록된 저장소 판정 규칙.
