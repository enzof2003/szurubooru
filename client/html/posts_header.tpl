<div class='post-list-header<%- ctx.editMode ? ' edit-mode' : '' %>'><%
    %><% if (ctx.canUseEditMode) { %><%
        %><button type='button' class='mousetrap edit-mode-toggle<%- ctx.editMode ? ' active' : '' %>' title='Show the batch editing tools and allow drag-selecting posts'><%
            %><i class='fa fa-pencil'></i> Edit mode<%
        %></button><%
    %><% } %><%
    %><form class='horizontal search'><%
        %><%= ctx.makeTextInput({text: 'Search query', id: 'search-text', name: 'search-text', value: ctx.parameters.query}) %><%
        %><wbr/><%
        %><input class='mousetrap' type='submit' value='Search'/><%
        %><wbr/><%
        %><% if (ctx.enableSafety) { %><%
            %><input data-safety=safe type='button' class='mousetrap safety safety-safe <%- ctx.settings.listPosts.safe ? '' : 'disabled' %>'/><%
            %><input data-safety=sketchy type='button' class='mousetrap safety safety-sketchy <%- ctx.settings.listPosts.sketchy ? '' : 'disabled' %>'/><%
            %><input data-safety=unsafe type='button' class='mousetrap safety safety-unsafe <%- ctx.settings.listPosts.unsafe ? '' : 'disabled' %>'/><%
        %><% } %><%
        %><wbr/><%
        %><a class='mousetrap button append' href='<%- ctx.formatClientLink('help', 'search', 'posts') %>'>Syntax help</a><%
        %><% if (ctx.availableModes.length) { %><%
            %><wbr/><%
            %><span class='batch-modes'><%
                %><span class='append hint'>Batch:</span><%
                %><% for (let mode of ctx.availableModes) { %><%
                    %><button type='button' class='mousetrap batch-mode<%- ctx.mode === mode.key ? ' active' : '' %>' data-mode='<%- mode.key %>'><%- mode.label %></button><%
                %><% } %><%
            %></span><%
        %><% } %><%
    %></form><%
%></div>
