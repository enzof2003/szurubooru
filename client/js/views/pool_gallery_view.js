"use strict";

const events = require("../events.js");
const views = require("../util/views.js");
const settings = require("../models/settings.js");
const Post = require("../models/post.js");
const PostNotesOverlayControl = require("../controls/post_notes_overlay_control.js");
const template = views.getTemplate("pool-gallery");

const SHOW_NOTES_STORAGE_KEY = "poolGalleryShowNotes";

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
        this._setupNotes();
    }

    // Image posts with notes get the post view's notes overlay over their
    // picture (markup in pool_gallery.tpl). The overlay ignores clicks and the
    // note boxes' clicks bubble to .gallery-item, so a click still opens the
    // post; hovering a box shows its text.
    _setupNotes() {
        const postsById = new Map(this._posts.map((p) => [String(p.id), p]));
        this._notesOverlays = this._items
            .filter((item) => item.querySelector(".gallery-media-notes"))
            .map(
                (item) =>
                    new PostNotesOverlayControl(
                        item.querySelector(".post-overlay"),
                        Post.fromResponse(postsById.get(item.dataset.postId)),
                        { clickThroughText: true }
                    )
            );

        const checkbox = this._hostNode.querySelector(
            ".gallery-notes-toggle input"
        );
        if (!checkbox) {
            return;
        }
        let visible = settings.get().showNotes;
        try {
            const stored = localStorage.getItem(SHOW_NOTES_STORAGE_KEY);
            if (stored !== null) {
                visible = stored === "true";
            }
        } catch (e) {
            // storage unavailable: fall back to the "Show notes" setting
        }
        checkbox.checked = visible;
        this._setNotesVisible(visible);
        checkbox.addEventListener("change", () => {
            this._setNotesVisible(checkbox.checked);
            try {
                localStorage.setItem(
                    SHOW_NOTES_STORAGE_KEY,
                    String(checkbox.checked)
                );
            } catch (e) {
                // not remembered, still applied for this page
            }
        });
    }

    _setNotesVisible(visible) {
        for (let overlay of this._notesOverlays) {
            overlay.setNotesVisible(visible);
        }
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
