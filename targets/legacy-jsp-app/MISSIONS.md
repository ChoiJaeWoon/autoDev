# Legacy JSP App — 단계별 벤치마크 미션 (Stage 1 & Stage 2)

## Stage 1 미션 (Agent Forge 코어 자율 개발 루프)
1. **미션 1 (공백 검색어 및 대소문자 무시 검색):**
   - 요청: "`BoardService.findPosts`에서 검색어가 공백(`"   "`)만 있거나 빈 문자열일 때 전체 게시글을 반환하고, 제목뿐 아니라 작성자(`author`)까지 포함해 검색되도록 수정해 줘."
   - 검증 포인트: `BoardServiceTest.java` 보강 후 `IMPLEMENT` 단계에서 테스트 파일 잠금 유지 및 통과 확인.

## Stage 2 확장 미션 (AI 웹사이트 유지보수 에이전트 — Playwright & 시각 검증)
1. **미션 2 (반응형 테이블 레이아웃 보수 및 시각 회귀 검사):**
   - 요청: "`boardList.jsp`의 `.board-container`가 고정 폭(`960px`)이라 모바일 화면(폭 `375px`)에서 가로 스크롤 깨짐이 발생해. `max-width: 960px; width: 100%;`로 수정하고 검색 폼과 테이블이 깨지지 않는지 확인해 줘."
   - 검증 포인트: JSP/CSS 화이트리스트 수정 후 Playwright 렌더링 및 전후 스크린샷 비교 통과 확인.
