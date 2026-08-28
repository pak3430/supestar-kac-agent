# 수용 게이트와 현재 판정

판정 기준일: 2026-08-28

## 구조 게이트

- [x] 질문별 키워드 route map이 없다.
- [x] Local Qwen이 실제 `tool_calls`를 생성한다.
- [x] 등록된 도구만 실행된다.
- [x] CCS 개념·관계와 KAC Skill이 서로 다른 도구 계약이다.
- [x] 모든 실행이 step·tool-call 예산 안에서 종료된다.
- [x] 전체 관계 경로를 탐색 전에 모델에게 제공하지 않는다.
- [x] 결정론적 최단경로는 탐색 종료 뒤 사후 검증에만 사용된다.

## 자율 행동 게이트

- [x] Qwen이 현재 Observation을 보고 다음 행동을 선택한다.
- [x] 같은 질문의 경로를 코드가 미리 지정하지 않는다.
- [x] 검증 실패 뒤 개념 관찰과 1-hop 관계 선택을 추가 수행한다.
- [x] 막힌 분기는 backtrack할 수 있고, 선택하지 않은 edge는 evidence가 되지 않는다.
- [x] 입력 의미가 충돌하면 Skill이 `REVIEW`로 닫힌다.

## 정확성 게이트

- [x] 관계 주장은 실제 관찰된 edge를 가져야 한다.
- [x] 두 anchor의 관계를 말한 claim은 AI가 선택한 전체 edge 경로를 인용해야 한다.
- [x] 도메인 판정은 실제 `KACSkillRun`을 가져야 한다.
- [x] 관계 질문의 SkillRun은 완료된 traversal hash와 일치해야 한다.
- [x] claim마다 evidence와 source ref가 연결된다.
- [x] CCM·VCM·Scope·크레딧·배출권 혼동을 차단한다.
- [x] 근거 밖의 내용은 최종 답변으로 공개하지 않는다.
- [x] 자유 서술 초안이 아니라 검증된 claim text만 공개한다.

## 증명 게이트

- [x] Ollama version, model ID, digest가 기록된다.
- [x] prompt hash, tool arguments, Observation, answer hash가 기록된다.
- [x] endpoint가 `127.0.0.1` loopback으로 강제된다.
- [x] 웹 화면이 외부 JS·CSS·미디어를 요청하지 않는다.
- [x] 새 단계별 traversal을 실제 Local Qwen에서 확인한다.
- [x] 새 run 파일·Traversal Ledger·SkillRun·SQLite를 독립 스크립트로 대조한다.

## 기존 Runtime 검증 run

- Run ID: `agent-run-1550aabc-1b7b-469e-a5f8-a5b14717a092`
- 질문: 소유·통제 사업장 보일러 도시가스 연소의 Scope 분류
- Local model: `qwen2.5:14b-instruct-q4_K_M`
- 실행 스킬: `scope-activity-classification`
- 최종 판정: `PASS`
- 최종 답변: Scope 1
- 검증 evidence: `skill:skill-run-8bfe89a2-194c-4077-8eda-987ebb02dc1b`, `concept:OPERATIONAL_BOUNDARY`
- source refs: `ghg-protocol-corporate-standard-faq`, `supestar-stage-v2-contract-import`

이 snapshot들은 단계별 AI 관계 추적 개편 전 Runtime의 회귀 증거입니다. 재현 가능한 비밀정보 없는 기존 증명 snapshot은 [`../proof/latest_verified_run.json`](../proof/latest_verified_run.json)에 있습니다.

ESG–탄소크레딧 관계의 역방향 edge 처리, 신뢰 요청 문맥 결합, 실제 SkillRun과 전체 경로 인용까지 검증한 최신 회귀 증거는 [`../proof/validation_esg_carbon_credit_handler_fix_live_run.json`](../proof/validation_esg_carbon_credit_handler_fix_live_run.json)에 있습니다.

## 단계별 AI 관계 추적 검증 run

- Run ID: `agent-run-89720b1c-e30a-4656-9f7e-a46d522faa40`
- 질문: ESG 관점에서 탄소크레딧과 어떤 상관관계가 있는지
- Local model: `qwen2.5:14b-instruct-q4_K_M`
- Qwen 선택 경로: `CARBON_CREDIT → FOREST_CARBON_PROJECT → SDG_13 → SDGS → ESG`
- 최종 활성 경로: `CARBON_CREDIT → FOREST_CARBON_PROJECT → SDG_13 → SDGS → ESG`
- 사전 전체 경로 계산: `false`
- pathfinder 역할: `POST_HOC_VALIDATION_ONLY`
- 실행 스킬: `esg-carbon-action-path`
- SkillRun–Traversal hash: 일치
- 이벤트: 54개, Local Qwen inference 11회
- 관계 claim: 두 anchor를 한 문장에서 연결하고 활성 4-edge 전체 인용
- 최종 판정: `PASS`
- 인터넷 사용: `false`

독립 증명 snapshot은 [`../proof/validation_agentic_relation_traversal_live_run.json`](../proof/validation_agentic_relation_traversal_live_run.json)에 있습니다.

## 아직 주장하지 않는 것

- 모든 ESG 질문에 대한 완전성
- 법적·회계적 최종 판정
- 외부 거래 또는 등록부 변경 능력
- 실제 환경 Outcome 발생
- 인터넷 단절 OS 환경 전체에 대한 네트워크 패킷 캡처

현재 완료 문구는 `AGENTIC_RELATION_TRAVERSAL_VERIFIED`입니다. 이는 실제 Local Qwen의 1-hop 선택·Skill provenance·강화된 관계 Verifier를 검증했고 backtrack 동작은 자동 테스트로 검증했다는 뜻이며, “모든 도메인에 일반화된 완전 자율 AGI”를 뜻하지 않습니다.
