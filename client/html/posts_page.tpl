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
                    <% if (ctx.canBulkDelete && ctx.mode === 'delete') { %>
                        <span class='edit-overlay'>
                            <a href class='delete-flipper' title='Mark this post for deletion; marks survive changing page'>
                            </a>
                        </span>
                    <% } %>
                </li>
            <% } %>
            <%= ctx.makeFlexboxAlign() %>
        </ul>
    <% } %>
    <% if (ctx.canSelect) { %>
        <div class='post-selection-bar' hidden>
            <div class='bar-row primary'>
                <span class='summary'>
                    <span class='count'>0</span> selected<span class='marked-summary' hidden> &middot; <span class='marked-count'>0</span> marked</span>
                </span>
                <a href class='button select-all'>Select all</a>
                <a href class='button clear-selection'>Clear</a>
                <span class='hint'>drag to select &middot; ctrl+click toggles &middot; shift+click for a range &middot; esc clears</span>
            </div>

            <div class='mode-panel panel-none'>
                <div class='bar-row'>
                    <span class='notice'>Pick a batch mode above to act on the selection.</span>
                </div>
            </div>

            <% if (ctx.canBulkEditTags) { %>
                <div class='mode-panel panel-tag' hidden>
                    <div class='bar-row'>
                        <%= ctx.makeTextInput({name: 'bulk-tag', value: ctx.bulkEditTags, placeholder: 'tags to apply, space separated'}) %>
                    </div>
                    <div class='bar-row'>
                        <a href class='button apply-tags'>Apply tags</a>
                        <span class='hint'>posts that already have every tag get them removed instead</span>
                    </div>
                </div>
            <% } %>

            <% if (ctx.enableSafety && ctx.canBulkEditSafety) { %>
                <div class='mode-panel panel-safety' hidden>
                    <div class='bar-row'>
                        <span class='safety-picker'>
                            <% for (let safety of ['safe', 'sketchy', 'unsafe']) { %>
                                <a href data-safety='<%- safety %>' class='safety-<%- safety %>' title='<%- safety %>'></a>
                            <% } %>
                        </span>
                        <span class='safety-chosen'>no rating picked</span>
                    </div>
                    <div class='bar-row'>
                        <a href class='button apply-safety'>Apply</a>
                    </div>
                </div>
            <% } %>

            <% if (ctx.canBulkDelete) { %>
                <div class='mode-panel panel-delete' hidden>
                    <div class='bar-row'>
                        <span class='notice delete-notice'></span>
                    </div>
                    <div class='bar-row'>
                        <span class='delete-stage-1'>
                            <a href class='button danger request-delete'>Delete posts</a>
                        </span>
                        <span class='delete-stage-2' hidden>
                            <a href class='button danger confirm-delete'>Yes, permanently delete</a>
                            <a href class='button cancel-delete'>Cancel</a>
                        </span>
                        <a href class='button clear-marks' hidden>Clear marks</a>
                    </div>
                </div>
            <% } %>

            <% if (ctx.canCreatePool) { %>
                <div class='mode-panel panel-pool' hidden>
                    <form class='create-pool-from-selection'>
                        <div class='bar-row pool-naming'>
                            <%= ctx.makeTextInput({name: 'pool-name', placeholder: 'pool name', required: true}) %>
                            <%= ctx.makeSelect({name: 'pool-category', keyValues: ctx.poolCategories, selectedKey: ctx.defaultPoolCategory}) %>
                        </div>
                        <div class='bar-row pool-ordering' hidden>
                            <span class='pool-summary'></span>
                            <a href class='button swap-pool-order'>Swap order</a>
                            <label class='custom-order'>
                                <input type='checkbox' name='custom-order'/>
                                <span class='checkbox'>custom order</span>
                            </label>
                            <%= ctx.makeTextInput({name: 'pool-order', placeholder: 'post IDs in page order', hidden: true}) %>
                        </div>
                        <div class='bar-row pool-actions'>
                            <a href class='button start-pool-order'>Create pool</a>
                            <span class='pool-confirm-buttons' hidden>
                                <input type='submit' class='confirm' value='Confirm creation'/>
                                <a href class='button cancel-pool-order'>Back</a>
                            </span>
                            <span class='hint pool-hint' hidden>the number on each thumbnail is its page order</span>
                        </div>
                    </form>
                </div>
            <% } %>
        </div>
    <% } %>
</div>
