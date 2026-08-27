# 수용 게이트

## 구조 게이트

- [ ] 질문별 키워드 route map이 없다.
- [ ] Local Qwen이 실제 `tool_calls`를 생성한다.
- [ ] 등록된 도구만 실행된다.
- [ ] CCS 개념·관계와 KAC Skill이 서로 다른 도구 계약으로 제공된다.
- [ ] 모든 실행이 최대 step·tool-call 예산 안에서 종료된다.

## 자율 행동 게이트

- [ ] 사용자가 언급하지 않은 연결 개념을 Qwen이 스스로 관찰한다.
- [ ] 같은 질문의 경로를 코드가 미리 지정하지 않는다.
- [ ] 도구 Observation에 따라 다음 행동이 달라진다.
- [ ] 필요한 근거가 없으면 Qwen이 질문하거나 검증기가 중단한다.

## 정확성 게이트

- [ ] 관계 주장은 실제 관찰된 edge를 가진다.
- [ ] 도메인 판정은 실제 Skill OutputObject를 가진다.
- [ ] 답변 claim마다 source ref가 연결된다.
- [ ] CCM·VCM·Scope·크레딧·상쇄를 임의로 같은 개념으로 바꾸지 않는다.
- [ ] 근거 밖의 내용을 생성하면 답변 승인이 실패한다.

## 증명 게이트

- [ ] model ID·digest와 Ollama version이 기록된다.
- [ ] prompt hash·tool call·arguments·Observation·answer hash가 기록된다.
- [ ] Qwen 프로세스와 로컬 endpoint 호출이 확인된다.
- [ ] 인터넷이 차단된 상태에서도 로컬 inference가 성공한다.
- [ ] 실제 브라우저에서 행동 경로를 볼 수 있다.
- [ ] 미리 보지 못한 표현의 질문 세트에서도 행동이 재현된다.

## 완료 판정

모든 체크가 증거 파일과 자동 테스트로 확인되기 전에는 `AUTONOMOUS_KAC_AGENT_COMPLETE`를 선언하지 않습니다.
