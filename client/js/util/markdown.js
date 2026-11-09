"use strict";

const marked = require("marked");
const DOMPurify = require("dompurify");

class BaseMarkdownWrapper {
    preprocess(text) {
        return text;
    }

    postprocess(text) {
        return text;
    }
}

class SjisWrapper extends BaseMarkdownWrapper {
    constructor() {
        super();
        this.buf = [];
    }

    preprocess(text) {
        return text.replace(
            /\[sjis\]((?:[^\[]|\[(?!\/?sjis\]))+)\[\/sjis\]/gi,
            (match, capture) => {
                var ret = "%%%SJIS" + this.buf.length;
                this.buf.push(capture);
                return ret;
            }
        );
    }

    postprocess(text) {
        return text.replace(
            /(?:<p>)?%%%SJIS(\d+)(?:<\/p>)?/,
            (match, capture) => {
                return '<div class="sjis">' + this.buf[capture] + "</div>";
            }
        );
    }
}

// fix \ before ~ being stripped away
class TildeWrapper extends BaseMarkdownWrapper {
    preprocess(text) {
        return text.replace(/\\~/g, "%%%T");
    }

    postprocess(text) {
        return text.replace(/%%%T/g, "\\~");
    }
}

// prevent ^#... from being treated as headers, due to tag permalinks
class TagPermalinkFixWrapper extends BaseMarkdownWrapper {
    preprocess(text) {
        return text.replace(/^#/g, "%%%#");
    }

    postprocess(text) {
        return text.replace(/%%%#/g, "#");
    }
}

// post, user and tags permalinks
class EntityPermalinkWrapper extends BaseMarkdownWrapper {
    preprocess(text) {
        text = text.replace(
            /(^|^\(|(?:[^\]])\(|[\s<>\[\]\)])([+#@][a-zA-Z0-9_-]+)/g,
            "$1[$2]($2)"
        );
        text = text.replace(/\]\(@(\d+)\)/g, "](/post/$1)");
        text = text.replace(/\]\(\+([a-zA-Z0-9_-]+)\)/g, "](/user/$1)");
        text = text.replace(/\]\(#([a-zA-Z0-9_-]+)\)/g, "](/posts/query=$1)");
        return text;
    }
}

class SearchPermalinkWrapper extends BaseMarkdownWrapper {
    postprocess(text) {
        return text.replace(
            /\[search\]((?:[^\[]|\[(?!\/?search\]))+)\[\/search\]/gi,
            '<a href="/posts/query=$1"><code>$1</code></a>'
        );
    }
}

class SpoilersWrapper extends BaseMarkdownWrapper {
    postprocess(text) {
        return text.replace(
            /\[spoiler\]((?:[^\[]|\[(?!\/?spoiler\]))+)\[\/spoiler\]/gi,
            '<span class="spoiler">$1</span>'
        );
    }
}

class SmallWrapper extends BaseMarkdownWrapper {
    postprocess(text) {
        return text.replace(
            /\[small\]((?:[^\[]|\[(?!\/?small\]))+)\[\/small\]/gi,
            "<small>$1</small>"
        );
    }
}

class StrikeThroughWrapper extends BaseMarkdownWrapper {
    postprocess(text) {
        text = text.replace(/(^|[^\\])(~~|~)([^~]+)\2/g, "$1<del>$3</del>");
        return text.replace(/\\~/g, "~");
    }
}

// Notes: "~" is never strikethrough (translations use it for a playful tone,
// "Oh my~ ... okay~?"). Every tilde (and an escaped "\~") is hidden from both
// marked's GFM del syntax and StrikeThroughWrapper, then restored verbatim.
class NoStrikeThroughWrapper extends BaseMarkdownWrapper {
    preprocess(text) {
        return text.replace(/\\?~/g, "%%%W");
    }

    postprocess(text) {
        return text.replace(/%%%W/g, "~");
    }
}

class FaviconWrapper extends BaseMarkdownWrapper {
    preprocess(text) {
        return text.replace(
            /\[icon\]((?:[^\[]|\[(?!\/?icon\]))+)\[\/icon\]/gi,
            '<a href="$1"><img src="https://www.google.com/s2/favicons?domain=$1"> $1</a>'
        );
    }
}

function escapeHtml(unsafe) {
    return unsafe
        .toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&apos;");
}

function createRenderer() {
    const renderer = new marked.Renderer();
    renderer.image = (href, title, alt) => {
        let [_, url, width, height] =
            /^(.+?)(?:\s=\s*(\d*)\s*x\s*(\d*)\s*)?$/.exec(href);
        let res = '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(alt);
        if (width) {
            res += '" width="' + width;
        }
        if (height) {
            res += '" height="' + height;
        }
        return res + '">';
    };
    return renderer;
}

// settings.strikethrough === false: "~" / "~~" render as plain tildes (notes).
function formatMarkdown(text, settings) {
    const strikethrough = !settings || settings.strikethrough !== false;
    const renderer = createRenderer();
    const options = {
        renderer: renderer,
        breaks: true,
        smartypants: true,
        headerIds: false,
    };
    let wrappers = [
        new SjisWrapper(),
        strikethrough ? new TildeWrapper() : new NoStrikeThroughWrapper(),
        new TagPermalinkFixWrapper(),
        new EntityPermalinkWrapper(),
        new SearchPermalinkWrapper(),
        new SpoilersWrapper(),
        new SmallWrapper(),
        strikethrough ? new StrikeThroughWrapper() : null,
        new FaviconWrapper(),
    ].filter((wrapper) => wrapper !== null);
    text = escapeHtml(text);
    for (let wrapper of wrappers) {
        text = wrapper.preprocess(text);
    }
    text = marked.parse(text, options);
    wrappers.reverse();
    for (let wrapper of wrappers) {
        text = wrapper.postprocess(text);
    }
    return DOMPurify.sanitize(text);
}

function formatInlineMarkdown(text) {
    const renderer = createRenderer();
    const options = {
        renderer: renderer,
        breaks: true,
        smartypants: true,
        headerIds: false,
    };
    let wrappers = [
        new TildeWrapper(),
        new EntityPermalinkWrapper(),
        new SearchPermalinkWrapper(),
        new SpoilersWrapper(),
        new SmallWrapper(),
        new StrikeThroughWrapper(),
        new FaviconWrapper(),
    ];
    text = escapeHtml(text);
    for (let wrapper of wrappers) {
        text = wrapper.preprocess(text);
    }
    text = marked.parseInline(text, options);
    wrappers.reverse();
    for (let wrapper of wrappers) {
        text = wrapper.postprocess(text);
    }
    return DOMPurify.sanitize(text);
}

module.exports = {
    formatMarkdown: formatMarkdown,
    formatInlineMarkdown: formatInlineMarkdown,
    escapeHtml: escapeHtml,
};
