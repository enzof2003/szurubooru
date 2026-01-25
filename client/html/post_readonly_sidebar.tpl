<div class='readonly-sidebar'>
    <article class='details'>
        <section class='download'>
            <a rel='external' href='<%- ctx.post.contentUrl %>'>
                <i class='fa fa-download'></i><!--
            --><%= ctx.makeFileSize(ctx.post.fileSize) %> <!--
                --><%- {
                    'image/gif': 'GIF',
                    'image/jpeg': 'JPEG',
                    'image/png': 'PNG',
                    'image/webp': 'WEBP',
                    'image/bmp': 'BMP',
                    'image/avif': 'AVIF',
                    'image/heif': 'HEIF',
                    'image/heic': 'HEIC',
                    'video/webm': 'WEBM',
                    'video/mp4': 'MPEG-4',
                    'video/quicktime': 'MOV',
                    'application/x-shockwave-flash': 'SWF',
                }[ctx.post.mimeType] %><!--
            --></a>
            (<%- ctx.post.canvasWidth %>x<%- ctx.post.canvasHeight %>)
            <% if (ctx.post.flags.length) { %><!--
                --><% if (ctx.post.flags.includes('loop')) { %><i class='fa fa-repeat'></i><% } %><!--
                --><% if (ctx.post.flags.includes('sound')) { %><i class='fa fa-volume-up'></i><% } %>
            <% } %>
        </section>

        <section class='upload-info'>
            <%= ctx.makeUserLink(ctx.post.user) %>,
            <%= ctx.makeRelativeTime(ctx.post.creationTime) %>
        </section>

        <% if (ctx.enableSafety) { %>
            <section class='safety'>
                <i class='fa fa-circle safety-<%- ctx.post.safety %>'></i><!--
                --><%- ctx.post.safety[0].toUpperCase() + ctx.post.safety.slice(1) %>
            </section>
        <% } %>

        <section class='zoom'>
            <a href class='fit-original'>Original zoom</a> &middot;
            <a href class='fit-width'>fit width</a> &middot;
            <a href class='fit-height'>height</a> &middot;
            <a href class='fit-both'>both</a>
        </section>

        <% if (ctx.post.source) { %>
            <section class='source'>
                Source: <% for (let i = 0; i < ctx.post.sourceSplit.length; i++) { %>
                    <% if (i != 0) { %>&middot;<% } %>
                    <a href='<%- ctx.post.sourceSplit[i] %>' title='<%- ctx.post.sourceSplit[i] %>'><%- ctx.extractRootDomain(ctx.post.sourceSplit[i]) %></a>
                <% } %>
            </section>
        <% } %>

        <section class='search'>
            Search on
            <a href='http://iqdb.org/?url=<%- encodeURIComponent(ctx.post.fullContentUrl) %>'>IQDB</a> &middot;
            <a href='https://danbooru.donmai.us/posts?tags=md5:<%- ctx.post.checksumMD5 %>'>Danbooru</a> &middot;
            <a href='https://lens.google.com/uploadbyurl?url=<%- encodeURIComponent(ctx.post.fullContentUrl) %>'>Google Images</a>
        </section>

        <section class='social'>
            <div class='score-container'></div>

            <div class='fav-container'></div>
        </section>
    </article>

    <% if (ctx.post.relations.length) { %>
        <nav class='relations'>
            <h1>Relations (<%- ctx.post.relations.length %>)</h1>
            <ul><!--
                --><% for (let post of ctx.post.relations) { %><!--
                    --><li><!--
                        --><a href='<%= ctx.getPostUrl(post.id, ctx.parameters) %>'><!--
                            --><%= ctx.makeThumbnail(post.thumbnailUrl) %><!--
                        --></a><!--
                    --></li><!--
                --><% } %><!--
            --></ul>
        </nav>
    <% } %>

    <nav class='tags'>
        <h1 class="tags-heading">Tags (<%- ctx.post.tags.length %>)</h1>

        <% if (ctx.post.tags.length) { %>
            <%
                // Desired display order (keeps your original ordering)
                const CATEGORY_ORDER = ['artist','serie','sub-serie','character','general','type'];

                // Map numeric category ids to names. Update numbers to match your backend if needed.
                const CATEGORY_MAP = {
                    0: 'general',
                    1: 'artist',
                    2: 'serie',
                    3: 'character',
                    4: 'type',
                    5: 'sub-serie'
                };

                function normalizeCategoryName(tag) {
                    if (tag && tag.categoryName && typeof tag.categoryName === 'string' && tag.categoryName.trim() !== '') {
                        return tag.categoryName.toLowerCase();
                    }
                    if (tag && typeof tag.category === 'number') {
                        if (CATEGORY_MAP.hasOwnProperty(tag.category)) {
                            return String(CATEGORY_MAP[tag.category]).toLowerCase();
                        }
                        return 'unknown-' + String(tag.category);
                    }
                    if (tag && typeof tag.category === 'string' && tag.category.trim() !== '') {
                        return tag.category.toLowerCase();
                    }
                    return 'general';
                }

                // Group tags by normalized category name
                const groups = {};
                for (let tag of ctx.post.tags) {
                    const name = normalizeCategoryName(tag);
                    if (!groups[name]) groups[name] = [];
                    groups[name].push(tag);
                }

                // Build ordered list: categories in CATEGORY_ORDER first, then remaining alphabetical
                const ordered = [];
                for (let n of CATEGORY_ORDER) {
                    if (groups[n]) ordered.push(n);
                }
                const remaining = Object.keys(groups).filter(n => !CATEGORY_ORDER.includes(n)).sort();
                for (let r of remaining) ordered.push(r);
            %>

            <% for (let catName of ordered) { %>
                <section class='tag-category tag-category-<%- catName.replace(/\s+/g,'-') %>'>
                    <h3 class="tag-category-heading"><%- catName.charAt(0).toUpperCase() + catName.slice(1) %></h3>
                    <ul class='compact-tags'><!--
                        --><% for (let tag of groups[catName]) { %><!--
                            --><li><!--
                                --><% if (ctx.canViewTags) { %><!--
                                --><a href='<%- ctx.formatClientLink('tag', tag.names[0]) %>' class='<%= ctx.makeCssName(tag.category, 'tag') %>'><!--
                                    --><i class='fa fa-tag'></i><!--
                                --><% } %><!--
                                --><% if (ctx.canViewTags) { %><!--
                                    --></a><!--
                                --><% } %><!--
                                --><% if (ctx.canListPosts) { %><!--
                                    --><a href='<%- ctx.formatClientLink('posts', {query: ctx.escapeTagName(tag.names[0])}) %>' class='<%= ctx.makeCssName(tag.category, 'tag') %>'><!--
                                --><% } %><!--
                                    --><%- ctx.getPrettyName(tag.names[0]) %><!--
                                --><% if (ctx.canListPosts) { %><!--
                                    --></a><!--
                                --><% } %>&#32;<!--
                                --><span class='tag-usages' data-pseudo-content='<%- tag.postCount %>'></span><!--
                            --></li><!--
                        --><% } %><!--
                    --></ul>
                </section>
            <% } %>

        <% } else { %>
            <p>
                No tags yet!
                <% if (ctx.canEditPosts) { %>
                    <a href='<%= ctx.getPostEditUrl(ctx.post.id, ctx.parameters) %>'>Add some.</a>
                <% } %>
            </p>
        <% } %>
    </nav>

</div>
