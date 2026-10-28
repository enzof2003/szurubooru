<div class='content-wrapper tag-diagnosis'>
    <h1>Tag diagnosis</h1>

    <div class='messages'></div>

    <p class='intro'>
        Finds posts whose tags are inconsistent with rules this booru already
        knows about, and can fill in what is missing. Every check is a search
        that <em>should</em> return no posts &mdash; the count is how many posts
        break it.
    </p>

    <section class='implications'>
        <h2>Tag implications</h2>
        <p class='hint'>
            Derived from the implications set on your tags
            (<%- ctx.canEditTags ? 'edit them on each tag’s page' : 'see each tag’s page' %>).
            Implications only fire when a tag is applied, so posts tagged
            before an implication existed never received the implied tag.
        </p>
        <div class='controls'>
            <a href class='button scan'>Scan implications</a>
            <a href class='button stop' hidden>Stop</a>
            <% if (ctx.canEditPostTags) { %>
                <a href class='button fix-all' hidden>Fix all problems</a>
            <% } %>
            <label class='problems-only'>
                <input type='checkbox' checked/>
                <span class='checkbox'>only show problems</span>
            </label>
            <span class='progress'></span>
        </div>
        <div class='results'></div>
    </section>

</div>
        </form>
        <div class='controls'>
            <a href class='button scan'>Scan file rules</a>
            <a href class='button stop' hidden>Stop</a>
            <% if (ctx.canEditPostTags) { %>
                <a href class='button fix-all' hidden>Fix all problems</a>
            <% } %>
            <label class='problems-only'>
                <input type='checkbox' checked/>
                <span class='checkbox'>only show problems</span>
            </label>
            <span class='progress'></span>
        </div>
        <div class='results'></div>
    </section>
</div>
