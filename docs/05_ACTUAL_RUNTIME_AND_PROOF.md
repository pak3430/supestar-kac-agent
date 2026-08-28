# 실제 Runtime과 증명

## 무엇이 실제로 실행되는가

### 1. Local Qwen

Ollama의 `qwen2.5:14b-instruct-q4_K_M`이 다음 행동을 선택합니다.

- 관찰할 개념
- 현재 node에서 선택할 실제 1-hop 관계
- 막힌 경우 backtrack 또는 근거 부족 STOP
- 실행할 KAC 원자 스킬
- 스킬의 도메인 입력
- 답변 후보와 evidence ID

연결 주소는 `http://127.0.0.1:11434`만 허용됩니다. 비-loopback endpoint는 코드가 거부합니다.

원 질문·사용자 역할·기준일은 Qwen이 추측하거나 바꾸는 도메인 입력이 아닙니다. 서버가 요청 봉투의 신뢰 값으로 Skill 호출에 결합하고, 그 결합 사실도 event로 남깁니다.

### 2. CCS Knowledge Environment

`knowledge/graph.json`에는 개념 정의만 있는 것이 아니라 방향성 edge, 관계 이유, 적용 가능한 스킬, source ref가 있습니다.

Qwen은 그래프 파일 전체나 목표까지의 경로를 받지 않습니다. `observe_concept`로 anchor를 보고, `observe_neighbors`로 현재 node의 직접 1-hop 관계만 Observation으로 받습니다. 그 뒤 `select_relation_step`으로 실제 edge 하나를 선택해야만 새 node로 이동합니다.

런타임의 공통 lifecycle gate는 필수 anchor 관찰, 1-hop 관계 선택 반복, 관계 탐색 종료, Skill 실행, 후보 제출 순서를 관리합니다. 다음 edge·backtrack·Skill·입력은 Qwen이 고릅니다. 후보 edge는 근거가 아니며 실제 선택된 edge만 Traversal Ledger와 evidence에 들어갑니다. 최단경로 함수는 탐색 완료 뒤 AI 경로의 단절과 길이를 사후 확인할 뿐 중간 node를 알려주지 않습니다.

### 3. KAC Skill Runtime

`skills/atomic/*`의 각 스킬은 다음 일곱 계약을 유지합니다.

```text
Identity → Goal → Task → Knowledge → Method → Skill → SkillRuntime
```

`skill_compiler.py`는 순서, `derived_from`, handler, 출처 승인, import SHA-256을 검사합니다. `invoke_kac_skill`이 호출되면 `skill_runtime.py`가 실제 handler를 실행하고 독립 `KACSkillRun` JSON을 만듭니다. 관계 질문에서는 완료된 AI traversal의 node·edge·선택 목적·hash가 SkillRun provenance에 함께 저장됩니다.

### 4. Verifier

Qwen의 문장이라고 바로 공개하지 않습니다.

- 질문 anchor가 관찰됐는가
- 관계 질문의 anchor 사이에 AI가 실제 선택한 edge 경로가 있는가
- 관계를 말한 개별 claim이 양쪽 anchor 사이의 전체 선택 edge 경로를 인용하는가
- SkillRun의 traversal hash가 현재 Ledger와 일치하는가
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
필수 개념 관찰 → 1-hop 관계 선택 반복 → Skill 실행 → 새 Observation → 후보 검증
```

핵심 차이는 지식 검색 뒤에 실제 관계 선택과 스킬 실행이라는 상태 변화가 이어지는 반복 루프입니다. 그래프 자체가 JSON이라는 사실만으로 KAC가 되는 것은 아닙니다. Qwen의 현재 Observation이 다음 edge Action을 만들고, 그 행동사슬이 Skill 실행 provenance와 답변 근거로 이어지기 때문에 KAC Runtime이라고 부릅니다.

## 단계별 AI 관계 추적 실제 증명 run

실제 Local Qwen에 다음 질문을 입력했습니다.

> ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?

실제 행동은 다음과 같았습니다.

1. `CARBON_CREDIT`, `ESG` anchor를 관찰했습니다.
2. Qwen은 `CARBON_CREDIT`에서 현재 1-hop 후보만 받고 `edge:forest:credit` 하나를 선택했습니다.
3. 이어서 새 node의 1-hop 관찰을 바탕으로 `edge:forest:sdg13` → `edge:sdgs:13` → `edge:esg:sdgs`를 turn마다 하나씩 선택했습니다.
4. AI 선택 활성 경로가 두 anchor를 연결한 뒤에만 탐색 종료가 승인됐습니다.
5. 종료 뒤 처음으로 `shortest_path()` 사후 비교가 수행됐고, 활성 경로 4-edge와 기준선 4-edge가 일치했습니다.
6. Qwen이 선택한 `esg-carbon-action-path`가 동일 traversal hash로 실행됐습니다.
7. 같은 Local Qwen이 compact evidence를 claim JSON으로 직렬화했습니다.
8. 두 차례 `REVIEW` 뒤 Qwen이 ESG와 탄소크레딧을 한 claim에서 직접 연결했습니다.
9. 런타임은 문장을 작성하지 않고 그 claim에 이미 관찰된 활성 경로·SkillRun 근거 ID만 제한적으로 보완했으며, 강화된 Verifier가 `PASS`했습니다.

검증 결과:

- Run ID: `agent-run-89720b1c-e30a-4656-9f7e-a46d522faa40`
- Event: 54개
- Qwen inference: 11회, 누적 약 370.2초
- 선택 이력: 4 edge, backtrack 0회
- 최종 활성 경로: 4 edge
- `full_path_precomputed_for_agent=false`
- `pathfinder_role=POST_HOC_VALIDATION_ONLY`
- Skill: `esg-carbon-action-path`
- SkillRun traversal hash: Ledger와 일치
- Local endpoint: `127.0.0.1:11434`
- 인터넷 사용: `false`
- 최종 Verifier: `PASS`

backtrack 구현은 별도 자동 테스트에서 막힌 분기 복귀, 폐기 edge의 활성 경로 제외, 다른 분기 재선택까지 검증합니다. 이 최종 실측 run 자체는 첫 분기로 유효한 산림탄소 경로를 골랐기 때문에 backtrack이 발생하지 않았습니다.

독립 검증 snapshot은 [`../proof/validation_agentic_relation_traversal_live_run.json`](../proof/validation_agentic_relation_traversal_live_run.json)에 있습니다.

## 기존 Runtime의 실제 Scope 증명 run

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

## 기존 Runtime의 ESG–탄소크레딧 오류 회귀 run

다음 질문에서 과거에는 `grounded ESG action path is unavailable` 예외가 화면에 노출됐습니다.

> ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?

수정 후 실제 Local Qwen run은 다음 순서로 완료됐습니다.

1. `ESG`, `CARBON_CREDIT` anchor를 각각 관찰
2. `ESG → SDGs → SDG 13 ↔ 산림탄소 프로젝트 → 탄소크레딧` 경로 관찰
3. 역방향으로 저장된 유효 산림탄소 관계를 양방향 fallback으로 연결
4. 모델이 빠뜨린 `userRole`, `asOfDate`를 요청 봉투의 `LEARNER`, `2026-08-28`로 결합
5. `esg-carbon-action-path` SkillRun 1개 실제 실행, 시장판단 선행 근거 부족을 `REVIEW`로 관찰
6. Qwen 후보가 관계 전체 경로 중 일부만 인용해 Verifier가 차단
7. 문장·판정은 바꾸지 않고 이미 관찰된 나머지 edge만 추가한 뒤 `PASS`

- 회귀 증명 snapshot: [`../proof/validation_esg_carbon_credit_handler_fix_live_run.json`](../proof/validation_esg_carbon_credit_handler_fix_live_run.json)
- Local Qwen: `qwen2.5:14b-instruct-q4_K_M`
- Skill: `esg-carbon-action-path`
- 최종 상태: `PASS`
- 인터넷 사용: `false`

검증 명령:

```bash
python3 scripts/verify_agent_run.py \
  --run-dir runs/<PASS_RUN_ID>

python3 scripts/verify_locality.py \
  --run-dir runs/<PASS_RUN_ID>
```

기존 run에서는 두 스크립트가 `PASS`했습니다. 새 독립 검증기는 `relation_traversal.json`, `full_path_precomputed_for_agent=false`, 사후 pathfinder 역할, SkillRun traversal hash까지 추가로 검사합니다.
