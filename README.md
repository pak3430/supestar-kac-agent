# Supestar KAC Agent

Local Qwen이 CCS 지식을 관찰하고 관계를 확장한 뒤, Stage 1~5에서 파생된 KAC 원자 스킬을 선택·실행하는 **로컬 지식행동사슬 에이전트**입니다.

이 저장소의 핵심은 “지식 JSON을 검색해 문장을 조립하는 챗봇”이 아닙니다. Qwen이 매 turn의 Observation을 보고 다음 행동을 고르고, 선택한 스킬이 실제 Python Runtime에서 실행되며, 검증된 claim만 사용자에게 공개됩니다.

## 현재 구현 상태

`AUTONOMOUS_KAC_AGENT_VERIFIED`

- Local Qwen `qwen2.5:14b-instruct-q4_K_M` 실제 추론
- Ollama loopback 전용 연결과 model digest 기록
- 출처가 연결된 CCS 그래프: 29 nodes / 31 edges
- 실행 가능한 KAC 원자 스킬 6개
- v2 Stage 산출물 60개 바이트 동일성 검증
- 질문별 고정 route map 없음
- 도구 행동·Observation·SkillRun·Verifier 전체 기록
- SQLite 감사 기록과 파일 기반 run evidence
- 실시간 실행 trace 웹 UI
- 자동 테스트 16개, 계약 검증 질문 20개와 실제 브라우저 PASS run

최신 검증 snapshot은 [`proof/latest_verified_run.json`](proof/latest_verified_run.json), 실제 브라우저 검수 기록은 [`proof/browser_verification.json`](proof/browser_verification.json)에 있습니다. 새 질문 은행의 구매 전력 질문을 Local Qwen으로 실행한 Scope 2 PASS 증거는 [`proof/validation_scope2_live_run.json`](proof/validation_scope2_live_run.json)입니다.

## 실제 실행 구조

```text
사용자 질문
  ↓
Local Qwen ── 다음 행동을 선택
  ├─ observe_concept
  ├─ expand_relations
  ├─ invoke_kac_skill
  ├─ request_missing_evidence
  └─ submit_answer_candidate
  ↓
CCS Observation + 실제 KAC SkillRun
  ↓
Local Qwen이 다음 행동 재선택
  ↓
Verifier
  ├─ anchor 관찰 여부
  ├─ 관계 edge 연결 여부
  ├─ SkillRun 존재 여부
  ├─ claim ↔ evidence ↔ source 연결
  └─ 금지된 개념 혼동 검사
  ↓
PASS claim만 최종 답변으로 조립
```

Qwen이 자연어를 반환하고 제출 도구 호출 형식에 실패할 때는 같은 로컬 Qwen의 JSON Schema 출력이 claim 직렬화만 담당합니다. 도메인 행동과 스킬 선택은 그대로 tool-calling Agent loop에서 이루어집니다.

스킬 실행 전 자연어로 이탈하면 같은 로컬 Qwen의 구조화 행동 어댑터가 현재 lifecycle gate에서 허용된 도구 하나를 다시 선택합니다. 런타임은 `관찰 → 스킬 실행 → 관계 확인 → 제출`의 공통 전제만 제한하며, 구체적인 개념·스킬·입력은 Qwen이 선택합니다. 질문별 route map은 만들지 않습니다.

Qwen의 claim 내용은 맞지만 이미 관찰한 개념 evidence ID만 빠진 경우, 검증기가 제안한 정확한 `concept:*` ID만 기계적으로 추가해 다시 검증합니다. 이 복구는 문장·판정·지식을 수정하지 않으며, 미관찰 근거·잘못된 내용·금지된 혼동은 자동 보정하지 않습니다.

## KAC 원자 스킬

| 스킬 | 실제 역할 |
|---|---|
| `esg-carbon-action-path` | ESG에서 측정·Scope·SDGs·시장·산림탄소까지 선행 행동 경로 실행 |
| `scope-activity-classification` | 소유·통제·구매에너지·가치사슬 관계로 Scope 후보 판정 |
| `carbon-market-unit-comparison` | CCM·VCM·배출권·크레딧·상쇄 축 분리 |
| `forest-esg-impact-mapping` | 산림탄소의 E/S/G 근거와 공백 지도 생성 |
| `forest-carbon-procedure-guidance` | 산림탄소 절차의 연속 증거와 다음 단계 판정 |
| `forest-carbon-transaction-readiness` | 외부 거래 없이 11개 준비도 게이트 실행 |

각 스킬은 `Identity → Goal → Task → Knowledge → Method → Skill → SkillRuntime` 7개 계약으로 컴파일됩니다. 가져온 계약의 원본 commit과 SHA-256은 [`provenance/import_manifest.json`](provenance/import_manifest.json)에 기록되어 있습니다.

## 실행하기

요구 사항:

- Python 3.9+
- Ollama 0.32+
- 로컬 모델 `qwen2.5:14b-instruct-q4_K_M`

```bash
cd /path/to/supestar-kac-agent
python3 -m pip install -e .

supestar-agent doctor
supestar-agent serve --port 4177
```

브라우저에서 [http://127.0.0.1:4177](http://127.0.0.1:4177)을 엽니다.

내부 작동 구조를 한 화면으로 설명한 시각화는 [http://127.0.0.1:4177/runtime-visual.html](http://127.0.0.1:4177/runtime-visual.html)에서 볼 수 있습니다.

입력·함수·내부 처리·생성물·다음 단계를 23개 요소로 분해한 상세도는 [http://127.0.0.1:4177/runtime-deep-dive.html](http://127.0.0.1:4177/runtime-deep-dive.html)에서 볼 수 있습니다.

설치 없이 저장소 안에서 실행할 수도 있습니다.

```bash
PYTHONPATH=src python3 -m supestar_kac_agent doctor
PYTHONPATH=src python3 -m supestar_kac_agent serve --port 4177
```

CLI 단일 질문 실행:

```bash
PYTHONPATH=src python3 -m supestar_kac_agent run \
  --input tests/fixtures/esg_carbon_credit.json
```

## 검증하기

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v

# 20개 질문의 anchor·원자 스킬·PROCEED/REVIEW/STOP 계약 일괄 검증
PYTHONPATH=src python3 scripts/validate_question_bank.py

PYTHONPATH=src python3 scripts/verify_import.py \
  --source-root /path/to/supestar_full_kac \
  --target-root "$PWD"

python3 scripts/verify_agent_run.py --run-dir runs/<PASS_RUN_ID>
python3 scripts/verify_locality.py --run-dir runs/<PASS_RUN_ID>
```

실제 검증된 Scope run은 다음을 증명했습니다.

- Qwen이 `OPERATIONAL_BOUNDARY`를 관찰
- `scope-activity-classification`을 직접 선택·실행
- `purchased_energy_type=NONE`으로 현장 연료 연소를 해석
- `OPERATIONAL_BOUNDARY` 개념을 추가 관찰
- 근거가 덜 연결된 후보를 여러 차례 차단
- 최종 Scope 1 claim이 실제 SkillRun을 직접 인용한 뒤에만 `PASS`

웹 화면의 **검증 질문 더 보기**에는 개념 관계, Scope 1·2·3, 탄소시장, 산림탄소, 거래·안전 질문이 들어 있습니다. 원본은 [`validation/question_bank.json`](validation/question_bank.json)입니다. 이 파일은 예시와 회귀 검증 전용이며 Agent 답변 입력이나 질문별 route map으로 사용되지 않습니다.

## 안전 경계와 한계

- 외부 인터넷·검색·원격 LLM API를 사용하지 않습니다.
- 결제·거래·등록부 변경을 실행하지 않습니다.
- SQLite는 지식 DB가 아니라 run 감사 저장소입니다.
- `knowledge/graph.json`은 벡터 RAG 문서 묶음이 아니라 도구로 관찰하는 CCS 환경입니다.
- 현재 그래프는 29개 개념과 31개 관계 범위입니다. 모든 ESG 지식을 안다고 주장하지 않습니다.
- Skill의 `PROCEED`는 코드 실행 완료이지 실제 사회·환경 Outcome의 발생을 뜻하지 않습니다.
- 로컬 14.8B 모델이므로 한 질문에 수 분이 걸릴 수 있습니다.

## 문서

- [`docs/01_PRODUCT_INTENT.md`](docs/01_PRODUCT_INTENT.md)
- [`docs/02_TARGET_ARCHITECTURE.md`](docs/02_TARGET_ARCHITECTURE.md)
- [`docs/03_ACCEPTANCE_GATES.md`](docs/03_ACCEPTANCE_GATES.md)
- [`docs/04_CLEAN_MIGRATION_BOUNDARY.md`](docs/04_CLEAN_MIGRATION_BOUNDARY.md)
- [`docs/05_ACTUAL_RUNTIME_AND_PROOF.md`](docs/05_ACTUAL_RUNTIME_AND_PROOF.md)
- [`docs/06_EXPLAIN_IT_SIMPLY.md`](docs/06_EXPLAIN_IT_SIMPLY.md)
