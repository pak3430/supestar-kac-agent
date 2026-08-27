# Supestar KAC Agent

Local Qwen이 CCS 지식 환경을 관찰하고, 관계를 탐색하고, KAC 원자 Skill을 선택·실행한 뒤 그 결과를 다시 관찰하는 **자율 지식행동사슬 챗봇 v3** 프로젝트입니다.

## 이 저장소가 새로 시작된 이유

기존 `supestar-full-kac` v2는 Stage 파생 Skill의 결정적 실행을 증명했지만, 질문별 키워드 라우터가 실행 경로를 선택했고 LLM은 사용하지 않았습니다. 이 저장소는 다음 목표를 위해 v2 실행 코드를 복사하지 않고 새로 시작합니다.

> 사용자가 탐색 경로를 지정하지 않아도 Local Qwen이 필요한 지식과 Skill을 스스로 선택하고, 실행 결과를 관찰해 다음 행동을 결정하는 과정을 실제로 보여준다.

## 목표 실행 구조

```text
사용자 질문
  → Local Qwen Agent
  → CCS 개념 관찰 / 관계 확장 / KAC Skill 실행
  → Observation
  → Qwen의 다음 행동 선택
  ↺ 반복
  → 근거·경계 검증
  → 최종 답변 + 전체 행동 추적
```

## 현재 상태

`FOUNDATION_READY` — 깨끗한 v3 저장소, 실행 계약, Agent 정책, Ollama/Qwen 사전점검 CLI와 검증 테스트가 준비된 상태입니다.

아직 주장하지 않는 것:

- 완성된 Agent loop
- 전체 CCS 지식 그래프 import
- KAC Skill 도구 등록 완료
- 답변 정확성 또는 해커톤 제출 준비 완료

## 기본 원칙

- 질문별 고정 route map을 만들지 않습니다.
- LLM의 도구 호출은 제안이며, 오케스트레이터가 허용 목록·입력 계약·예산을 검증한 뒤 실행합니다.
- LLM이 지식이나 관계를 새로 주장할 수 없습니다. 관찰된 CCS 노드·관계·Skill 결과만 사용합니다.
- 모든 개념 관찰, 관계 선택, Skill 호출, 결과, 검증 판정을 실행 기록으로 남깁니다.
- 모델 ID·digest·prompt hash·tool call·observation·response hash를 보존합니다.
- 최종 답변과 실제 Outcome을 구분합니다.

## 시작하기

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m supestar_kac_agent doctor \
  --model qwen2.5:14b-instruct-q4_K_M
```

구현 기준은 다음 문서에 있습니다.

- [`docs/01_PRODUCT_INTENT.md`](docs/01_PRODUCT_INTENT.md)
- [`docs/02_TARGET_ARCHITECTURE.md`](docs/02_TARGET_ARCHITECTURE.md)
- [`docs/03_ACCEPTANCE_GATES.md`](docs/03_ACCEPTANCE_GATES.md)
- [`docs/04_CLEAN_MIGRATION_BOUNDARY.md`](docs/04_CLEAN_MIGRATION_BOUNDARY.md)
