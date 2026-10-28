package com.example.legacy;

public class BoardServiceTest {
    public void testFindPostsByKeyword() {
        BoardService service = new BoardService();
        service.addPost(new Post(1L, "점검 안내", "관리자", "서버 점검 공지"));
        assert service.findPosts("점검").size() == 1;
    }
}
