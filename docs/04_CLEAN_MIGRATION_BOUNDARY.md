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

## 선별해 가져온 것

- Stage 1~5로 파생된 원자 Skill 계약 6개, 파일 60개
- 계약에 포함된 Identity·Goal·Task·Knowledge·Method·Skill·Runtime
- 공식 출처 snapshot과 source registry

원본은 `supestar_full_kac` commit `e6a27e7fb9d28c0662df2f81e78bf170793f0f7e`이며, 모든 파일은 [`../provenance/import_manifest.json`](../provenance/import_manifest.json)의 SHA-256과 바이트 동일성을 검사합니다.

## 아직 가져오지 않은 것

- 출처와 사용 권리가 확정되지 않은 수페스타 캐릭터 이미지
- v2의 질문 router와 고정 답변 adapter
- v2의 SQLite 상태와 실행 기록

원본 저장소, commit, 상대 경로, SHA-256, 변환 내역을 `provenance/import_manifest.json`에 기록했습니다. manifest 없이 복사된 파일은 Runtime에 등록하지 않습니다.
