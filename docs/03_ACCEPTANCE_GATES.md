# 수용 게이트와 현재 판정

판정 기준일: 2026-08-27

## 구조 게이트

- [x] 질문별 키워드 route map이 없다.
- [x] Local Qwen이 실제 `tool_calls`를 생성한다.
- [x] 등록된 도구만 실행된다.
- [x] CCS 개념·관계와 KAC Skill이 서로 다른 도구 계약이다.
- [x] 모든 실행이 step·tool-call 예산 안에서 종료된다.

## 자율 행동 게이트

- [x] Qwen이 현재 Observation을 보고 다음 행동을 선택한다.
- [x] 같은 질문의 경로를 코드가 미리 지정하지 않는다.
- [x] 검증 실패 뒤 개념 관찰과 관계 확장을 추가 수행한다.
- [x] 입력 의미가 충돌하면 Skill이 `REVIEW`로 닫힌다.

## 정확성 게이트

- [x] 관계 주장은 실제 관찰된 edge를 가져야 한다.
- [x] 도메인 판정은 실제 `KACSkillRun`을 가져야 한다.
- [x] claim마다 evidence와 source ref가 연결된다.
- [x] CCM·VCM·Scope·크레딧·배출권 혼동을 차단한다.
- [x] 근거 밖의 내용은 최종 답변으로 공개하지 않는다.
- [x] 자유 서술 초안이 아니라 검증된 claim text만 공개한다.

## 증명 게이트

- [x] Ollama version, model ID, digest가 기록된다.
- [x] prompt hash, tool arguments, Observation, answer hash가 기록된다.
- [x] endpoint가 `127.0.0.1` loopback으로 강제된다.
- [x] 웹 화면이 외부 JS·CSS·미디어를 요청하지 않는다.
- [x] 실제 브라우저에서 행동 경로와 PASS 결과를 확인했다.
- [x] run 파일과 SQLite 감사 기록을 독립 스크립트로 대조했다.

## 검증된 실제 run

- Run ID: `agent-run-1550aabc-1b7b-469e-a5f8-a5b14717a092`
- 질문: 소유·통제 사업장 보일러 도시가스 연소의 Scope 분류
- Local model: `qwen2.5:14b-instruct-q4_K_M`
- 실행 스킬: `scope-activity-classification`
- 최종 판정: `PASS`
- 최종 답변: Scope 1
- 검증 evidence: `skill:skill-run-8bfe89a2-194c-4077-8eda-987ebb02dc1b`, `concept:OPERATIONAL_BOUNDARY`
- source refs: `ghg-protocol-corporate-standard-faq`, `supestar-stage-v2-contract-import`

재현 가능한 비밀정보 없는 증명 snapshot은 [`../proof/latest_verified_run.json`](../proof/latest_verified_run.json)에 있습니다.

## 아직 주장하지 않는 것

- 모든 ESG 질문에 대한 완전성
- 법적·회계적 최종 판정
- 외부 거래 또는 등록부 변경 능력
- 실제 환경 Outcome 발생
- 인터넷 단절 OS 환경 전체에 대한 네트워크 패킷 캡처

따라서 현재 완료 문구는 `AUTONOMOUS_KAC_AGENT_VERIFIED`이며, “모든 도메인에 일반화된 완전 자율 AGI”를 뜻하지 않습니다.
