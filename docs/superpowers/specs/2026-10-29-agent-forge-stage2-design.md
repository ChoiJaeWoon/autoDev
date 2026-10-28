# Agent Forge 2단계: AI 웹사이트 유지보수 에이전트 설계 문서

- **작성일:** 2026-10-29
- **대상 범위:** 2단계 — 레거시 웹(Java/JSP/HTML/CSS) 유지보수, 브라우저 렌더링 검증 및 전후 스크린샷 시각 회귀(Visual Regression) 인스펙터

---

## 1. 배경 및 목적 (Intent & Why)

현업에서 레거시 Java/JSP 웹사이트를 유지보수할 때는 백엔드 Java 로직(`Post.java`, `BoardService.java`)만 통과했다고 해서 작업이 끝났다고 볼 수 없다. JSP 템플릿(`boardList.jsp`)이나 CSS를 수정한 뒤 실제 브라우저에서 렌더링했을 때:
1. 레이아웃이 깨지거나 가로 스크롤 오버플로(`scrollWidth > clientWidth`)가 발생하는 문제
2. 테이블 헤더(`<th>`)와 본문 셀(`<td>`) 개수가 어긋나는 구조 불일치 문제
3. 수정 전(`Before`)과 수정 후(`After`) 화면이 어떻게 달라졌는지 눈으로 대조하기 어려운 문제

2단계의 목적은 **1단계 코어 FSM 엔진(`ForgeEngine`)을 재작성하지 않고**, 플러그인 `Verifier` 계층에 **`LegacyWebVerifier` (Java 컴파일·단위 테스트 + JSP 프리뷰 렌더링 + Playwright Chromium 브라우저 DOM/레이아웃 검증 + Pillow 기반 전후 스크린샷 비교)**를 결합하여 레거시 웹 유지보수를 자동화하는 것이다.

---

## 2. 핵심 구성 요소 (Architecture & Components)

### 2.1 저장소 심볼 맵 확장 (`agent_forge/harness/repo_map.py`)
- 기존 `.py`, `.java`, `.jsp`에 더해 `.html`, `.css` 파일까지 스캔하여 `<title>`, `<table class="...">`, `<form>`, CSS 주요 클래스 선택자를 추출한다.

### 2.2 레거시 Java & JSP 통합 검증기 (`agent_forge/verifiers/legacy_web.py`)
1. **Java 컴파일 및 JUnit 호환 테스트 실행기 (`run_java_tests`)**
   - 시스템에 `mvn`이 설치되어 있으면 `mvn test -q`를 사용하고, `mvn`이 없는 환경에서도 `javac` + `java`만으로 즉시 동작하도록 경량 JUnit 5(`org.junit.jupiter.api.Test`, `Assertions`) 내장 Shim과 테스트 러너를 제공한다.
2. **JSP → HTML 프리뷰 렌더러 (`render_jsp_preview`)**
   - `boardList.jsp` 등 JSP 파일에서 `<%@ ... %>` 지시어를 정리하고, 테이블 헤더(`<th>`) 구조에 맞춘 샘플 게시글 행(`<tr><td>...</td></tr>`)을 렌더링하여 로컬 임시 서버 또는 파일로 제공한다.
3. **Playwright 브라우저 렌더링 및 시각 회귀 검증 (`PlaywrightVisualVerifier`)**
   - `capture_baseline(repo_root, run_id)`: 구현(`IMPLEMENT`) 진입 직전의 JSP 화면을 Headless Chromium(`1280x800`)으로 렌더링하여 `baseline.png`로 저장한다.
   - `verify(repo_root)`:
     - Java 백엔드 테스트를 먼저 실행하고, 실패 시 즉시 컴파일/테스트 에러를 반환한다.
     - 통과 시 수정된 JSP 화면을 Headless Chromium으로 렌더링하여 `after.png`를 캡처한다.
     - **브라우저 DOM & 레이아웃 검증:**
       - 페이지 런타임 에러(`pageerror`) 0건 확인
       - 가로 오버플로(`document.documentElement.scrollWidth <= document.documentElement.clientWidth`) 확인
       - 테이블 헤더(`thead th`)와 데이터 행(`tbody tr td`) 컬럼 수 일치 확인
     - **픽셀 비교 및 Diff 히트맵 생성 (`Pillow`):**
       - `baseline.png`와 `after.png`를 픽셀 단위로 비교해 변경된 영역을 붉은색 하이라이트로 표시한 `diff.png`와 변경 비율(`diff_ratio`)을 생성한다.

### 2.3 체크포인트 아티팩트 영속화 (`state.py`, `store.py`, `engine.py`)
- `AttemptRecord`에 `artifacts: dict[str, str]` 필드를 추가하고 SQLite `attempts` 테이블에 `artifacts_json` 컬럼을 저장한다(기존 DB 파일과 하위 호환되도록 자동 마이그레이션 지원).
- `ForgeEngine`은 `WRITE_TEST` 직후 `verifier.capture_baseline(...)`이 존재하면 호출하여 수정 전 화면을 기록하고, `VERIFY` 단계마다 생성된 스크린샷·검증 메타데이터(`artifacts`)를 `AttemptRecord`에 저장한다.

### 2.4 웹 인스펙터 대시보드 시각 회귀 뷰어 (`server.py`, `index.html`, `styles.css`, `app.js`)
- `GET /api/runs/<run_id>/artifacts/<attempt_number>/<kind>`: 해당 시도의 `baseline`, `after`, `diff` PNG 이미지를 안전하게 서빙한다.
- 대시보드 내 **Visual Regression & Layout Gate** 패널 추가:
  - 아티팩트가 존재하는 Run(레거시 JSP/웹 타깃)을 선택하면 **Before (수정 전)** / **After (수정 후)** / **Visual Diff (변경 영역 하이라이트)** 3단 스크린샷 비교 카드와 DOM/레이아웃 체크 결과를 화이트 톤·곡선 카드 UI로 표시한다.
