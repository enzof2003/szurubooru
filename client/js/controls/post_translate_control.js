"use strict";

const api = require("../api.js");
const uri = require("../util/uri.js");
const views = require("../util/views.js");

const POLL_INTERVAL_MS = 4000;
// A failed job is still reported when coming back to the post within this
// window; older failures are not worth a message on every visit.
const SHOW_FAILURE_FOR_MS = 60 * 60 * 1000;

const ACTIVE = ["pending", "running"];

// The "Translate" button in the post sidebar's top navigation row.
// The job itself runs on the server; this only queues it and polls status,
// so the user can leave the page at any time.
class PostTranslateControl {
    constructor(buttonNode, statusNode, post, editMode) {
        this._buttonNode = buttonNode;
        this._linkNode = buttonNode.querySelector("a");
        this._iconNode = buttonNode.querySelector("i");
        this._statusNode = statusNode;
        this._post = post;
        this._editMode = editMode;
        this._pollTimer = null;
        this._watching = false; // saw this job active while on the page

        this._linkNode.addEventListener("click", (e) => this._evtClick(e));
        this._fetchStatus(true);
    }

    get _url() {
        return uri.formatApiLink("post", this._post.id, "translate");
    }

    _isAttached() {
        return document.body.contains(this._buttonNode);
    }

    _setBusy(busy) {
        this._busy = busy;
        this._iconNode.className = busy
            ? "fa fa-circle-o-notch fa-spin"
            : "fa fa-language";
        this._linkNode.classList.toggle("inactive", busy);
        this._linkNode.title = busy ? "Translation in progress…" : "Translate";
    }

    _message(kind, text) {
        views.clearMessages(this._statusNode);
        if (kind) {
            views[kind](this._statusNode, text);
        }
    }

    _evtClick(e) {
        e.preventDefault();
        if (this._busy) {
            return;
        }
        this._setBusy(true);
        this._message("showInfo", "Translation in progress…");
        api.post(this._url, {}).then(
            (job) => {
                this._watching = true;
                this._render(job, false);
            },
            (error) => {
                this._setBusy(false);
                this._message("showError", error.message);
                // e.g. someone else already queued it: show that job instead
                this._fetchStatus(false);
            }
        );
    }

    _fetchStatus(initial) {
        if (!this._isAttached()) {
            return;
        }
        delete api.cache[this._url]; // api.get() caches by URL; we poll
        api.get(this._url, { noProgress: true }).then(
            (job) => this._render(job, initial),
            (error) => {
                if (!initial) {
                    // transient (server restarting?): keep polling
                    this._schedulePoll();
                }
            }
        );
    }

    _schedulePoll() {
        clearTimeout(this._pollTimer);
        this._pollTimer = setTimeout(
            () => this._fetchStatus(false),
            POLL_INTERVAL_MS
        );
    }

    _render(job, initial) {
        if (!this._isAttached() || !job) {
            return;
        }
        if (ACTIVE.includes(job.status)) {
            this._watching = true;
            this._setBusy(true);
            let text = "Translation in progress…";
            if (job.status === "pending" && job.queuePosition) {
                text +=
                    " (" +
                    job.queuePosition +
                    (job.queuePosition === 1 ? " job" : " jobs") +
                    " ahead)";
            }
            this._message("showInfo", text);
            this._schedulePoll();
            return;
        }

        this._setBusy(false);
        if (job.status === "failed") {
            const ended = Date.parse(job.endTime);
            if (
                this._watching ||
                (initial && Date.now() - ended < SHOW_FAILURE_FOR_MS)
            ) {
                this._message(
                    "showError",
                    "Translation failed: " + (job.error || "unknown error")
                );
            }
        } else if (job.status === "done" && this._watching) {
            this._watching = false;
            this._showDone(job);
        } else if (!initial) {
            this._message(null);
        }
    }

    _showDone(job) {
        let text =
            "Translation done: " +
            job.notesAdded +
            (job.notesAdded === 1 ? " note" : " notes") +
            " added";
        if (job.notesReplaced) {
            text += " (" + job.notesReplaced + " [MT?] replaced)";
        }
        if (job.notesSkipped) {
            text += ", " + job.notesSkipped + " skipped";
        }
        text += ".";
        if (!job.notesAdded && !job.notesReplaced) {
            this._message("showSuccess", text);
            return;
        }
        if (this._editMode) {
            // Don't clobber unsaved edits; the post version changed anyway.
            this._message(
                "showSuccess",
                text + " Reload the page to see the new notes."
            );
            return;
        }
        this._post.refresh().then(
            () => this._message("showSuccess", text),
            (error) => this._message("showError", error.message)
        );
    }
}

module.exports = PostTranslateControl;
