# AI 단계별 관계 추적 개편의 의미와 판단 기준

부제: 최단 경로 계산과 지식행동사슬형 자율 탐색의 차이

- 작성자: 제시아플랫폼
- 작성일: 2026.08.28
- 문서 유형: 내부 구조 판단서
- 적용 대상: Supestar KAC Agent

---

## 1. 문서 목적

이 문서는 개편 전 수페스타의 관계 처리 방식과, 2026-08-28 구현된 `AI가 한 단계씩 지식을 관찰하고 다음 관계를 선택하는 방식`의 기술적·개념적·시연적 차이를 과장 없이 규정한다.

핵심 질문은 다음과 같다.

> `ESG → SDGs → SDG 13 ↔ 산림탄소 프로젝트 → 탄소크레딧`이라는 경로를 AI가 스스로 구성한 것인가, 아니면 그래프 알고리즘이 계산한 것인가?

## 2. 전체 결론

개편 전 경로는 질문별로 하드코딩되지는 않았지만, 중간 노드와 edge의 연속 경로를 그래프의 `shortest_path()`가 한 번에 계산했다. 당시 구조를 `AI가 중간 지식을 하나씩 판단해 스스로 경로를 만들었다`고 설명하면 과장이었다.

현재 구현은 각 지식 이동을 `AI Action → CCS Observation`으로 분리한다. Qwen은 현재 node의 1-hop 후보만 보고 edge 하나를 선택하며, 선택의 연속으로 경로를 구성한다. 전체 경로는 탐색 완료 전 모델에게 제공되지 않는다.

다만 정확성과 속도만 보면 현재 알고리즘 방식이 더 유리할 수 있다. 본 프로젝트처럼 `지식이 AI의 다음 행동을 발생시키는 과정` 자체를 증명하려는 경우에는 단계별 개편의 의미가 크다.

> **구현 결과:** AI가 1-hop 관계를 선택하고, 결정론적 그래프 알고리즘은 답을 제공하는 탐색기가 아니라 사후 검증기와 안전장치로 남는 하이브리드 구조를 채택했다.

## 3. 개편 전 Runtime의 사실

### 3.1 개편 전 실제 동작

```text
사용자 질문
  ↓
코드가 질문 문자열에서 anchor 후보 탐지
  ↓
Lifecycle Gate가 현재 단계에서 허용할 도구 제한
  ↓
Local Qwen이 ESG 관찰과 탄소크레딧 관찰 선택
  ↓
Local Qwen이 ESG에서 탄소크레딧 방향의 관계 확장 선택
  ↓
그래프 shortest_path()가 전체 최단 경로를 한 번에 계산
  ↓
Local Qwen이 KAC Skill 선택·실행
  ↓
SkillRun Observation을 보고 답변 후보 생성
  ↓
Verifier 통과 claim만 공개
```

실제 회귀 run에서 관계 도구가 반환한 경로는 다음과 같다.

```text
ESG → SDGs → SDG 13 ↔ 산림탄소 프로젝트 → 탄소크레딧
```

### 3.2 개편 전 AI가 선택한 것과 선택하지 않은 것

| 구분 | 현재 실제 주체 | 설명 |
|---|---|---|
| 질문의 anchor 후보 탐지 | 코드 | 그래프 alias의 문자열 포함 여부로 탐지한다. |
| 현재 단계의 도구 종류 | Lifecycle Gate | 관찰·관계·Skill·제출의 공통 순서를 제한한다. |
| 관찰 도구 호출 | Local Qwen | 허용된 도구 안에서 호출을 생성한다. |
| 관계 탐색 방향과 목적 | Local Qwen | `ESG`에서 `CARBON_CREDIT` 방향으로 확장하도록 요청한다. |
| 중간 node·edge 선택 | 그래프 알고리즘 | BFS 기반 `shortest_path()`가 전체 경로를 계산한다. |
| 실행할 KAC Skill | Local Qwen | 관찰된 개념에 적용 가능한 Skill 중 하나를 선택한다. |
| 업무 판정 | KAC Skill Runtime | Python handler가 계약과 규칙에 따라 실행한다. |
| 최종 설명 후보 | Local Qwen | Observation과 SkillRun을 근거로 claim을 생성한다. |
| 공개 여부 | Verifier | 개념·관계·SkillRun·출처가 연결된 claim만 승인한다. |

### 3.3 개편 전 구조의 정확한 명칭

개편 전 구조를 가장 정확하게 표현하면 다음과 같았다.

> Local Qwen이 탐색 목표·도구·Skill을 선택하고, 결정론적 그래프 알고리즘이 관계 경로를 계산하는 제한형 KAC Agent Runtime

개편 전에도 벡터 RAG나 고정 답변 mock은 아니었고 질문별 정답 route map도 없었다. 그러나 경로의 중간 선택까지 AI 행동이라고 주장할 수는 없었다.

## 4. 단계별 AI 관계 추적의 의미

### 4.1 Knowledge, Action, Chain의 분리

| 구성 | 의미 | 실행 예시 |
|---|---|---|
| Knowledge | 현재까지 관찰된 개념·관계·Skill 결과 | ESG 정의, ESG에 연결된 1-hop 관계 |
| Action | 다음에 관찰하거나 실행할 대상을 선택하는 행위 | SDGs 선택, SDG 13 선택, Skill 실행 |
| Observation | Action 결과로 새롭게 드러난 사실 | SDGs가 SDG 13을 포함한다는 edge |
| Chain | 앞선 Observation이 다음 Action의 원인이 되는 연속 기록 | ESG 관찰 → SDGs 선택 → SDG 13 선택 |

개편 전 방식은 관계 Action 한 번에 전체 경로가 Observation으로 들어왔다.

```text
expand_relations 1회
  → ESG부터 탄소크레딧까지 전체 경로 반환
```

현재 구현에서는 다음처럼 각 전이가 독립 행동이 된다.

```text
ESG 관찰
  → Qwen이 SDGs 선택
SDGs 관찰
  → Qwen이 SDG 13 선택
SDG 13 관찰
  → Qwen이 산림탄소 프로젝트 선택
산림탄소 프로젝트 관찰
  → Qwen이 탄소크레딧 선택
```

이 차이는 단순한 화면 연출이 아니라, `누가 경로를 결정했는가`라는 실행 책임의 변화다.

### 4.2 AI가 스스로 했다고 말할 수 있는 범위

현재 구현에서도 AI가 새로운 ESG 지식을 창조하는 것은 아니다. CCS에 존재하는 지식 중 질문에 필요한 관계를 선택하여 경로를 구성한다.

정확한 표현은 다음과 같다.

> AI가 구조화된 CCS 지식 환경 안에서 현재 Observation만을 바탕으로 다음 관계를 선택하고, 그 선택의 연속으로 질문에 필요한 지식 경로를 구성했다.

다음 표현은 개편 후에도 피해야 한다.

- AI가 세상에 없던 새로운 ESG 지식을 발견했다.
- AI가 아무런 구조나 제약 없이 완전히 자유롭게 사고했다.
- CCS와 Runtime의 도움 없이 LLM 자체가 모든 관계를 증명했다.

## 5. 개편으로 얻는 실질적 가치

### 5.1 경로 형성의 주체가 AI로 이동

현재는 AI가 목적지를 지정하고 알고리즘이 경로를 만든다. 개편 후에는 AI가 매 단계에서 다음 node·edge를 선택한다. 따라서 최종 경로가 알고리즘 출력이 아니라 AI 행동 기록의 결과가 된다.

### 5.2 지식이 다음 행동을 발생시키는 구조

이전 단계의 Observation이 다음 행동 선택에 실제로 영향을 준다.

```text
ESG의 1-hop 관계 관찰
  ↓
기후행동과 가까운 SDGs 선택
  ↓
SDGs 관계 관찰
  ↓
SDG 13 선택
```

이 구조는 `질문 → 검색 → 문장 생성`으로 끝나는 일반적인 RAG와 구별되는 핵심 근거가 된다.

### 5.3 재사용과 조합 가능성

질문별 경로 코드를 추가하지 않고 같은 1-hop 탐색 계약을 여러 관계 질문에 적용할 수 있다.

- ESG와 산림탄소의 관계
- SDGs와 탄소크레딧의 관계
- 운영 경계와 Scope의 관계
- 산림탄소와 지역사회 기여의 관계

질문이 달라지면 AI의 선택 경로와 Skill도 달라질 수 있다.

### 5.4 감사 가능성과 실패 위치 확인

각 선택을 실행 기록으로 남기면 다음을 확인할 수 있다.

- 해당 단계에서 AI가 본 후보 관계
- AI가 실제 선택한 edge
- 선택의 짧고 검증 가능한 목적
- 새롭게 받은 Observation
- 되돌아가기 또는 중단 여부
- Skill을 호출하게 된 직접 선행 근거

경로가 끊기면 어느 지식 연결에서 근거가 부족했는지도 확인할 수 있다.

### 5.5 해커톤 시연의 차별성

최종 답변만 보여주는 챗봇과 달리, 다음 구조를 실시간으로 보여줄 수 있다.

```text
현재 지식 → AI의 다음 행동 → 새 지식 → Skill 실행 → 검증된 답변
```

이는 `CCS를 왜 구조화하는가`, `Skill을 왜 묶는가`, `왜 Runtime에 배포하는가`를 하나의 실행 장면으로 설명할 수 있게 한다.

## 6. 비용과 위험

| 위험 | 의미 | 필요한 대응 |
|---|---|---|
| LLM 호출 증가 | edge마다 판단하면 응답 시간이 증가한다. | 단순 질문 fast path, 관계 질문 traversal mode 분리 |
| 확률적 경로 | 같은 질문에서도 다른 유효 경로가 나올 수 있다. | 경로 유효성·출처·Skill 적합성 검증 |
| 순환 탐색 | 이미 본 node로 반복 이동할 수 있다. | visited set, edge 중복 금지, 단계 예산 |
| 불필요한 우회 | 관련성이 낮은 긴 경로를 선택할 수 있다. | 최대 깊이, 관련성 점수, 사후 최단 경로 비교 |
| 없는 관계 생성 | LLM이 관찰하지 않은 edge를 주장할 수 있다. | 관찰된 edge ID만 실행·인용 가능하도록 제한 |
| 로컬 모델 성능 | 14.8B 모델이 복잡한 분기에서 흔들릴 수 있다. | 선택 스키마, 짧은 목적 필드, backtrack 지원 |
| 시연용 자율성 연출 | 실질적 선택 없이 유일한 경로를 고르게 할 수 있다. | 복수 후보·방해 관계·순서 무작위화 검증 |

단계별 추적은 개편 전 방식보다 자동으로 더 정확한 것이 아니다. 목표가 빠른 정답 제공뿐이라면 결정론적 최단 경로가 더 효율적일 수 있다.

## 7. 의미 없는 ‘자율성 연출’의 판별 기준

다음 구조는 AI가 관계를 선택한 것처럼 보여도 실질적인 자율 탐색이 아니다.

- 현재 단계에서 선택 가능한 edge가 사실상 하나뿐이다.
- 전체 경로 또는 정답 node가 prompt에 미리 제공된다.
- Qwen의 선택과 무관하게 서버가 다음 node를 고정한다.
- 잘못된 방향을 선택할 가능성과 중단·되돌아가기 행동이 없다.
- 화면에는 여러 단계가 표시되지만 내부에서는 전체 경로를 먼저 계산해 놓는다.

실질적 선택으로 인정하려면 다음 조건이 필요하다.

1. AI에는 현재 node의 1-hop 관계만 제공한다.
2. 두 개 이상의 유효하거나 경쟁적인 관계가 존재할 수 있다.
3. 전체 경로를 탐색 완료 전까지 AI에게 제공하지 않는다.
4. 다음 node는 현재 Observation을 받은 뒤에만 선택한다.
5. 선택한 edge가 실제 CCS에 존재해야 실행된다.
6. 막힌 경로에서는 backtrack 또는 근거 부족 중단을 수행할 수 있다.
7. 최종 경로는 실제 Action log로만 재구성한다.

## 8. 구현된 목표 구조

### 8.1 역할 분리

```text
사용자 질문
  ↓
Anchor Candidate Detector
후보만 제시하며 경로는 제시하지 않음
  ↓
Local Qwen Traversal Agent
현재 node의 1-hop 관계를 보고 다음 edge 선택
  ↓
Traversal Ledger
선택 node·edge·목적·Observation·visited 상태 기록
  ↓
KAC Skill Resolver / Runtime
형성된 지식 경로와 질문 목적을 보고 Skill 선택·실행
  ↓
Verifier
실제 CCS edge, 출처, SkillRun, claim 연결 검증
  ↓
최종 답변
```

### 8.2 `shortest_path()`의 구현된 역할

최단 경로 알고리즘을 삭제하지 않고 AI에게 답을 알려주지 않는 내부 안전장치로 전환한다.

| 현재 역할 | 권장 역할 |
|---|---|
| AI에게 전체 후보 경로 반환 | 탐색 완료 후 경로 유효성 비교 |
| 실제 탐색 경로 결정 | 과도한 우회·단절 검사 |
| 중간 node 자동 선택 | 테스트 기준선과 회귀 검증 |

AI는 1-hop 관계를 선택하고, `shortest_path()`는 AI가 만든 경로가 실제 그래프에서 연결되는지 사후 확인한다.

### 8.3 필요한 도구 계약

| 도구 | 역할 |
|---|---|
| `observe_concept` | 현재 개념의 정의·경계·출처 관찰 |
| `observe_neighbors` | 현재 node의 1-hop edge 후보만 반환 |
| `select_relation_step` | Qwen이 다음 edge·node와 짧은 목적을 제출 |
| `backtrack_relation_step` | 막힌 경로에서 이전 상태로 복귀 |
| `stop_relation_traversal` | 경로 완성 또는 근거 부족으로 탐색 종료 |
| `invoke_kac_skill` | 형성된 지식 경로에 따라 원자 Skill 실행 |
| `submit_answer_candidate` | 실행 결과를 근거 ID와 함께 제출 |

내부 장문의 chain-of-thought를 저장하지 않는다. 각 행동에는 `선택 목적`, `사용한 Observation ID`, `선택한 edge ID`처럼 짧고 감사 가능한 정보만 기록한다.

## 9. 진짜 단계별 탐색을 증명하는 검증

### 9.1 전체 경로 비공개 검사

탐색 완료 전 prompt·도구 Observation·정책 파일에 전체 정답 경로가 존재하지 않아야 한다.

### 9.2 관계 순서 무작위화 검사

동일한 1-hop 후보의 표시 순서를 바꿔도 의미적으로 유효한 경로를 선택해야 한다. 항상 첫 번째 항목을 고르면 의미 판단으로 인정하지 않는다.

### 9.3 방해 관계 검사

ESG의 환경·사회·지배구조·경영·SDGs 관계를 함께 제공하고, 탄소크레딧 질문에서 관련 기후행동 방향을 선택하는지 확인한다.

### 9.4 질문 바꿔쓰기 검사

다음처럼 표현을 바꿔도 같은 정답 문자열이 아니라 유효한 관계사슬을 새로 구성하는지 확인한다.

- ESG와 탄소크레딧은 어떤 관계인가요?
- 기업의 지속가능성 활동은 탄소크레딧과 어떻게 이어지나요?
- 기후행동의 성과가 탄소 단위로 이어지는 과정을 설명해 주세요.

### 9.5 역방향 질문 검사

`탄소크레딧은 ESG 활동과 어떻게 관련됩니까?`처럼 출발점과 목표가 뒤바뀐 질문에서도 관찰된 edge만 사용해 유효한 경로를 구성해야 한다.

### 9.6 edge 제거 검사

검증 환경에서 특정 edge를 제거했을 때 다음을 확인한다.

- 제거된 관계를 만들어내지 않는다.
- 다른 유효 경로가 있으면 다시 탐색한다.
- 경로가 없으면 근거 부족으로 중단한다.

### 9.7 반복·막힘 검사

- 같은 node·edge를 무한 반복하지 않는다.
- 최대 깊이와 도구 예산 안에서 종료한다.
- 막힌 경우 `backtrack` 또는 `STOP`이 실행 기록에 남는다.

### 9.8 Skill 발생 원인 검사

Skill 호출이 질문 키워드만으로 발생한 것이 아니라, 탐색 중 관찰한 개념·관계와 직접 연결돼야 한다. SkillRun에는 선행 traversal edge ID가 기록되어야 한다.

## 10. 수용 기준

개편 완료는 화면에 단계가 여러 개 표시되는 것으로 판정하지 않는다. 다음 조건을 모두 확인해야 한다.

- [x] 전체 경로가 사전에 Qwen에게 제공되지 않는다.
- [x] Qwen은 매 turn 현재 node의 1-hop 관계만 본다.
- [x] 각 node 이동마다 독립 Action과 Observation이 기록된다.
- [x] 선택한 edge는 실제 CCS graph에 존재한다.
- [x] 최종 경로는 Action log로 재구성할 수 있다.
- [x] 관찰하지 않은 edge와 node는 최종 claim에 사용할 수 없다.
- [x] 경로가 없으면 환각하지 않고 backtrack 또는 STOP한다.
- [x] SkillRun은 실제 traversal hash와 연결된다.
- [x] 결정론적 경로 알고리즘은 탐색기가 아니라 사후 검증기로만 사용된다.
- [x] 관계 순서 변경·방해 관계·역방향·edge 제거 시험을 자동 테스트로 통과한다.
- [x] 실제 Local Qwen model identity와 모든 새 traversal 도구 호출을 run evidence로 보존한다.
- [x] 실제 Local Qwen run에서 인터넷과 원격 LLM 없이 동일 구조를 재현한다.

## 11. 적용 판단

| 프로젝트 목표 | 단계별 AI 추적의 필요성 |
|---|---|
| 빠르고 안정적인 ESG 답변 | 반드시 필요하지 않음 |
| 일반적인 안내 챗봇 | 개편 전 방식으로도 구현 가능 |
| RAG와 다른 실행 구조 증명 | 필요성이 높음 |
| AI가 지식을 행동으로 연결하는 과정 증명 | 핵심 요구사항 |
| Skill이 선택·실행되는 발생 과정 증명 | 매우 중요 |
| 해커톤 시연 차별화 | 강한 가치가 있음 |
| 실제 운영 서비스 | fast path와 안전한 traversal mode 병행 권장 |

본 프로젝트의 목적은 단순 ESG 설명 챗봇이 아니라, 구조화된 지식이 AI의 행동을 발생시키고 그 행동이 Skill 실행과 답변으로 이어지는 과정을 보여주는 것이다. 이 목적을 기준으로 하면 단계별 AI 관계 추적 개편은 진행 가치가 충분하다.

## 12. 최종 정의

개편 전:

> AI가 탐색 목표를 정하고, 그래프 알고리즘이 전체 경로를 계산하는 KAC Agent

현재 구현:

> AI가 현재 관찰한 CCS 지식만을 바탕으로 다음 관계를 한 단계씩 선택하고, 그 행동사슬로 형성된 경로에 따라 원자 Skill을 실행하며, 결정론적 Verifier가 전체 실행 근거를 검사하는 KAC Agent

## 13. 근거 파일

1. [`../src/supestar_kac_agent/graph.py`](../src/supestar_kac_agent/graph.py) — anchor 탐지, 1-hop 관계, BFS 최단 경로 구현
2. [`../src/supestar_kac_agent/agent.py`](../src/supestar_kac_agent/agent.py) — Lifecycle Gate, Local Qwen Agent loop, Tool Action 처리
3. [`../src/supestar_kac_agent/agent_tools.py`](../src/supestar_kac_agent/agent_tools.py) — 관계 탐색·Skill 실행 도구 계약
4. [`../src/supestar_kac_agent/skill_runtime.py`](../src/supestar_kac_agent/skill_runtime.py) — KAC Skill 실제 실행과 SkillRun 생성
5. [`../src/supestar_kac_agent/verifier.py`](../src/supestar_kac_agent/verifier.py) — 관찰 관계·SkillRun·claim 검증
6. [`../proof/validation_esg_carbon_credit_handler_fix_live_run.json`](../proof/validation_esg_carbon_credit_handler_fix_live_run.json) — 실제 Local Qwen ESG–탄소크레딧 PASS run 증거
7. [`../proof/validation_agentic_relation_traversal_live_run.json`](../proof/validation_agentic_relation_traversal_live_run.json) — 실제 Local Qwen의 1-hop 선택·Skill provenance·강화된 관계 Verifier PASS 증거
