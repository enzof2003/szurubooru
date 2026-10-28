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
            <div class='bar-row primary'>
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
                    <% if (ctx.canCreatePool) { %>
                        <a href class='button open-create-pool'>Create pool&hellip;</a>
                    <% } %>
                    <a href class='button select-all'>Select all</a>
                    <a href class='button clear-selection'>Clear</a>
                </span>
                <span class='hint'>drag to select &middot; ctrl+click toggles &middot; shift+click for a range &middot; esc clears</span>
            </div>
            <% if (ctx.canCreatePool) { %>
                <form class='create-pool-from-selection' hidden>
                    <div class='bar-row pool-fields'>
                        <%= ctx.makeTextInput({name: 'pool-name', placeholder: 'pool name', required: true}) %>
                        <%= ctx.makeSelect({name: 'pool-category', keyValues: ctx.poolCategories, selectedKey: ctx.defaultPoolCategory}) %>
                        <label class='custom-order'>
                            <input type='checkbox' name='custom-order'/>
                            <span class='checkbox'>custom order</span>
                        </label>
                        <%= ctx.makeTextInput({name: 'pool-order', placeholder: 'post IDs in page order', hidden: true}) %>
                    </div>
                    <div class='bar-row pool-confirm'>
                        <span class='pool-buttons'>
                            <input type='submit' class='confirm' value='Confirm creation'/>
                            <a href class='button swap-pool-order'>Swap order</a>
                            <a href class='button cancel-create-pool'>Cancel</a>
                        </span>
                        <span class='hint pool-hint'>the number on each thumbnail is its page order</span>
                    </div>
                </form>
            <% } %>
        </div>
    <% } %>
</div>
