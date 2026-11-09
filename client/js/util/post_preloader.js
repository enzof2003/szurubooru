"use strict";

// Background preloading of the previous/next posts in the post view, so that
// arrow-key navigation can show them straight from the caches:
//  - post JSON and "around" JSON go into api.get()'s per-URL cache,
//  - post content (or just the thumbnail) goes into the browser's cache.
//
// Only one preload session is live at a time: starting a new one (i.e.
// navigating to another post) cancels the previous one, aborting its pending
// image downloads and ignoring its outstanding API responses.

const settings = require("../models/settings.js");
const views = require("./views.js");
const Post = require("../models/post.js");
const PostList = require("../models/post_list.js");

const API_OPTIONS = { noProgress: true };

let generation = 0;
let pendingImages = [];

function cancel() {
    generation++;
    for (const img of pendingImages) {
        // dropping the src aborts an in-flight download
        img.onload = img.onerror = null;
        img.src = "";
    }
    pendingImages = [];
}

function isFullContentPreloadable(post) {
    if (!["image", "animation"].includes(post.type)) {
        return false; // video / flash: thumbnail only
    }
    const limitMB = parseFloat(settings.get().preloadMaxFileSizeMB);
    const limit = (isNaN(limitMB) ? 15 : limitMB) * 1024 * 1024;
    return post.fileSize > 0 && post.fileSize <= limit;
}

function loadImage(url, gen) {
    return new Promise((resolve) => {
        if (gen !== generation || !url) {
            resolve();
            return;
        }
        const img = new Image();
        img.decoding = "async";
        const done = () => {
            pendingImages = pendingImages.filter((i) => i !== img);
            resolve();
        };
        img.onload = done;
        img.onerror = done;
        pendingImages.push(img);
        img.src = url;
    });
}

function preloadPost(postId, query, gen) {
    if (!postId || gen !== generation) {
        return Promise.resolve();
    }
    return Post.get(postId, API_OPTIONS)
        .then((post) => {
            if (gen !== generation) {
                return;
            }
            // the neighbour's own neighbours, so that the step after the next
            // one is ready too (same URL the next controller will request)
            PostList.getAround(postId, query, API_OPTIONS).catch(() => {});
            return loadImage(
                isFullContentPreloadable(post)
                    ? post.contentUrl
                    : post.thumbnailUrl,
                gen
            );
        })
        .catch(() => {});
}

// Resolves once the current post's content node has loaded (or failed), so
// that preloading never competes with what the user is looking at.
function whenContentLoaded(contentNode) {
    return new Promise((resolve) => {
        if (!contentNode) {
            resolve();
            return;
        }
        const img = contentNode.querySelector("img");
        const video = contentNode.querySelector("video");
        if (img) {
            if (img.complete) {
                resolve();
            } else {
                img.addEventListener("load", resolve, { once: true });
                img.addEventListener("error", resolve, { once: true });
            }
        } else if (video) {
            // HAVE_FUTURE_DATA: enough is buffered for playback to proceed
            if (video.readyState >= 3) {
                resolve();
            } else {
                video.addEventListener("canplay", resolve, { once: true });
                video.addEventListener("error", resolve, {
                    once: true,
                    capture: true,
                });
            }
        } else {
            // flash: no reliable load event; give it a head start
            window.setTimeout(resolve, 2000);
        }
    });
}

// Start a preload session for the post being shown. `contentNode` is the
// current post's .post-content element.
function start(contentNode, prevPostId, nextPostId, query) {
    cancel();
    if (!settings.get().preloadNeighbours) {
        return;
    }
    if (!prevPostId && !nextPostId) {
        return;
    }
    const gen = generation;
    // leaving the post view (to a non-post page) ends the session too
    const container = contentNode && contentNode.closest(".post-container");
    if (container) {
        views.monitorNodeRemoval(container, () => {
            if (gen === generation) {
                cancel();
            }
        });
    }
    whenContentLoaded(contentNode).then(() => {
        if (gen !== generation) {
            return;
        }
        // forward is the more common direction: next first, then previous
        preloadPost(nextPostId, query, gen).then(() =>
            preloadPost(prevPostId, query, gen)
        );
    });
}

module.exports = {
    start: start,
    cancel: cancel,
};
