# Session Log — 2026-03-26~27

## 개요

CTF 자동 풀이 하네스를 처음부터 설계+구현+테스트까지 진행한 세션.
이전 Mentor 기반 TS 설계를 전면 폐기하고, 6개 리서치 보고서 + 3개 레퍼런스 프로젝트 분석 → RALPLAN 합의 → Python asyncio 구현 → Dreamhack 문제로 실전 테스트 → **crypto 문제 flag 획득 성공**.

GitHub: https://github.com/0xAxii/oh-my-ctf

---

## Phase 1: 리서치 (6개 보고서)

### 리서치 #1: CTF+LLM 시스템 종합 서베이
- 모든 주요 CTF 자동화 시스템 테이블화
- 핵심 발견: 카테고리 특화 > 균일 에이전트, 하네스 > 모델
- verialabs 52/52 BSidesSF, Squid Agent 92% CTFTiny, CAI 99% 평균 백분위

### 리서치 #2: 에이전트 하네스 설계 패턴
- Anthropic/OpenAI/Google 엔지니어링 블로그 분석
- 10대 패턴: 카테고리 특화, fresh session > compact, writeup RAG, observation masking, 프로그래밍적 게이트 등
- CyberGym union 효과: 개별 10% → 합 18.4%
- Writeup RAG = 단일 최고 ROI (+85%/+120%)

### 리서치 #3: 분야별 CTF 자동화 deep dive
- PWN: PwnGPT 57.9%, GDB 스키마화, pwntools hang이 최다 실패
- REV: Ghidra 디컴파일 + z3/angr
- CRYPTO: SageMath/z3 필수, 도구 호출 안정성이 핵심
- WEB: MAPTA 76.9%, blind SQLi 0%
- 11분 임계값: 인간 11분+ 걸리는 문제 AI 솔브 0건

### 리서치 #4: 벤치마크+데이터셋
- InterCode-CTF 95% 포화, NYU CTF 22%, Cybench 46%
- CTFusion: 라이브 성능 = 정적의 ~50% (데이터 오염)

### 리서치 #5: 커뮤니티/실전 경험 (X/Reddit/HN)
- Red-Run: compaction이 정보 날림, 에이전트가 지시 무시
- $100/주말 CTF 비용
- 모든 상위 팀 수렴: coordinator + Docker sandbox + message bus

### 리서치 #6: GPT-5.4 조기 종료 심층 조사
- 원인 5가지: reasoning effort 기본값 none, 토큰 효율 훈련, Codex 튜닝, auto-compaction, phase 미처리
- GitHub #13950, #13799, #14228 등 다수 보고
- "keep going" = 최악의 전략 (퇴화 루프)
- 해결: fresh session + state injection, reasoning_effort=xhigh, phase 파라미터

---

## Phase 2: 레퍼런스 프로젝트 분석

### verialabs/ctf-agent (52/52 BSidesSF 2026)
- Coordinator + ChallengeSwarm + MessageBus + bump 패턴
- 5개 모델 병렬 레이싱 (Claude Opus, GPT-5.4, 5.4-mini, 5.3-codex)
- Docker sandbox per solver
- LoopDetector (12-window, 5-threshold)
- Flag 제출: dedup + escalating cooldown
- 상태 관리 최소 (findings summary + JSONL trace)

### Machine (sane100400)
- Claude Code 네이티브 서브에이전트
- 카테고리별 파이프라인: pwn→critic→verifier→reporter
- SQLite state.py + --src 환각 방지
- quality_gate.py (exit code 차단)
- decision_tree.py (실패 시 다음 전략)
- knowledge.py (FTS5 검색)

### Squid Agent (SPL, 92% CTFTiny)
- 카테고리별 완전 분리된 멀티에이전트 시스템
- per-category RAG
- GPT-5 (manager) + gpt-mini (서브에이전트)
- criticize_agent (crypto dual-path)

### CAI (Alias Robotics, 99% 평균 백분위)
- 300+ 모델 지원, OpenAI Agents SDK 포크
- generic_linux_command (올인원 도구)
- interactive session 관리 (nc/ssh)
- guardrails (프롬프트 인젝션 방어)
- flag_discriminator (handoff 패턴)

### D-CIPHER (NYU, 22% NYU CTF)
- Planner-Executor 멀티에이전트
- AutoPrompter: 환경 탐색 → 맞춤 프롬프트 생성 → Planner에 전달
- 우리 Recon의 직접적 근거

---

## Phase 3: 설계 (RALPLAN)

### 사용자 결정사항
- Python asyncio (CTF 생태계 네이티브)
- Codex App Server JSON-RPC (turn/completed로 조기 종료 감지)
- GPT-5.4 + GPT-5.2 병렬 레이싱
- Docker per solver + externalSandbox
- Fresh session only (NO compact)
- Manager: 대화형 (v1 터미널, v2 Discord)
- LightCritic: spark (폴백 5.4 low)
- 구독 모델 → 비용 무관

### 모델 배치 (최종)
| 역할 | 모델 | effort |
|---|---|---|
| Manager | GPT-5.4 | medium |
| Recon | GPT-5.4 | medium |
| LightCritic | GPT-5.3-codex-spark (폴백: 5.4 low) | low |
| Solver 1 | GPT-5.4 | medium → high → xhigh (escalation) |
| Solver 2 | GPT-5.2 | medium → high → xhigh (escalation) |

### Solver Effort Escalation
- bump 0~2: medium
- bump 3~5: high
- bump 6+: xhigh
- 상한 없음 (flag 찾을 때까지)

### RALPLAN 결과
- Planner → Architect → Critic 1라운드 APPROVE
- Architect 핵심 지적: "fresh session은 accumulated reasoning chain을 잃음" → v1 한계로 인정
- Critic: "structured findings schema를 v1에 당겨라" → 채택

### GPT Pro 피드백 (3라운드)
1차: autonomous/operator 모드 분리(보류), structured fact schema(이미 반영), ExecutionVerifier(Manager가 담당), 전략 다양성(Recon이 담당), observation masking(프롬프트 규칙)
2차: reasoning effort 역할별 분리(반영), spark fallback(반영), 문서 불일치 정리(반영)
3차: dynamicTools 패턴(구현), approval 응답 형식(수정), 5.2 은퇴일 노트(추가)

---

## Phase 4: 구현

### 프로젝트 구조
```
ctf-solver/
├── main.py                     # CLI (interactive + direct 모드)
├── core/
│   ├── app_server.py           # Codex App Server JSON-RPC + dynamicTools + tool executor
│   ├── solver.py               # AppServerSolver + docker exec 라우팅 + _exec_tool
│   ├── swarm.py                # ChallengeSwarm + BumpEngine + effort escalation + LightCritic
│   ├── recon.py                # Recon (5.4 medium) + categories/*.md 로딩 + 모델별 프롬프트 가이드
│   ├── light_critic.py         # spark 검증 + flag 판별 + fake flag 필터
│   ├── message_bus.py          # Cross-solver findings (append-only + cursor)
│   ├── loop_detector.py        # 반복 도구 호출 감지
│   ├── tracer.py               # JSONL 이벤트 트레이싱
│   └── solver_base.py          # Protocol + result types
├── manager/
│   ├── manager.py              # Manager (5.4 medium) 한국어 대화
│   └── terminal_io.py          # 터미널 stdin/stdout
├── categories/                 # 8개 카테고리 프롬프트 (pwn/rev/crypto/web/forensics/web3/misc/ai)
├── sandbox/
│   ├── Dockerfile.base + 8개   # 카테고리별 Docker 이미지
│   └── container.py            # 컨테이너 관리
└── tests/challenges/           # 테스트 챌린지
```

### 핵심 구현 상세

#### AppServerClient (`core/app_server.py`)
- `codex app-server` asyncio subprocess 스폰
- JSON-RPC over stdin/stdout, 10MB buffer limit
- `experimentalApi: true` for dynamicTools
- `SANDBOX_TOOLS`: bash, read_file, write_file, list_files
- `item/tool/call` 핸들링 → `tool_executor` 콜백으로 라우팅
- `requestApproval` → `"acceptForSession"` (문자열, 객체 아님!)
- `approvalPolicy: "never"`는 `turn/start`에 넣어야 함 (thread/start 아님 — 이게 핵심 버그였음)
- `sandboxPolicy: {type: "externalSandbox", networkAccess: "enabled"}`도 `turn/start`

#### Solver (`core/solver.py`)
- `__post_init__`에서 `AppServerClient(tool_executor=self._exec_tool)` 생성
- `_exec_tool`: container_id 있으면 docker exec, 없으면 호스트 실행
- `start_thread`에 `dynamic_tools=SANDBOX_TOOLS` 전달
- `_handle_event`: turn/completed, agentMessage/delta, commandExecution/outputDelta
- `_flag` 리셋 (bump 간 false flag 방지)
- `model_response` tracer 기록 (5.4가 텍스트만 출력해도 trace 생성)
- `findings_summary = _response_buf[:2000]` (turn 종료 시 캡처)

#### ChallengeSwarm (`core/swarm.py`)
- 두 solver asyncio.wait(FIRST_COMPLETED) 레이싱
- BumpEngine: turn/completed(no flag) → LightCritic verify → fresh session + verified findings
- Effort escalation: `_effort_for_bump(bump_count)` → medium/high/xhigh
- Exponential backoff: `min(15 * 2^bump, 300) + jitter`
- 상한 없음 (무제한 bump)
- LightCritic 통합: verify → flag queue → FLAG_FOUND
- MessageBus: findings 공유, bump 시 verified summary 주입

#### Recon (`core/recon.py`)
- GPT-5.4 medium, categories/*.md 로딩
- Solver A (5.4): completeness_contract + tool_persistence_rules + verification_loop
- Solver B (5.2): grounding_rules + output_verbosity_spec
- 파싱: FINDINGS / SOLVER_PROMPT_A / SOLVER_PROMPT_B 섹션
- 실패 시 최대 3회 재시도 (fallback 프롬프트 없음)
- anti-brute-force 규칙 포함

#### LightCritic (`core/light_critic.py`)
- GPT-5.3-codex-spark (폴백: 5.4 low)
- raw findings + JSONL trace → 교차 검증
- flag 판별: 진짜 → Manager에 보고, 가짜 → "fake flag" 기록 → 다음 세션에서 회피
- findings_verified.json 저장

#### Docker 이미지 (9개 전부 빌드 완료)
- ctf-base (2.72GB), ctf-pwn (5.34GB), ctf-rev (3.59GB), ctf-crypto (7.73GB)
- ctf-web (4.29GB), ctf-forensics (3.43GB), ctf-web3 (3.37GB), ctf-misc (3.23GB), ctf-ai (2.93GB)
- cado-nfs, mythril, torch 제외 (빌드 실패 → 필요 시 수동)

---

## Phase 5: 테스트 + 버그 수정

### 버그 #1: approvalPolicy 위치 (치명적)
- 증상: Recon이 10분+ 멈춤, solver가 도구 실행 안 함
- 원인: `approvalPolicy: "never"`를 `thread/start`에 넣었는데, 공식 문서상 `turn/start` 파라미터
- 수정: `approvalPolicy`와 `sandboxPolicy`를 `start_turn`으로 이동
- 효과: Recon 2분 만에 완료, solver 정상 작동

### 버그 #2: approval 응답 형식
- 증상: codex가 승인 기다리며 멈춤
- 원인: `result: {"decision": "acceptForSession"}` (객체) → 공식: `result: "acceptForSession"` (문자열)
- 수정: 문자열로 변경

### 버그 #3: readline 버퍼 초과
- 증상: `LimitOverrunError` — codex가 64KB 넘는 JSON 라인 전송
- 수정: `limit=10*1024*1024` (10MB)

### 버그 #4: 5.4 solver 0줄 trace
- 원인 1: `_flag` 리셋 안 됨 → false flag가 다음 turn에서 FLAG_FOUND
- 원인 2: `agentMessage/delta`에서 `tracer.model_response()` 미호출
- 수정: `self._flag = None` + tracer 기록 추가

### 버그 #5: findings_summary 빈 문자열
- 원인: `_response_buf`가 `findings_summary`로 안 옮겨짐
- 수정: turn 종료 시 `self.findings_summary = self._response_buf[:2000]`

### 버그 #6: effort 하드코딩
- 원인: swarm에서 solver effort가 "xhigh"로 고정
- 수정: "medium"으로 시작, escalation 적용

---

## Phase 6: 실전 테스트

### Fruit Market (web, Dreamhack)
- Recon 성공: Spring Boot + Node.js + nginx 구조 파악
- 5.4: 소스 분석 → admin 패스워드 leak (${env:ADMIN_PASSWORD}) → JSP 웹쉘 업로드
- 5.2: path traversal, NoSQL injection 시도
- LightCritic: DH{fake_flag} 정확히 가짜로 감지
- BumpEngine: fresh session 재시작 3회 동작
- 결과: flag 직전까지 갔지만 서버 만료로 미획득
- 문제: solver가 브루트포스로 서버 과부하 → anti-brute-force 규칙 추가

### CRC Power (crypto, Dreamhack) ✅ FLAG 획득
- Recon 성공: chal.py 분석 → CRC64 + 이산 로그 문제 파악
- 5.4: Z3/SAT solver로 CRC64 충돌 분석 → PoW 풀기 → 리모트 접속 → **flag 획득**
- 5.2: sympy discrete_log 시도 → Python 3.14 sympy 버그로 실패
- Flag: `DH{crcs_are_so_fun:4gZPbBgkBLLe5BiPRd4zBQ==}`
- writeup.md 자동 생성 (14KB)

---

## 최종 변경사항 (세션 후반)

### 타임아웃 전부 제거
- recon.py: 600초 → 무제한
- light_critic.py: 60초 → 무제한
- app_server.py: 120초 RPC → 무제한
- subprocess timeout, destroy timeout, bump cooldown은 유지

### Recon fallback 프롬프트 삭제
- RECON_FALLBACK_A, RECON_FALLBACK_B 삭제
- Recon 실패 시 최대 3회 재시도, 모든 프롬프트는 Recon이 생성

### dynamicTools 구현
- app_server.py: SANDBOX_TOOLS 정의 + tool_executor 콜백 + item/tool/call 핸들링
- solver.py: _exec_tool() → container_id 있으면 docker exec, 없으면 호스트
- swarm.py: bump 시 새 client에 tool_executor + dynamic_tools 전달

### Anti-brute-force
- 모든 프롬프트에 "NEVER brute-force passwords, logins, or tokens. NEVER send mass requests or flood the server." 추가

### OpenAI 프롬프트 가이드 반영
- Solver A (5.4): completeness_contract + tool_persistence_rules + verification_loop
- Solver B (5.2): grounding_rules + output_verbosity_spec
- 공통: anti-termination, parallel tool calls, observation masking (프롬프트 규칙)

---

## 미구현 (다음 세션)

### Phase 1 잔여
- Docker exec 연동 테스트 (container.py → solver._exec_tool 연결은 됨, 실제 테스트 안 함)
- Manager ↔ Swarm interactive 모드 테스트

### Phase 2
- **Writeup RAG** (최우선 — per-category, 단일 최고 ROI)
- Discord 연동 (terminal_io → discord_bot 교체)
- Decision Tree (전략 로테이션) — BumpEngine + findings 축적으로 자연스럽게 대체 가능
- 벤치마크

### Phase 3 (v2)
- web/forensics/misc 카테고리 테스트
- CTFd 자동 폴링
- 멀티챌린지 병렬화
- autonomous 모드 (CSAW 대회용)

### 알려진 이슈
- Recon 파싱이 가끔 실패 (FINDINGS/SOLVER_PROMPT_A/SOLVER_PROMPT_B 섹션 인식)
- 5.2가 Python 3.14 + sympy 버그로 discrete_log 실패 → Docker 안에서 돌리면 해결 (Python 3.10)
- fruit-market web 문제에서 solver가 서버 과부하 유발 → anti-brute-force 추가했지만 효과 미검증
- Recon이 "0 chars findings"인 경우가 많음 — FINDINGS 섹션 파싱 개선 필요
- trace 파일 경로가 cwd 기준이라 실행 위치에 따라 달라짐

---

## 핵심 참조 파일

| 파일 | 역할 |
|---|---|
| PLAN.md | 전체 설계 문서 (ADR, 레퍼런스, 모델 배치, 구현 일정) |
| ctf-solver/core/app_server.py | Codex App Server JSON-RPC 클라이언트 |
| ctf-solver/core/solver.py | Solver + docker exec tool executor |
| ctf-solver/core/swarm.py | 병렬 레이싱 + BumpEngine + effort escalation |
| ctf-solver/core/recon.py | Recon + 프롬프트 생성 |
| ctf-solver/core/light_critic.py | LightCritic (spark) |
| ctf-solver/main.py | 엔트리포인트 (interactive + direct) |
| ctf-solver/categories/*.md | 8개 카테고리 프롬프트 |
| ctf-solver/sandbox/Dockerfile.* | 9개 Docker 이미지 |

---

## 핵심 교훈

1. **approvalPolicy는 turn/start에** — thread/start에 넣으면 무시됨. 이것 때문에 반나절 디버깅.
2. **approval 응답은 문자열** — `"acceptForSession"`, 객체 `{"decision": ...}` 아님.
3. **fresh session > keep going** — 5.4 퇴화 루프 실제 확인. "keep going" 할수록 짧아짐.
4. **모델 다양성 = 접근 다양성** — 5.4가 Z3로 풀고, 5.2가 sympy로 시도. 하나가 막혀도 다른 놈이 다른 길로.
5. **anti-brute-force 필수** — solver가 서버를 터뜨릴 수 있음.
6. **Recon이 프롬프트를 생성** — fallback 없이 Recon이 모든 프롬프트를 맞춤 생성.
7. **dynamicTools로 docker exec 라우팅** — codex 네이티브 bash 대신 우리 tool executor 경유.
