package com.example.legacy;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

public class BoardServiceTest {
    @Test
    public void testFindPostsByKeyword() {
        BoardService service = new BoardService();
        service.addPost(new Post(1L, "점검 안내", "관리자", "서버 점검 공지"));
        assertEquals(1, service.findPosts("점검").size());
    }
}
