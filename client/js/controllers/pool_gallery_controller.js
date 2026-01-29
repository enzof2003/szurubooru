"use strict";

const router = require("../router.js");
const api = require("../api.js");
const uri = require("../util/uri.js");
const PoolGalleryView = require("../views/pool_gallery_view.js");
const EmptyView = require("../views/empty_view.js");

class PoolGalleryController {
    constructor(ctx) {
        console.log("GALLERY CONTROLLER: constructor", ctx.parameters);

        this._poolId = ctx.parameters.id;
        this._view = new EmptyView();

        // 1. Load the pool
        api.get(uri.formatApiLink("pool/" + this._poolId)).then(
            (pool) => {
                console.log("GALLERY CONTROLLER: pool loaded", pool);

                this._pool = pool;

                // 2. Load all posts in the pool
                return Promise.all(
                    (pool.posts || []).map((p) =>
                        api.get(uri.formatApiLink("post/" + p.id))
                    )
                );
            },
            (error) => {
                this._view = new EmptyView();
                this._view.showError(error.message);
            }
        ).then(
            (posts) => {
                if (!posts) return;

                document.title = `Tengu Futaket - Pool ${this._pool.id} Gallery`;

                // 3. Render the view (still the pool-create form for now)
                this._view = new PoolGalleryView({
                    hostNode: ctx.hostNode,
                    pool: this._pool,
                    posts: posts,
                });
            },
            (error) => {
                this._view = new EmptyView();
                this._view.showError(error.message);
            }
        );
    }
}

module.exports = (router) => {
    router.enter(["pool", ":id", "gallery"], (ctx, next) => {
        ctx.controller = new PoolGalleryController(ctx);
    });
};


