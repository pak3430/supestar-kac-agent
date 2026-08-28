# 실제 Runtime과 증명

## 무엇이 실제로 실행되는가

### 1. Local Qwen

Ollama의 `qwen2.5:14b-instruct-q4_K_M`이 다음 행동을 선택합니다.

- 관찰할 개념
- 확장할 관계와 목표 개념
- 실행할 KAC 원자 스킬
- 스킬 입력
- 답변 후보와 evidence ID

연결 주소는 `http://127.0.0.1:11434`만 허용됩니다. 비-loopback endpoint는 코드가 거부합니다.

### 2. CCS Knowledge Environment

`knowledge/graph.json`에는 개념 정의만 있는 것이 아니라 방향성 edge, 관계 이유, 적용 가능한 스킬, source ref가 있습니다.

Qwen은 그래프 파일 전체를 답변처럼 복사하지 않습니다. `observe_concept`와 `expand_relations` 도구를 통해 일부를 Observation으로 받고, 그 Observation이 다음 turn의 입력이 됩니다.

런타임의 공통 lifecycle gate는 필수 anchor 관찰, anchor 연결, Skill 실행, 후보 제출 순서를 관리합니다. 완료된 단계는 다시 열지 않지만, 남은 개념·관계 방향·Skill·입력은 Qwen이 고릅니다. 이는 질문별 정답 경로가 아니라 모든 질문에 동일하게 적용되는 증거 완료조건입니다.

### 3. KAC Skill Runtime

`skills/atomic/*`의 각 스킬은 다음 일곱 계약을 유지합니다.

```text
Identity → Goal → Task → Knowledge → Method → Skill → SkillRuntime
```

`skill_compiler.py`는 순서, `derived_from`, handler, 출처 승인, import SHA-256을 검사합니다. `invoke_kac_skill`이 호출되면 `skill_runtime.py`가 실제 handler를 실행하고 독립 `KACSkillRun` JSON을 만듭니다.

### 4. Verifier

Qwen의 문장이라고 바로 공개하지 않습니다.

- 질문 anchor가 관찰됐는가
- 관계 질문의 anchor 사이에 관찰 edge 경로가 있는가
- 원자 스킬이 실제 실행됐는가
- claim이 존재하는 evidence ID만 인용하는가
- claim에 언급한 개념에 해당 evidence가 직접 닿는가
- evidence에 승인 source ref가 있는가
- 한국어 claim인가
- 알려진 개념 혼동이 없는가

모두 통과한 claim text만 이어 붙여 최종 답변으로 만듭니다. Qwen의 자유 서술 `answer`는 초안이며 공개 답변의 권위가 아닙니다.

## DB는 무엇인가

`.state/supestar_agent.sqlite3`가 있지만 이것은 지식 검색 DB가 아닙니다.

- `agent_run`: 최종 run manifest
- `agent_event`: 순서가 있는 행동·Observation 이벤트

지식은 CCS 그래프와 Skill의 Knowledge 계약에 있고, SQLite는 감사와 재현을 위한 실행 기록입니다.

## RAG와 무엇이 다른가

이 Runtime에도 필요한 지식 일부를 가져오는 “retrieval” 성격은 있습니다. 그러나 전형적인 벡터 RAG처럼 유사 문서를 찾아 prompt에 넣고 곧바로 문장을 생성하는 구조로 끝나지 않습니다.

```text
필수 개념 관찰 → 관계 연결 → 스킬 실행 → 새 Observation → 후보 검증
```

핵심 차이는 지식 검색 뒤에 실제 스킬 실행과 상태 변화가 있는 반복 루프입니다. 다만 그래프 자체가 JSON이라는 사실만으로 KAC가 되는 것은 아닙니다. Qwen이 관계와 스킬을 도구로 관찰·실행하고 그 결과를 다음 행동에 사용하기 때문에 KAC Runtime이라고 부릅니다.

## 실제 Scope 증명 run

브라우저에서 다음 질문을 실행했습니다.

> 우리 회사가 소유하고 직접 운영·통제하는 사업장 보일러에서 도시가스를 연소합니다. 이 배출원은 Scope 몇인가요?

주요 사건:

1. `ACTIVITY_DATA`, `OPERATIONAL_BOUNDARY` anchor 발견
2. 두 anchor를 각각 한 번씩 관찰
3. Qwen이 두 anchor를 선택해 실제 CCS 관계 경로 관찰
4. Qwen이 `scope-activity-classification`과 질문 사실 기반 입력 선택
5. 신규 SkillRun 1개 실행, `SCOPE_1 · PROCEED`
6. 첫 후보의 운영 경계 concept 직접 인용 누락을 Verifier가 `REVIEW`
7. 문장·판정 변경 없이 이미 관찰된 `concept:OPERATIONAL_BOUNDARY`만 제한 보완
8. 5단계에서 `PASS`; Local Qwen loopback 검증, 인터넷 사용 `false`

- 최신 회귀 증명 snapshot: [`../proof/validation_scope1_max_steps_fix_live_run.json`](../proof/validation_scope1_max_steps_fix_live_run.json)
- 기존 증명 snapshot: [`../proof/latest_verified_run.json`](../proof/latest_verified_run.json)
- 브라우저 검수 기록: [`../proof/browser_verification.json`](../proof/browser_verification.json)

검증 명령:

```bash
python3 scripts/verify_agent_run.py \
  --run-dir runs/<PASS_RUN_ID>

python3 scripts/verify_locality.py \
  --run-dir runs/<PASS_RUN_ID>
```

두 스크립트 모두 `PASS`했습니다.
