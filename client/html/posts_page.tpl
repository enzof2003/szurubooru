<% if (ctx.postFlow) { %><div class='post-list post-flow'><% } else { %><div class='post-list'><% } %>
    <% if (ctx.response.results.length) { %>
        <ul>
            <% for (let post of ctx.response.results) { %>
                <li data-post-id='<%= post.id %>'>
                    <a class='thumbnail-wrapper <%= post.tags.length > 0 ? "tags" : "no-tags" %>'
                            title='@<%- post.id %> (<%- post.type %>)&#10;&#10;Tags: <%- post.tags.map(tag => '#' + tag.names[0]).join(' ') || 'none' %>'
                            href='<%= ctx.canViewPosts ? ctx.getPostUrl(post.id, ctx.parameters) : '' %>'>
                        <%= ctx.makeThumbnail(post.thumbnailUrl) %>
                        <span class='type' data-type='<%- post.type %>'>
                            <% if (post.type == 'video' || post.type == 'flash' || post.type == 'animation') { %>
                                <span class='icon'><i class='fa fa-film'></i></span>
                            <% } else { %>
                                <%- post.type %>
                            <% } %>
                        </span>
                        <% if (post.score || post.favoriteCount || post.commentCount) { %>
                            <span class='stats'>
                                <% if (post.score) { %>
                                    <span class='icon'>
                                        <i class='fa fa-thumbs-up'></i>
                                        <%- post.score %>
                                    </span>
                                <% } %>
                                <% if (post.favoriteCount) { %>
                                    <span class='icon'>
                                        <i class='fa fa-heart'></i>
                                        <%- post.favoriteCount %>
                                    </span>
                                <% } %>
                                <% if (post.commentCount) { %>
                                    <span class='icon'>
                                        <i class='fa fa-commenting'></i>
                                        <%- post.commentCount %>
                                    </span>
                                <% } %>
                            </span>
                        <% } %>
                    </a>
                    <span class='edit-overlay'>
                        <% if (ctx.canBulkEditTags && ctx.parameters && ctx.parameters.tag) { %>
                            <a href class='tag-flipper'>
                            </a>
                        <% } %>
                        <% if (ctx.canBulkEditSafety && ctx.parameters && ctx.parameters.safety) { %>
                            <span class='safety-flipper'>
                                <% for (let safety of ['safe', 'sketchy', 'unsafe']) { %>
                                    <a href data-safety='<%- safety %>' class='safety-<%- safety %><%- post.safety === safety ? ' active' : '' %>'>
                                    </a>
                                <% } %>
                            </span>
                        <% } %>
                        <% if (ctx.canBulkDelete && ctx.parameters && ctx.parameters.delete) { %>
                            <a href class='delete-flipper'>
                            </a>
                        <% } %>
                    </span>
                </li>
            <% } %>
            <%= ctx.makeFlexboxAlign() %>
        </ul>
    <% } %>
    <% if (ctx.canSelect) { %>
        <div class='post-selection-bar' hidden>
            <span class='summary'><span class='count'>0</span> selected</span>
            <span class='actions'>
                <% if (ctx.canBulkEditTags && ctx.parameters && ctx.parameters.tag) { %>
                    <a href class='button tag-selected'>Add tags</a>
                    <a href class='button untag-selected'>Remove tags</a>
                <% } %>
                <% if (ctx.enableSafety && ctx.canBulkEditSafety && ctx.parameters && ctx.parameters.safety) { %>
                    <span class='safety-selected'>
                        <% for (let safety of ['safe', 'sketchy', 'unsafe']) { %>
                            <a href data-safety='<%- safety %>' class='safety-<%- safety %>' title='Set selection to <%- safety %>'></a>
                        <% } %>
                    </span>
                <% } %>
                <% if (ctx.canBulkDelete && ctx.parameters && ctx.parameters.delete) { %>
                    <a href class='button mark-selected-deleted'>Mark for deletion</a>
                    <a href class='button unmark-selected-deleted'>Unmark</a>
                <% } %>
                <a href class='button select-all'>Select all</a>
                <a href class='button clear-selection'>Clear</a>
            </span>
            <span class='hint'>drag to select &middot; ctrl+click to toggle &middot; shift+click for a range &middot; esc to clear</span>
        </div>
    <% } %>
</div>
