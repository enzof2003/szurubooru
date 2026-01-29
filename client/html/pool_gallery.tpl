<div class="pool-gallery">
    <h1><%= ctx.pool.names && ctx.pool.names[0] %></h1>

    <div class="gallery-grid">
        <% ctx.posts.forEach(function(post) { %>
            <div class="gallery-item">
                <a href="/post/<%= post.id %>">
                    <img src="/<%= post.contentUrl %>" loading="lazy">
                </a>
            </div>
        <% }); %>
    </div>
</div>
