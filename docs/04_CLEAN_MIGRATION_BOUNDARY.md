# 깨끗한 이전 경계

## 보존하는 기존 프로젝트

- v1: 제출 완료 하이브리드 시스템
- v2: Stage 파생 Skill의 결정적 실행을 증명한 `supestar-full-kac`

두 프로젝트의 코드와 Git 이력은 이 저장소에 합치지 않습니다.

## 가져오지 않는 것

- 키워드 기반 `ROUTE_TO_SKILL`
- 질문별 자연어 adapter
- 미리 작성된 답변 문자열
- v2의 SQLite 실행 상태와 run 기록
- 환경변수·API key·배포 credential
- 출처가 불명확한 임시 산출물

## 나중에 선별해서 가져올 수 있는 것

- Stage 1~5로 파생된 원자 Skill 계약
- 검증된 CCS Identity·Knowledge·관계
- 공식 출처 레지스트리
- 사용 권리가 확인된 수페스타 시각 자산

가져올 때는 원본 저장소, commit, 상대 경로, SHA-256, 변환 내역을 `provenance/import_manifest.json`에 먼저 기록합니다. manifest 없이 복사된 파일은 Runtime에 등록하지 않습니다.
