package com.example.legacy;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

public class BoardService {
    private final List<Post> posts = new ArrayList<>();

    public void addPost(Post post) {
        posts.add(post);
    }

    public List<Post> findPosts(String keyword) {
        if (keyword == null) {
            return new ArrayList<>(posts);
        }
        return posts.stream()
                .filter(p -> p.getTitle().contains(keyword))
                .collect(Collectors.toList());
    }
}
