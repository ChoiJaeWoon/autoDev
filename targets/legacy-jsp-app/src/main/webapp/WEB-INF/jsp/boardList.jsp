<%@ page contentType="text/html;charset=UTF-8" language="java" %>
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>사내 공지사항 게시판 (Legacy JSP)</title>
    <style>
        .board-container { width: 960px; margin: 0 auto; }
        .board-table { width: 100%; border-collapse: collapse; }
        .board-table th, .board-table td { border: 1px solid #ccc; padding: 8px; }
    </style>
</head>
<body>
<div class="board-container">
    <h1>사내 공지사항 게시판</h1>
    <form action="/board/search" method="get">
        <input type="text" name="keyword" placeholder="검색어를 입력하세요" />
        <button type="submit">검색</button>
    </form>
    <table class="board-table">
        <thead>
        <tr>
            <th>번호</th>
            <th>제목</th>
            <th>작성자</th>
        </tr>
        </thead>
        <tbody>
        <!-- 게시글 목록 렌더링 영역 -->
        </tbody>
    </table>
</div>
</body>
</html>
