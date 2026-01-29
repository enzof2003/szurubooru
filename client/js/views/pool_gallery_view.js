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

        this._items = Array.from(document.querySelectorAll(".gallery-item"));
        this._setupKeyboardNavigation();
        this._setupClickNavigation();
    }

    _setupKeyboardNavigation() {
        document.addEventListener("keydown", (e) => {
            const key = e.key.toLowerCase();

            // Only prevent default for arrow keys
            if (key === "arrowdown" || key === "arrowup") {
                e.preventDefault();
            }

            if (!["arrowdown", "arrowup", "w", "s"].includes(key)) return;

            const viewportCenter = window.innerHeight / 2;

            // Find the item closest to the center
            let closestIndex = 0;
            let closestDistance = Infinity;

            this._items.forEach((item, index) => {
                const rect = item.getBoundingClientRect();
                const itemCenter = rect.top + rect.height / 2;
                const distance = Math.abs(itemCenter - viewportCenter);

                if (distance < closestDistance) {
                    closestDistance = distance;
                    closestIndex = index;
                }
            });

            if (key === "arrowdown" || key === "s") {
                if (closestIndex < this._items.length - 1) {
                    this._items[closestIndex + 1].scrollIntoView({
                        behavior: "smooth",
                        block: "center"
                    });
                }
            }

            if (key === "arrowup" || key === "w") {
                if (closestIndex > 0) {
                    this._items[closestIndex - 1].scrollIntoView({
                        behavior: "smooth",
                        block: "center"
                    });
                }
            }
        });
    }


    _setupClickNavigation() {
        this._items.forEach((item) => {
            const postId = item.dataset.postId;

            item.addEventListener("click", () => {
                window.location.href = `/post/${postId}`;
            });
        });
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
