<div class="pool-gallery">
    <h1>
        <%= ctx.makePoolLinkv2(
            ctx.pool.id,
            false,
            false,
            ctx.pool,
            ctx.pool.names[0]
        ) %>
    </h1>

    <% if (ctx.posts.some(function(p) { return p.type === 'image' && (p.notes || []).length; })) { %>
        <div class="gallery-notes-toggle">
            <%= ctx.makeCheckbox({
                text: 'Show notes',
                name: 'show-notes',
            }) %>
        </div>
    <% } %>

    <div class="gallery-grid">
        <% ctx.posts.forEach(function(post, index) { %>

            <div class="gallery-item post-type-<%- post.type %>"
                 data-post-id="<%- post.id %>"
                 data-index="<%- index %>">

                <% if (post.type === 'image' && (post.notes || []).length) { %>

                    <div class="gallery-media-notes">
                        <img class="resize-listener gallery-media"
                             alt=""
                             src="/<%- post.contentUrl %>"
                             loading="lazy">
                        <div class="post-overlay"></div>
                    </div>

                <% } else if (['image', 'animation'].includes(post.type)) { %>

                    <img class="resize-listener gallery-media"
                         alt=""
                         src="/<%- post.contentUrl %>"
                         loading="lazy">

                <% } else if (post.type === 'flash') { %>

                    <object class="resize-listener gallery-media"
                            width="<%- post.canvasWidth %>"
                            height="<%- post.canvasHeight %>"
                            data="/<%- post.contentUrl %>">
                        <param name="wmode" value="opaque"/>
                        <param name="movie" value="/<%- post.contentUrl %>"/>
                    </object>

                <% } else if (post.type === 'video') { %>

                    <video class="resize-listener gallery-media"
                           controls
                           playsinline
                           loop="<%= (post.flags || []).includes('loop') %>"
                           preload="metadata">
                        <source type="<%- post.mimeType %>"
                                src="/<%- post.contentUrl %>">
                    </video>

                <% } else { %>

                    <div class="unknown-post-type">Unknown post type</div>

                <% } %>

            </div>

        <% }); %>
    </div>
</div>
