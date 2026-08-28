# 구현된 아키텍처

```text
┌──────────────────┐
│ 사용자 질문       │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Local Qwen Planner│
│ 다음 도구 선택     │
└────────┬─────────┘
         ▼
┌─────────────────────────────────────────────┐
│ Evidence lifecycle gate                     │
│ anchor 관찰 → 1-hop 선택 반복 → Skill → 제출 │
└────────┬────────────────────────────────────┘
         ▼
┌─────────────────────────────────────────────┐
│ 허용된 도구 레지스트리                       │
│ observe_concept · observe_neighbors          │
│ select_relation_step · backtrack_relation_step│
│ stop_relation_traversal                      │
│ invoke_kac_skill · request_missing_evidence  │
│ submit_answer_candidate                      │
└────────┬────────────────────────────────────┘
         ▼
┌──────────────────┐     ┌────────────────────┐
│ CCS 지식·관계 환경│     │ KAC 원자 Skill Runtime│
└────────┬─────────┘     └─────────┬──────────┘
         └──────────┬───────────────┘
                    ▼
             Observation 반환
                    │
                    └──────→ Local Qwen Planner 반복
                                   │
                                   ▼
                         근거·경계 검증기
                                   │
                    ┌──────────────┴─────────────┐
                    ▼                            ▼
              최종 답변 승인                보완/중단
```

## 책임 분리

| 구성요소 | 책임 | 하지 않는 것 |
|---|---|---|
| Local Qwen Planner | 다음 관찰·Skill 행동 선택 | 출처·관계·판정 임의 생성 |
| CCS 환경 | 현재 node의 1-hop 개념·관계·출처 제공 | 전체 경로·질문별 경로 지정 |
| Traversal Ledger | Qwen이 선택한 edge·node·backtrack·경로 hash 기록 | 다음 edge 대신 결정 |
| KAC Skill Runtime | 구조화된 도메인 행동 실행 | 자유문장으로 규칙 변경 |
| Orchestrator | 공통 증거 단계·호출 검증·예산·중복 방지·기록 | 질문별 도메인 정답 경로 하드코딩 |
| Verifier | 관찰 근거·anchor·claim 검사 | 답변 대신 생성 |
| Renderer | 검증된 결과 표시 | 내부 추론 전체 공개 |

## 출력 직렬화 fallback

Qwen이 도구 호출 대신 자연어를 반환할 수 있습니다. anchor 관찰·관계·SkillRun 전제가 충족된 경우에만 같은 Local Qwen을 JSON Schema 모드로 한 번 또는 두 번 호출해 claim 구조로 직렬화합니다.

이 단계는 새 도메인 판단을 수행하지 않습니다. 이미 관찰된 evidence catalog만 입력으로 받고, Verifier가 다시 모든 claim을 검사합니다. 자유 서술 초안은 최종 답변으로 직접 공개되지 않습니다.

## 핵심 차이

v2의 `질문 키워드 → 고정 route`를 제거했습니다. 현재 Runtime에서는 Qwen이 매 turn 현재 node의 1-hop 관계만 받고 실제 edge 하나를 선택합니다. 선택된 edge만 증거가 되며, 후보로 보기만 한 edge는 답변에 사용할 수 없습니다. Qwen이 anchor를 연결하고 탐색을 종료하면 그 관계사슬 hash가 SkillRun에 결합됩니다. 오케스트레이터는 모든 질문에 같은 증거 lifecycle과 실행 예산을 적용하고, 결정론적 최단경로는 종료 뒤 사후 비교에만 사용합니다.
