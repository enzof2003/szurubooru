<% if (ctx.postFlow) { %><div class='pool-list post-flow'><% } else { %><div class='pool-list'><% } %>
    <% if (ctx.response.results.length) { %>
        <ul>
          <% for (let pool of ctx.response.results) { %>
            <li data-pool-id='<%= pool.id %>'>
                <a class='thumbnail-wrapper'
                   href='<%= ctx.canViewPools ? "/posts/query=pool:" + pool.id + " -sort:pool" : "" %>'>
                    <% if (ctx.canViewPosts) { %>
                        <%= ctx.makePoolThumbnails(pool.posts, ctx.postFlow) %>
                    <% } %>
                </a>

                <div class='pool-name'>
                    <%= ctx.makePoolLinkv2(pool.id, false, false, pool, name) %>

                    <% if (ctx.canViewPools) { %>
                        <a class='pool-settings'
                           href='<%= ctx.formatClientLink("pool", pool.id) %>'
                           title='Edit pool'>
                            <i class='fa fa-cog'></i>
                        </a>
                    <% } %>
                </div>

            </li>

          <% } %>
          <%= ctx.makeFlexboxAlign() %>
        </ul>
    <% } %>
</div>
