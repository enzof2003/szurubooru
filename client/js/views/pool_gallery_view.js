"use strict";

const events = require("../events.js");
const views = require("../util/views.js");
const template = views.getTemplate("pool-gallery");

class PoolGalleryView extends events.EventTarget {
    constructor(ctx) {
        super();

        this._hostNode = document.getElementById("content-holder");
        this._pool = ctx.pool;
        this._posts = ctx.posts;

        views.replaceContent(
            this._hostNode,
            template({
                pool: this._pool,
                posts: this._posts,
            })
        );
    }

    clearMessages() {
        views.clearMessages(this._hostNode);
    }

    showError(message) {
        views.showError(this._hostNode, message);
    }

    showSuccess(message) {
        views.showSuccess(this._hostNode, message);
    }
}

module.exports = PoolGalleryView;
