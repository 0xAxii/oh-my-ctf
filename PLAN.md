# CTF Auto-Solver Harness — Design Plan

## Status: APPROVED (RALPLAN R1, 2026-03-26)

Planner → Architect → Critic 1라운드 합의.
Architect 권고 중 "structured findings schema" v1 채택, 나머지 4개는 v1.1.

---

## 설계 의도와 의식적 트레이드오프

이 설계는 **개인 사용자가 구독 모델로 실전 CTF 대회에서 쓰는 도구**를 만드는 것이다. 학술 벤치마크 재현용이 아니고, 상용 SaaS도 아님.

**의식적으로 채택한 것:**
- **fresh session only (NO compact)**: GPT-5.4의 조기 종료 퇴화 루프(20분→10분→5분)가 GitHub #13950, #13799, Reddit, X에서 다수 보고됨. compact는 context poisoning(잘못된 가설이 압축에 살아남음)을 해결 못 함. Anthropic도 "context reset이 context anxiety를 줄이는 강한 수단"이라 함. 단, 이는 정책이지 절대 규칙이 아님 — 실전에서 compact가 더 나은 케이스가 발견되면 hybrid로 전환할 준비가 되어 있음.
- **LLM Manager (결정론적 control plane 아님)**: 이 도구의 핵심 UX는 사용자와 한국어 대화로 동적 운영하는 것. 카테고리 분류, 힌트, OCR 요청, 도구 설치 등을 대화로 처리. 결정론적 파서로 빼면 "대화에서 없던 상황"에 대응 불가. Manager가 LLM인 이유는 유연성이지 복잡성이 아님.
- **GPT-5.4 + GPT-5.2 (같은 회사, 다른 모델)**: Claude는 토큰 비용이 높고, 구독 모델 아님. GPT 구독 내에서 5.4(능력)과 5.2(끈기, 공식 "long-running agents 최적화")가 상호보완. CyberGym union 효과(개별 10%→합 18.4%)는 모델 다양성뿐 아니라 접근 다양성에서 나옴 — Recon이 문제별 맞춤으로 solver 1,2에 다른 접근을 할당.
- **구독 기반이라 비용 통제 불필요**: API 종량제가 아님. rate limit만 신경 쓰면 됨. 리뷰에서 "per-challenge token budget" 같은 걸 권하는 건 API 종량제 기준이라 해당 없음.
- **Recon(5.4-mini) → 맞춤 프롬프트 생성**: D-CIPHER의 AutoPrompter 패턴과 동일. 사전 고정 프롬프트가 아니라 문제를 실제로 탐색한 후 solver에게 줄 프롬프트를 생성. 탐색 findings는 LightCritic(spark)으로 검증된 것만 사용.
- **LightCritic = spark LLM 검증 (프로그래밍적 regex 아님)**: solver가 자유 형식으로 기록한 findings를 JSONL trace와 대조. "이 주소가 GDB 출력에 실제로 나왔는가" 수준의 교차 검증. 프로그래밍적 regex만으로는 "stack overflow 취약점"같은 의미적 발견을 검증 못 함. spark가 ultra-fast라 오버헤드 미미.
- **observation masking = 프롬프트 규칙**: autocompact를 끄고 턴 1회씩만 돌리므로, solver 프롬프트에 "100줄 넘는 도구 출력은 파일에 저장하고 핵심만 기록"을 명시. 별도 컴포넌트가 아니라 프롬프트 규칙으로 처리.

**의식적으로 채택하지 않은 것:**
- **CSAW autonomous 모드**: 이 도구는 개인 사용 목적. 대회 자동화 규칙(HITL 금지)은 필요 시 Manager를 policy 기반으로 전환하면 됨. v1에서 두 모드를 분리하는 건 오버엔지니어링.
- **classical planning layer (CHECKMATE)**: v1은 LLM 오케스트레이션으로 시작. 실전에서 병목이 전략 선택이면 v2에서 도입.
- **microVM / Firecracker**: Docker 격리로 충분. CTF 환경에서 프로덕션급 isolation은 불필요.
- **sandbox escape 모니터링**: CTF solver는 공격 도구를 실행하는 게 목적. "위험한 명령"을 필터링하면 exploit 자체가 안 됨. Docker 격리가 방어선.
- **turn/steer 기반 bump**: GitHub #11062에서 steer가 원래 작업을 버리게 하는 문제 확인됨. BumpEngine은 steer를 쓰지 않고 fresh session만 사용.

---

## 핵심 결정사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 언어 | Python 3.12+ asyncio | CTF 생태계 네이티브, verialabs/Squid Agent와 동일 |
| 기반 | Codex App Server JSON-RPC | turn/completed 이벤트로 조기 종료 정확 감지. spike test 검증됨 |
| 모델 | GPT-5.4 + GPT-5.2 병렬 레이싱 | 5.4=똑똑+빨리멈춤, 5.2="long-running agents 최적화". 역할별 reasoning effort 분리 (아래 참조) |
| 샌드박스 | Docker per solver instance + externalSandbox | 별도 컨테이너 격리. Codex sandbox를 externalSandbox로 설정해 이중 격리 오버헤드 제거 |
| 세션 전략 | Fresh session only (NO compact) | 5.4 퇴화 루프 차단, context poisoning 방지 |
| 상태 관리 | verialabs 스타일 최소 (findings + JSONL trace + MessageBus) | 단순함 우선 |
| 품질 게이트 | Flag regex (프로그래밍적) + LightCritic (프로그래밍적) | LLM judge 불필요 |
| v1 범위 | pwn + rev + crypto | 도구 특화가 필요한 3개 먼저 |
| Manager | 대화형 매니저 (v1: 터미널, 후에 Discord) | 모든 상호작용의 창구. 카테고리 분류, 힌트, 도구 요청 등 동적 대화 |
| CTFd 자동화 | v2 | 챌린지는 Manager 대화로 지정 |
| 비용 | 무관 (구독) | rate limit만 신경 |
| 기존 TS 코드 | 폐기 | WSL→Fedora 전환 + Mentor 설계 폐기 + Python rewrite |

### 모델별 역할 + reasoning effort

| 역할 | 모델 | effort | 이유 |
|---|---|---|---|
| Manager | GPT-5.4 | medium | 대화+판단. SWE-Bench 그래프상 5.4 medium > 5.4-mini xhigh |
| Recon | GPT-5.4 | medium | 탐색+프롬프트 생성 |
| LightCritic | GPT-5.3-codex-spark (폴백: GPT-5.4 low) | low | trace 대조 매칭, 추론 거의 불필요 |
| Solver 1 | GPT-5.4 | medium → high → xhigh | 점진적 escalation (아래 참조) |
| Solver 2 | GPT-5.2 | medium → high → xhigh | 동일 escalation. **⚠️ 2026-06-05 은퇴 예정** |

### Solver Effort Escalation

Solver는 medium에서 시작, bump 횟수에 따라 점진적으로 올림:

| bump 횟수 | effort | 이유 |
|---|---|---|
| 0~2 | medium | 쉬운 문제는 여기서 끝남. 빠르고 효율적 |
| 3~5 | high | medium으로 안 되면 추론 깊이 올림 |
| 6+ | xhigh | findings 축적으로 실패 루트가 배제된 상태. 깊은 추론으로 남은 가능성 탐색 |

각 solver 독립 bump 카운트. 상한 없음 — flag 찾거나 사용자가 중단할 때까지. fresh session + findings 축적으로 매 bump마다 새 루트를 시도하므로 계속 의미 있음.

---

## 아키텍처

```
ctf-solver/ (Python 3.12+, asyncio)
├── main.py                     # CLI entrypoint (터미널 대화 시작)
├── core/
│   ├── app_server.py           # Codex App Server JSON-RPC (stdin/stdout)
│   ├── recon.py                 # Recon (spark) — 초기 탐색 + 문제별 맞춤 solver 프롬프트 생성
│   ├── swarm.py                # ChallengeSwarm (5.4 + 5.2 병렬 레이싱)
│   ├── solver.py               # SolverProtocol + AppServerSolver
│   ├── bump_engine.py          # turn/completed → LightCritic → fresh thread + verified findings
│   ├── message_bus.py          # Cross-solver findings (append-only + per-model cursor)
│   ├── loop_detector.py        # 반복 도구 호출 감지 (12-window, 5-threshold, provisional)
│   ├── light_critic.py         # findings 검증 (spark) + flag 판별 + 가짜 flag 필터링
│   └── tracer.py               # JSONL 이벤트 트레이싱
├── manager/
│   ├── manager.py              # Manager Agent (GPT-5.4-mini, 대화형 창구)
│   ├── terminal_io.py          # v1: 터미널 stdin/stdout 대화
│   └── discord_bot.py          # 후에 연결: Discord 양방향 통신
├── categories/
│   ├── router.py               # 카테고리 분류 (Manager 대화 + file cmd 보조)
│   ├── pwn.py                  # PWN 프롬프트 + 도구 + 전략
│   ├── rev.py                  # REV 프롬프트 + 도구 + 전략
│   └── crypto.py               # CRYPTO 프롬프트 + 도구 + 전략
├── sandbox/
│   ├── Dockerfile              # 기존 ctf-tools 이미지 기반
│   ├── container.py            # 컨테이너 생성/삭제 (ctf-solver-{model}-{uuid[:8]})
│   └── tools.txt               # 설치 도구 목록
└── tests/
    ├── test_app_server.py
    ├── test_swarm.py
    └── challenges/             # 테스트용 챌린지
```

---

## 핵심 컴포넌트

### 0. Manager (`manager/manager.py` + `manager/discord_bot.py`)

**모든 상호작용의 창구.** 사용자와 Discord로 대화하며 동적으로 파이프라인을 운영.

GPT-5.4-mini, on-demand 호출 (상시 가동 아님 — 메시지 올 때만 turn/start).

```python
class Manager:
    """Discord 대화로 모든 것을 동적으로 결정."""

    async def handle_message(self, text: str) -> str:
        """사용자 메시지 → Manager LLM → 행동 결정"""
        # Manager가 대화 맥락을 보고 알아서 판단:
        # - "이 문제 풀어봐" + 파일 → 카테고리 질문 or 바로 스폰
        # - "리모트 주소 있어?" → 사용자에게 질문
        # - "힌트: tcache 아니야" → MessageBus로 solver에 전달
        # - "상태?" → solver 진행 보고
        # - "중단" → cancel_event.set()

    async def report_event(self, event: str, data: dict) -> None:
        """파이프라인 이벤트 → Discord로 사용자에게 보고"""
        # solver 막힘 → "solver가 heap 분석 중 진전 없음. 힌트 있어?"
        # 도구 필요 → "volatility3 설치 필요. 설치해도 돼?"
        # OCR 필요 → "이 이미지 텍스트 읽어줄 수 있어?" + 이미지 첨부
        # flag 발견 → "flag{...} 찾았다!"

    async def relay_to_solver(self, hint: str) -> None:
        """사용자 힌트 → MessageBus broadcast"""
```

Manager가 결정하는 것들 (결정론적이 아닌 대화 기반):
- 카테고리 분류: 사용자 설명 + file 명령 보조
- 리모트 서버 주소: 사용자에게 질문
- Flag 형식: 사용자에게 질문 or 문제 설명에서 추출
- 도구 설치 승인: 사용자에게 요청
- OCR/이미지 분석: 사용자에게 요청
- 힌트 전달: 사용자 → solver
- 전략 변경: solver 막히면 사용자에게 판단 요청
- Flag 제출: 사용자 확인 후 제출

### 1. Recon (`core/recon.py`)

**Solver 시작 전 초기 탐색. spark로 빠르게 문제 파악 후, 문제별 맞춤 solver 프롬프트 생성.**

```
챌린지 투입
  → Recon (5.4-mini)
    - file, checksec, strings, Ghidra 디컴파일 요약 등 기본 탐색
    - 탐색 findings → LightCritic 검증 → findings_verified에 기록
  → 검증된 findings 기반으로 Solver 1,2 시작 프롬프트 맞춤 생성
    - 문제 특성에 맞는 구체적 지시 (보호기법, 취약점 위치, 추천 접근법)
    - Solver 1,2에 서로 다른 접근 방향 할당 (문제를 본 후 결정)
  → Solver 5.4 + 5.2 동시 시작
```

사전 고정 프롬프트가 아니라 문제를 실제로 본 후 맞춤 생성.
Recon이 spark라 빠르고(수십 초), findings 검증도 같은 LightCritic 파이프라인.

### 2. AppServerClient (`core/app_server.py`)

`codex app-server` 프로세스를 asyncio subprocess로 스폰.
JSON-RPC over stdin/stdout 양방향 통신.

```python
class AppServerClient:
    async def connect(self) -> None
    async def start_thread(self, model: str, sandbox: str, cwd: str) -> str  # → threadId
    async def start_turn(self, thread_id: str, input: list[dict]) -> str     # → turnId
    async def steer(self, thread_id: str, turn_id: str, input: list[dict]) -> None
    async def interrupt(self, thread_id: str, turn_id: str) -> None
    async def destroy(self) -> None
```

이벤트 스트리밍:
- `turn/completed` → status: completed|interrupted|failed
- `item/agentMessage/delta` → 모델 사고 과정
- `item/commandExecution/outputDelta` → 도구 실행 출력
- `ContextWindowExceeded` → BumpEngine 트리거

Sandbox 정책: `sandboxPolicy.type = "externalSandbox"` — Docker가 이미 격리하므로 Codex 자체 sandbox 끔
프로세스 크래시: `process.returncode` 감지 → 자동 재스폰 (최대 3회)

참고 구현: `/tmp/ctf-agent/backend/agents/codex_solver.py`, 기존 `/home/axii/ctf_llm/src/core/app-server-client.ts`

### 3. ChallengeSwarm (`core/swarm.py`)

verialabs/ctf-agent 패턴 직접 차용.

```python
@dataclass
class ChallengeSwarm:
    challenge_dir: str
    model_specs: list[str]          # ["codex/gpt-5.4/xhigh", "codex/gpt-5.2/xhigh"]
    cancel_event: asyncio.Event
    message_bus: ChallengeMessageBus
    solvers: dict[str, SolverProtocol]
    findings: dict[str, str]
    winner: SolverResult | None

    async def run(self) -> SolverResult | None:
        """모든 solver를 병렬 실행. 첫 flag 발견 시 나머지 중단."""
        tasks = [asyncio.create_task(self._run_solver(spec)) for spec in self.model_specs]
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=FIRST_COMPLETED)
            for task in done:
                result = task.result()
                if result and result.status == FLAG_FOUND:
                    self.cancel_event.set()
                    for p in pending: p.cancel()
                    return result
            tasks = list(pending)
        return self.winner
```

참고 구현: `/tmp/ctf-agent/backend/agents/swarm.py`

### 4. BumpEngine (`core/bump_engine.py`)

GPT-5.4 조기 종료 핵심 대응. "keep going" 절대 사용 안 함.

```
turn/completed 감지 (flag 없음)
  → LightCritic: findings를 JSONL trace 대비 검증
  → 검증된 findings만 추출 (VerifiedFindings)
  → 새 thread 생성 (fresh session — 이전 히스토리 없음)
  → 카테고리 프롬프트 + verified findings 주입
  → turn/start → solver 재시작
  → bump_count++ (최대 15회)
  → bump 간 쿨다운: min(bump_count * 30, 300)초
```

왜 fresh session이 "keep going"보다 나은가:
- 대화 히스토리에 "나는 멈췄다" 패턴이 안 쌓임 → 퇴화 루프 차단
- context poisoning 없음 (잘못된 가설이 전달 안 됨, LightCritic이 필터)
- GPT-5.4의 20분→10분→5분 감소 패턴 완전 방지

### 5. LightCritic (`core/light_critic.py`)

**GPT-5.3-codex-spark로 findings를 빠르게 검증.** Solver가 자유롭게 적은 raw findings를 trace(도구 출력 기록)와 대조해서 검증된 것만 통과시킴.

```
흐름:
  Solver 5.4 → findings_raw_5.4.md (자유 형식, 발견할 때마다 append)
  Solver 5.2 → findings_raw_5.2.md
                      │
                      ▼ (파일 변경 감지 — inotify or polling)
              LightCritic (5.3-codex-spark, ultra-fast)
              "이 raw finding이 JSONL trace의 도구 출력과 일치하는가?"
                      │
                      ▼
              findings_verified.json (검증 통과한 것만)
                      │
                      ▼ (bump 시 이것만 주입)
              BumpEngine → 새 session + verified findings only
```

Solver 프롬프트에 규칙 하나만 추가:
  "발견한 사실(주소, 취약점, 보호기법 등)은 findings_raw.md에 append해라."

LightCritic(spark)이 하는 일:
  - raw finding + 해당 시점의 trace(도구 출력)를 함께 보고
  - "이 주소가 GDB 출력에 실제로 나왔는가?" 수준의 단순 교차 검증
  - 검증 통과 → findings_verified.json에 추가
  - 검증 실패 → 버림 (다음 세션에 전달 안 됨)

Flag 판별도 LightCritic이 담당:
  - findings에 flag 형식 문자열 등장 → spark가 trace 대조
  - 진짜 (remote 출력에서 나왔거나 exploit 실행 결과) → Manager에 보고 → 사용자에게 보고
  - 가짜 (strings에 박혀있던 것, placeholder 등) → "이건 가짜 flag" 기록 → 다음 세션에 전달 → 같은 실수 반복 방지

왜 spark인가:
  - ultra-fast (검증은 추론이 아니라 매칭)
  - 저렴 (가벼운 모델로 충분)
  - solver를 방해하지 않음 (비동기로 별도 실행)

### 7. MessageBus (`core/message_bus.py`)

verialabs 패턴 그대로.

```python
@dataclass
class ChallengeMessageBus:
    findings: list[Finding]       # append-only
    cursors: dict[str, int]       # per-model read cursor
    _lock: asyncio.Lock

    async def post(self, model: str, content: str) -> None: ...
    async def check(self, model: str) -> list[Finding]: ...  # 다른 모델의 unread
    async def broadcast(self, content: str) -> None: ...
```

참고 구현: `/tmp/ctf-agent/backend/message_bus.py`

### 6. LoopDetector (`core/loop_detector.py`)

verialabs 패턴. 동일 도구 호출 반복 감지.

```python
@dataclass
class LoopDetector:
    window: int = 12           # 최근 N개 추적
    warn_threshold: int = 3    # 경고
    break_threshold: int = 5   # 강제 중단

    def check(self, tool_name: str, args: dict) -> str | None:
        # None | "warn" | "break"
```

파라미터는 provisional. 실험 후 조정.
참고 구현: `/tmp/ctf-agent/backend/loop_detect.py`

### 8. Tracer (`core/tracer.py`)

JSONL append-only. 디버깅 + LightCritic 입력.

```python
class SolverTracer:
    def tool_call(self, tool_name: str, args: str, step: int) -> None
    def tool_result(self, tool_name: str, result: str, step: int) -> None
    def model_response(self, text: str, step: int) -> None
    def event(self, kind: str, **kwargs) -> None
```

스키마: `{ts: float, type: str, tool?: str, args?: str, result?: str, step?: int, ...}`
참고 구현: `/tmp/ctf-agent/backend/tracing.py`

---

## 카테고리별 프롬프트

### 공통 Anti-Termination 규칙 (전 카테고리)

```
## MANDATORY RULES
- DO NOT describe what you will do. EXECUTE immediately.
- DO NOT output a plan, summary, or status update. USE TOOLS.
- You are DONE only when you have captured a valid flag matching the format.
- If a tool fails, try a DIFFERENT tool or approach. Never repeat the same failed command.
- Every address/offset you use MUST come from actual tool output, not from memory.
```

Codex 설정: `reasoning_effort = xhigh` (5.4 기본값이 none이라 필수)

### PWN (`categories/pwn.py`)

```
Phase 1: Recon
  checksec --file=./binary
  file ./binary
  strings ./binary | grep -iE "flag|cat|system|/bin|shell|win"
  readelf -S ./binary
  ldd ./binary

Phase 2: Static Analysis (Ghidra headless)
  Decompile main + 취약 함수
  패턴: gets/strcpy/printf(buf)/free-without-null → 취약점 분류

Phase 3: Dynamic Analysis (GDB)
  cyclic pattern → offset 찾기
  주소 검증: info address <func>
  힙: heap chunks, vis_heap_chunks, heap bins
  모든 수치는 GDB 출력에서 직접 추출

Phase 4: Exploit (pwntools)
  단계별: leak → control → payload
  각 단계 성공 확인 후 다음
  python3 solve.py | ./binary 로 로컬 검증

Phase 5: Remote
  process() → remote(host, port) 전환
  타임아웃 30초
```

보호기법 바이패스:
| 보호기법 | 전략 |
|---|---|
| NX ON | ROP / ret2libc |
| PIE ON | 런타임 leak 필요 |
| PIE OFF | 절대 주소 직접 사용 |
| Canary ON | format string leak 또는 brute force |
| RELRO Full | FSOP (glibc ≥ 2.34) |
| RELRO Partial | GOT overwrite |

### REV (`categories/rev.py`)

```
Phase 1: Recon — file, strings, checksec
Phase 2: Decompile — Ghidra headless → 알고리즘 분석
Phase 3: Constraint Solving — z3/angr 스크립트 생성 + 실행
Phase 4: Verification — python3 solve.py → flag
```

### CRYPTO (`categories/crypto.py`)

```
Phase 1: Identify — 암호 프리미티브, 파라미터, 약점
Phase 2: Attack — SageMath/z3로 공격 스크립트 작성
  - RSA: 작은 e → Coppersmith, 공통 p → GCD, weak key → factordb/RsaCtfTool
  - AES: ECB → 블록 치환, CBC → bit flip, padding oracle
  - Custom: z3 제약 풀이
Phase 3: Execute — 스크립트 실행 → flag 추출
Phase 4: Verify — flag regex 확인
```

---

## Docker Sandbox (`sandbox/`)

기존 ctf-tools 이미지 재활용. solver 인스턴스마다 별도 컨테이너.

컨테이너 네이밍: `ctf-solver-{model}-{uuid[:8]}`
워크스페이스: bind-mount, solver마다 격리된 복사본

도구 목록:
```
PWN/REV: gdb, pwndbg, pwntools, ROPgadget, one_gadget, angr, capstone, Ghidra headless
CRYPTO:  SageMath, z3, gmpy2, pycryptodome, RsaCtfTool, cado-nfs
COMMON:  python3, pip, gcc, binwalk, strings, objdump, readelf, file, checksec
```

네트워크: 원격 서버 접속 허용 (pwn remote 필요)

---

## GPT-5.4 조기 종료 대응 (3계층)

### Layer 1: Codex 설정
- `reasoning_effort = xhigh` (5.4 기본값 none → 필수 변경)
- `phase` 파라미터: commentary vs final_answer (중간 보고가 최종으로 오인되는 것 방지)
- 프리앰블/계획 프롬프트 제거 ("설명 말고 실행해")

### Layer 2: BumpEngine (하네스)
- turn/completed 감지 → flag 없으면 fresh session 자동 시작
- findings를 파일로 주입 (대화 히스토리 아님)
- "keep going" 절대 사용 금지
- 최대 15 bump, 쿨다운 점진적 증가

### Layer 3: 모델 보험
- GPT-5.2가 병렬로 돌면서 5.4 조기 종료를 커버
- 5.2는 "Optimized for long-running agents" 공식 명시
- 하나가 멈춰도 다른 모델이 계속

---

## 실행 흐름

```
사용자가 하네스 시작: python main.py
Manager가 터미널 대화 모드로 대기 (v1: stdin/stdout, Phase 2에서 Discord 연결)

사용자: "이 문제 풀어봐 ./chall/ dreamhack pwn 문제야"
Manager: "PWN으로 분류할게. 리모트 서버 주소 있어?"
사용자: "host1.dreamhack.games:1337"
Manager: "flag 형식은?"
사용자: "DH{}"
Manager: "풀이 시작한다."

→ Manager가 내부적으로:
  1. Docker 컨테이너 2개 생성 (5.4용, 5.2용)
  2. codex app-server 2개 스폰
  3. ChallengeSwarm 시작 (5.4 + 5.2 병렬)
  4. 이벤트 스트리밍 수신

Manager: "solver 2개 돌리는 중. 5.4가 checksec 시작했어."

Solver 5.4가 조기 종료:
  → BumpEngine: fresh session + verified findings
  → Manager: "5.4가 한번 멈춰서 재시작했어. stack overflow 찾은 상태."

Solver 5.2가 막힘:
  → Manager: "5.2가 heap 분석에서 진전이 없어. 힌트 있어?"
  사용자: "tcache가 아니라 fastbin이야"
  → Manager → MessageBus broadcast → 두 solver 모두에 전달

Solver 5.4가 flag 발견:
  → LightCritic: findings에서 flag 감지 + trace 대조 검증
  → Manager: "DH{flag_here} 찾았다! 제출할까?"
  사용자: "ㅇㅇ"
  → 5.2 중단, 결과 저장

도구 필요 상황:
  Solver: "volatility3가 없어서 메모리 분석 못 함"
  → Manager: "volatility3 설치가 필요해. 설치해도 돼?"
  사용자: "ㅇㅇ"
  → Manager → Docker에 pip install volatility3 → solver 계속

OCR 필요 (misc):
  Solver: "이미지에 텍스트가 있는데 읽을 수 없음"
  → Manager: "이 이미지 텍스트 읽어줄 수 있어?"
  사용자: "HELLO_WORLD_123 이라고 써있어"
  → Manager → solver에 전달
```

---

## 구현 일정

### Phase 1: Core (1-2주)

| Step | 내용 | Done when |
|---|---|---|
| 1 | AppServerClient (Python JSON-RPC) | pytest 5 cases: connect, start_thread, start_turn, turn/completed, crash recovery |
| 2 | Docker sandbox | 기존 이미지 재활용, 컨테이너 안 checksec+gdb+sage 통과 |
| 3 | Solver + BumpEngine + LightCritic | 단일 5.4, 쉬운 pwn: bump cycle + verified findings → flag |
| 4 | ChallengeSwarm + MessageBus | 5.4+5.2 병렬, flag→cancel, findings 공유 |
| 5 | Manager Agent (터미널) | GPT-5.4-mini on-demand 대화 + 한국어 응답. 터미널 stdin/stdout |
| 6 | Manager ↔ Swarm 통합 | 터미널에서 "풀어봐" → Manager가 swarm 스폰 → 진행 보고 → 힌트 전달 → flag 알림 |
| 7 | 카테고리 프롬프트 + e2e | pwn/rev/crypto 각 1개, 터미널 대화로 전체 흐름 완주 |

### Phase 2: 강화 (1-2주)

| Step | 내용 | Done when |
|---|---|---|
| 7 | **Writeup RAG (최우선)** | **per-category** RAG DB (pwn/rev/crypto 별도). Chroma/FAISS + writeup. RAG 유무 비교. 리서치에서 단일 최고 ROI로 확인됨 (KryptoPilot 10×, CRAKEN web 3×, CTFAgent +85%/+120%) |
| 8 | Discord 연결 | Manager의 IO를 terminal_io → discord_bot으로 교체. 양방향 통신 확인 |
| 9 | Decision Tree (전략 로테이션) | pwn 3회 같은 전략 실패 → 다른 전략 전환 |
| 10 | 벤치마크 | 자체 10개 문제 solve rate 측정 |

### Phase 3: 확장 (v2)

- web / forensics / misc 카테고리 추가
- CTFd 자동 폴링 + 자동 제출
- 멀티챌린지 병렬화
- Writeup 자동 생성 + knowledge DB 축적

### v1.1 (Architect 권고, Phase 1 후)

- 모델별 다른 전략 주입 (5.4=static first, 5.2=dynamic first)
- Progress-aware bump cooldown (새 findings → 짧은 쿨다운)
- MessageBus content-hash dedup
- Semantic loop detection (findings set 3회 unchanged → escalate)

---

## 레퍼런스

### CTF 자동 풀이 시스템 (직접 분석/참고)

| 시스템 | 성과 | 아키텍처 | 우리가 가져온 것 | URL |
|---|---|---|---|---|
| **verialabs/ctf-agent** | 52/52 BSidesSF 2026 1위 | Coordinator + 병렬 model swarm + Docker sandbox + MessageBus + bump | ChallengeSwarm, MessageBus, bump 패턴, LoopDetector, flag 제출 관리 | https://github.com/verialabs/ctf-agent |
| **Machine (sane100400)** | Claude Code 네이티브 CTF solver | 카테고리별 파이프라인 + SQLite state(--src) + quality_gate.py + decision_tree.py + knowledge FTS5 | 카테고리별 프롬프트 구조, anti-hallucination 개념, decision tree 패턴 | https://github.com/sane100400/Machine |
| **Squid Agent (SPL)** | 92% CTFTiny (46/50), CSAW 1위 | 카테고리별 서브에이전트 시스템 + per-category RAG + IDA Pro 공유 | 카테고리 특화의 근거, criticize_agent 패턴, dual-path(복잡/단순) 분기 | https://spl.team/blog/squid-agent-csaw/ |
| **CAI (Alias Robotics)** | 99% 평균 백분위 5개 대회, NeuroGrid 1위 | 300+ 모델, OpenAI Agents SDK 포크, 에이전트 패턴, guardrails | interactive session 관리(nc/ssh), guardrails(프롬프트 인젝션 방어), flag_discriminator handoff | https://github.com/aliasrobotics/cai |

### CTF+LLM 시스템 (리서치에서 확인)

| 시스템 | 날짜 | 성과 | 핵심 기여 | URL |
|---|---|---|---|---|
| **EnIGMA** | 2024-09 | 13.5% NYU CTF, 72% InterCode-CTF | Interactive Agent Tools (GDB/netcat/Ghidra를 LLM-friendly 스키마로 추상화) | arXiv 2409.16165 |
| **D-CIPHER** | 2025-02 | 22% NYU CTF, 44% HackTheBox | Planner-Executor 멀티에이전트, Auto-prompter, 65% 더 많은 MITRE ATT&CK 커버리지 | arXiv 2502.10931 |
| **CRAKEN** | 2025-05 | D-CIPHER + Self-RAG/Graph-RAG | writeup RAG로 8개 독점 솔브, Graph-RAG > vector RAG | arXiv 2505.17107 |
| **CTFAgent/CTFKnow** | 2025-06 | picoCTF 상위 23.6% | 2단계 RAG (DB-Understanding + DB-Exploiting), +85% InterCode-CTF, +120% NYU CTF | arXiv 2506.17644 |
| **PwnGPT** | 2025 (ACL) | 57.9% exploit completion (o1-preview) | Analysis-Generation-Verification 3모듈, pwn 전용 벤치마크 | ACL 2025 |
| **PentestGPT v2** | 2025 | 91% 벤치마크, +39-49% | Task Difficulty Assessment + Evidence-Guided Attack Tree Search | arXiv (USENIX) |
| **CHECKMATE** | 2025-12 | >20% over Claude Code, 50% 비용↓ | classical planning + LLM 하이브리드 | arXiv 2512.11143 |
| **ATLANTIS (Team Atlanta)** | 2025 | AIxCC 1위 ($4M), 18개 실제 0-day | N-version parallel CRS, GPT-4o-mini가 큰 모델보다 패칭 잘함 | arXiv 2509.14589 |
| **Buttercup (Trail of Bits)** | 2025 | AIxCC 2위, $181/point | non-reasoning LLM만 사용 (Claude Sonnet 4, GPT-4.1) | blog.trailofbits.com |
| **RoboDuck (Theori)** | 2025 | AIxCC 3위 | LLM-only PoV 생성 (fuzzing/symbolic 없이) | theori.io/blog |
| **Big Sleep (Google P0+DeepMind)** | 2024-2025 | 최초 AI 발견 실제 0-day (SQLite), 20+ 보안 결함 | Gemini 1.5 Pro + 가설 기반 반복 루프 | projectzero.google |
| **AISLE** | 2025-2026 | OpenSSL 12개 0-day 전부 발견, 100+ CVE | 완전 자율 scan→analyze→exploit→patch | aisle.com |
| **XBOW** | 2025 | HackerOne US 1위, 1060+ 취약점 리포트 | LLM 발견 + deterministic non-AI 검증기 → false positive 제거 | xbow.com |
| **Cyber-Zero** | 2025-07 | Qwen3-32B가 Claude 3.5 Sonnet 수준 달성 | 실행 없이 writeup 시뮬레이션으로 훈련 데이터 생성 | github.com/amazon-science/Cyber-Zero |
| **CTF-Dojo** | 2025-08 | 31.9% Pass@1 (open-weight SOTA) | 658 Docker 챌린지 자동 생성, 486 검증된 trajectory로 fine-tune | github.com/amazon-science/CTF-Dojo |
| **Pentest-R1** | 2025 | 24.2% AutoPenBench (GPT-4o 초과) | 2단계 RL (GRPO) fine-tuning | arXiv 2508.07382 |
| **Plain Agents (Palisade)** | 2024-12 | 95% InterCode-CTF (포화) | 단순 ReAct + Plan&Solve로 벤치마크 포화 시킴 | arXiv 2412.02776 |
| **ByteBreach** | 2025-11 | GPT-5-codex 12/19, Claude 10/19 | RAG 유무 비교: HackTheBox pwn 1/4→3/4 | Georgia Tech |
| **MAPTA** | 2025 | 76.9% XBOW 104개 blackbox web CTF | SSRF/misconfig 100%, blind SQLi 0%, "40 tool calls or $0.30에서 조기 종료" | XBOW 벤치마크 |
| **KryptoPilot** | 2026 | 100% InterCode-CTF crypto, 56-60% NYU crypto | Deep Research RAG + SageMath 백엔드 | 2026 |
| **Red-Run** | 2026-03 | Flight.HTB 1시간 24분 (operator nudge 필요) | Claude Code + skills + MCP + SQLite state + Playwright | blog.blacklanternsecurity.com |

### AI 회사 엔지니어링 블로그 (하네스 설계 패턴)

**Anthropic:**
| 글 | 날짜 | 핵심 패턴 | URL |
|---|---|---|---|
| Building effective agents | 2024-12 | 5가지 워크플로 패턴 + 에이전트 구분, 단순함 우선 | anthropic.com/engineering/building-effective-agents |
| Multi-agent research system | 2025-06 | 오케스트레이터-워커, 서브에이전트 요약 반환, 90.2% 성능 향상 | anthropic.com/engineering/multi-agent-research-system |
| Effective context engineering | 2025-09 | 컨텍스트 = 유한 자원, context rot, compaction, just-in-time retrieval | anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| Code execution with MCP | 2025-11 | 도구를 코드 API로 → 98.7% 토큰 절감, 중간 결과 마스킹 | anthropic.com/engineering/code-execution-with-mcp |
| Effective harnesses for long-running agents | 2025-11 | initializer + coding agent, 아티팩트 핸드오프, context anxiety → 리셋 | anthropic.com/engineering/effective-harnesses-for-long-running-agents |
| Building a C compiler with parallel Claudes | 2026-02 | 16 병렬 에이전트, lock 파일 조율, $20K/100K줄 | anthropic.com/engineering/building-c-compiler |
| Harness design for long-running apps | 2026-03 | GAN 영감 Planner-Generator-Evaluator, sprint 계약, 모델 향상 시 scaffold 제거 | anthropic.com/engineering/harness-design-long-running-apps |

**OpenAI:**
| 글 | 날짜 | 핵심 패턴 | URL |
|---|---|---|---|
| Unlocking the Codex harness (App Server) | 2026-02 | JSON-RPC 양방향, Items/Turns/Threads, 이벤트 스트리밍 | openai.com/index/unlocking-the-codex-harness/ |
| Unrolling the Codex agent loop | 2026-01 | 프롬프트 구성 → Responses API → 도구 실행 → 루프 | openai.com/index/unrolling-the-codex-agent-loop/ |
| Run long horizon tasks with Codex | 2026-02 | 25시간 무중단 실행, Prompt.md/Plan.md/Implement.md 파일 스택 | developers.openai.com/blog/run-long-horizon-tasks-with-codex/ |
| Codex Prompting Guide | 2026-02 | phase 파라미터, 병렬 도구 호출, anti-termination, 프리앰블 제거 | developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide/ |
| Subagents | 2026 | 컨텍스트 오염/로트 방지, 명시적 트리거, 요약만 반환 | developers.openai.com/codex/concepts/subagents |
| Skills + Shell + Compaction | 2026 | progressive disclosure, SKILL.md 메타데이터 → 상세 → scripts | developers.openai.com/blog/skills-shell-tips/ |
| Skills for OSS maintenance | 2026-03 | AGENTS.md + skills → PR 처리량 44% 증가 | developers.openai.com/blog/skills-agents-sdk/ |
| Monitoring agents for misalignment | 2026-03 | 비동기 모니터 → 향후 동기 차단 확장 로드맵 | openai.com/index/how-we-monitor-internal-coding-agents-misalignment/ |

**Google:**
| 글 | 날짜 | 핵심 패턴 | URL |
|---|---|---|---|
| Multi-agent patterns in ADK | 2025-12 | 8가지 멀티에이전트 패턴, session.state로 상태 전달 | developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/ |
| CodeMender | 2025 | LLM judge + self-correct 루프, 인간 최종 리뷰 게이트 | deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/ |
| A2A protocol | 2025-04 | 에이전트 간 상호운용, long-running task, artifact 출력 | developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/ |
| Lessons from 2025 on agents and trust | 2025 | non-atomic 실패 위험, guardrails/critics/routers 필요 | cloud.google.com/transform/ai-grew-up-and-got-a-job-lessons-from-2025-on-agents-and-trust |

**기타:**
| 출처 | 핵심 | URL |
|---|---|---|
| Mistral Agents API | handoff 기반 멀티에이전트 오케스트레이션 | mistral.ai/news/agents-api |
| Meta LlamaFirewall | 에이전트 입출력 다단계 guardrail | arxiv.org/html/2505.03574v1 |
| Cohere tool use patterns | multi-step/parallel tool calling, self-correct | docs.cohere.com/docs/tool-use-usage-patterns |

### 벤치마크/데이터셋

| 벤치마크 | 규모 | 난이도 | SOTA | 비고 |
|---|---|---|---|---|
| InterCode-CTF | 100 | 고등학교 | 95% (Palisade) — **포화** | 더 이상 유용하지 않음 |
| NYU CTF Bench | 200+55 | 대학 (CSAW) | 22% (D-CIPHER/CRAKEN) | 데이터 오염 우려 (CTFUSION) |
| Cybench | 40 | 전문가 | 46% (Claude Sonnet 4.5) | US/UK AISI 채택, 11분 임계값 |
| CTFTiny | 50 | 소형 평가용 | 92% (Squid Agent) | 하이퍼파라미터 민감도 분석 |
| CyberGym | 1507 | 실제 취약점 | 17.9% 단일 / 67% 30회 | union 효과: 개별 10% → 합 18.4% |
| CVE-Bench | 40 CVE | 프로덕션 웹앱 | 10-12.5% | CTF vs 프로덕션 갭 6-7배 |
| BountyBench | 40 바운티 | 실제 | 패칭 90%, 탐지 12.5% | 방어 > 공격 |
| AutoPenBench | 33 | 다양 | 24.2% (Pentest-R1) | 자동/반자동 비교 |
| DARPA AIxCC | 실제 OSS | 프로덕션 | 86% 탐지, 68% 패칭 | 54M LOC, 7개 팀 |
| CAIBench | 10000+ | 메타벤치마크 | — | 7+ 벤치마크 통합 |
| CTFusion | 동적 (라이브) | 다양 | 라이브 성능 = 정적의 ~50% | 데이터 오염 문제 실증 |

### 학술 논문 (핵심 인사이트)

| 논문 | 인사이트 | 출처 |
|---|---|---|
| AICrypto (2025-07) | o3가 MCQ 97.8%지만 CTF 실전은 한 자릿수. "알면서 못 하는" 문제 | arXiv |
| Random-Crypto RL (2025) | Llama-3.1-8B fine-tune → crypto Pass@8 0.35→0.88 (+53%p). 도구 호출 안정성이 핵심 | arXiv |
| JetBrains Complexity Trap | observation masking ≈ LLM 요약 성능, 비용 50%↓ | JetBrains |
| ACON (Microsoft Research) | 26-54% 토큰 절감, 95%+ 정확도 유지 | Microsoft |
| Morph Compact | 50-70% 압축, 98% verbatim 정확도 | — |
| LLM4Decompile (EMNLP 2024) | 특화 모델이 GPT-4o 대비 재실행 가능성 100%+ 향상. x86_64 C만 지원 | EMNLP |
| NCC Group | 인간 작성 코드가 Ghidra 출력보다 LLM 분석에 유리 (학습 데이터 편향) | NCC Group |
| ChatDBG (UMass, FSE 2025) | LLM이 디버거를 자율 구동, 85-87% 결함 진단 성공, <$0.20/query | FSE 2025 |
| CTFUSION (ICLR 2026 제출) | 라이브 CTF 성능 = 정적 벤치마크의 ~50%. 데이터 오염 실증 | ICLR 2026 |
| CoRL (2025) | 동적 모델 라우팅 (싼 모델 ↔ 비싼 모델), 예산 내 최적 성능 | — |

### GPT-5.4 조기 종료 출처

| 출처 | 날짜 | 핵심 | URL |
|---|---|---|---|
| GitHub #13950 | 2026-03-08 | 200 서브에이전트 중 ~10에서 멈춤. OpenAI 스태프 확인 | github.com/openai/codex/issues/13950 |
| GitHub #14228 | 2026-03-10 | merge-train 대기 루프 유지 실패 | github.com/openai/codex/issues/14228 |
| GitHub #13799 | 2026-03-06 | "5.3-codex was relentlessly good — this is a regression" | github.com/openai/codex/issues/13799 |
| GitHub #11062 | 2026-02-08 | steer가 새 작업으로 취급되어 원래 작업 멈춤 | github.com/openai/codex/issues/11062 |
| GitHub #3938 | — | "session time-limited" 환각 → 장기 작업 거부 | github.com/openai/codex/issues/3938 |
| OpenAI 공식 가이드 | 2026 | phase 파라미터 = 조기 종료 방지용 | developers.openai.com/api/docs/guides/prompt-guidance |
| OpenAI 공식 | 2026-03-05 | reasoning_effort 기본값 none (5.4) | openai.com/index/introducing-gpt-5-4/ |
| Reddit r/codex | 2026-03-26 | "5.4 stops after explaining what it will do, requires continue" | reddit.com/r/codex/comments/1rwrs9h/ |
| Reddit r/codex | 2026-03 | "5.4 prematurely claims success and feels more lazy" | reddit.com/r/codex/comments/1rooc9h/ |
| Karpathy autoresearch #57 | 2026-03-08 | "never stop" 지시를 Codex가 무시 | github.com/karpathy/autoresearch/issues/57 |
| d4b.dev | 2026-03-04 | Ralph Wiggum loop — 외부 bash 루프로 fresh session 반복 | d4b.dev |
| OpenAI Community Forum | 2025-11 | "10-15분 chunk → continue → 1-3분으로 퇴화" (5.1-codex-max) | community.openai.com |

### 프레임워크 비교 (2025-2026)

| 프레임워크 | 핵심 특징 | CTF 적합성 |
|---|---|---|
| **OpenAI Agents SDK** | Agents+Handoffs+Guardrails, 트레이싱, 가벼움 | handoff/guardrails 좋지만 병렬 실행 부족 |
| **LangGraph** | 그래프 기반 상태 머신, checkpoint/rollback, durable execution | 복구/상태 관리 최강, 학습 곡선 높음 |
| **CrewAI** | 역할 기반 crew, 빠른 프로토타이핑 | 3× 더 많은 토큰, 6-12개월 후 한계 |
| **AutoGen** | 대화형 멀티에이전트, 이벤트 기반 Core | 유연하지만 Semantic Kernel 합병으로 혼란 |
| **smolagents** | ~1000줄, CodeAgent(코드로 도구 호출) | 단순, Squid Agent가 채택 후 버그로 이탈 |
| **결론** | **모든 상위 CTF 팀이 커스텀 오케스트레이션 사용** | 범용 프레임워크 위 커스텀 하네스 |

### 커뮤니티/실전 경험

| 출처 | 날짜 | 핵심 인사이트 | URL |
|---|---|---|---|
| @stuxfdev (Veria Labs) | 2026-03-23 | 주말에 만들어서 52/52 1위. coordinator + parallel racing | x.com/stuxfdev/status/2036160579229065413 |
| Red-Run (Black Lantern Security) | 2026-03-10 | 비결정성(같은 프롬프트 45분 vs 3시간), compaction이 정보 날림, 에이전트가 지시 무시 | blog.blacklanternsecurity.com/p/red-run |
| HN 유저 @wwdmaxwell | 2026-02-24 | $100/주말 CTF, xAI 가장 저렴, Anthropic rate limit | news.ycombinator.com/item?id=47136683 |
| BoxPwnr benchmark (r/netsec) | 2025 | MCP가 CTF 도구 통합 표준으로 자리잡음 | reddit.com/r/netsec/comments/1s1is41/ |
| Squid Agent X 포스트 | 2025-11 | D-CIPHER의 uniform strategy 한계를 명시적으로 비판 | x.com/SquidProxyLover/status/1990813102804193366 |
| CSAW Agentic CTF 규칙 | 2025 | 완전 자동화 필수, HITL 금지, 커스텀 프레임워크 허용 | csaw.io/agentic-automated-ctf |

### 핵심 수치 요약

| 수치 | 출처 | 설계 영향 |
|---|---|---|
| 에이전트 다양성 union: 개별 10% → 합 18.4% (2배) | CyberGym | 모델 레이싱의 근거 |
| 멀티턴 64.8% vs 싱글턴 25.3% | InterCode-CTF | 피드백 루프 필수 |
| Writeup RAG: +85% InterCode-CTF, +120% NYU CTF | CTFAgent (HKUST) | 단일 최고 ROI |
| Observation masking = LLM 요약 성능, 비용 50%↓ | JetBrains | verbatim masking > 요약 |
| MCP 코드 실행: 98.7% 토큰 절감 | Anthropic | 도구를 코드 API로 |
| PentestGPT v2: 91%, +39-49% | PentestGPT | 난이도 인식 전략 로테이션 |
| Crypto RL fine-tune: Pass@8 0.35→0.88 (+53%p) | Random-Crypto | 도구 호출 안정성 > 수학 능력 |
| 11분 임계값: 인간 11분+ 걸리는 문제 AI 솔브 0건 | Cybench | 현재 AI CTF 상한 |
| 라이브 성능 = 정적 벤치마크의 ~50% | CTFUSION | 정적 벤치마크 과대평가 |
| PWN RAG: HackTheBox 1/4→3/4 | ByteBreach | RAG가 pwn에서도 극적 효과 |
| MAPTA web: 성공 $0.073, 실패 $0.357 | MAPTA | "40 tool calls에서 조기 종료" 기준 |
| Anthropic V1→V2: 37% 비용↓, 품질↑ | Anthropic harness design | 모델 발전 시 scaffold 줄여야 |

---

## ADR (Architecture Decision Record)

**Decision**: Python asyncio + Codex App Server + GPT-5.4/5.2 레이싱 + Fresh session BumpEngine

**Drivers**:
1. GPT-5.4 조기 종료 → 하네스 자동 재시작 필수
2. 카테고리별 도구 차이 → 전문화된 파이프라인
3. 구독 모델 → 비용 무관, 레이싱 부담 없음

**Alternatives considered**:
- Codex exec CLI: turn/completed 감지 불가 → 기각
- Claude Code 네이티브: GPT 레이싱 불가 → 기각
- TS 기존 코드 확장: 환경 소실 + Mentor 설계 폐기 → 기각
- compact + keep going: 퇴화 루프 확정 → 기각

**Why chosen**:
- App Server = 유일하게 turn/steer + 이벤트 스트리밍 + ContextWindowExceeded 지원
- Fresh session = GPT-5.4 퇴화 루프에 대한 현재 기본 정책. 실전 결과에 따라 compact hybrid로 전환 가능
- 5.4+5.2 병렬 = 능력 + 끈기 상호보완

**Consequences**:
- Python rewrite 필요 (1-2주)
- Hard challenge에서 "accumulated reasoning chain" 손실 (v1 한계)
- Docker 이미지 빌드/관리 필요

**Follow-ups**:
- v1 완성 후 hard challenge에서 성능 측정
- 필요시 structured findings → strategic summary 레이어 추가
- Writeup RAG로 knowledge 축적
