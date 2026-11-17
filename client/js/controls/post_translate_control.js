"use strict";

const api = require("../api.js");
const misc = require("../util/misc.js");
const uri = require("../util/uri.js");
const views = require("../util/views.js");

const POLL_INTERVAL_MS = 4000;
// A failed job is still reported when coming back to the post within this
// window; older failures are not worth a message on every visit.
const SHOW_FAILURE_FOR_MS = 60 * 60 * 1000;

const ACTIVE = ["pending", "running"];
const TAG_DONE = "machine_translated";

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
        this._doneHere = false; // a job finished while on this page

        this._linkNode.addEventListener("click", (e) => this._evtClick(e));
        // tags change when the post is saved (edit mode) or refreshed
        this._post.addEventListener("change", () => {
            this._doneHere = false; // saved post: its tags are the truth
            this._updateButton();
        });
        this._updateButton();
        this._fetchStatus(true);
    }

    get _url() {
        return uri.formatApiLink("post", this._post.id, "translate");
    }

    _isAttached() {
        return document.body.contains(this._buttonNode);
    }

    // The server refuses posts tagged machine_translated (409); removing the
    // tag is how a user asks for a fresh translation.
    get _translated() {
        return this._doneHere || this._post.tags.isTaggedWith(TAG_DONE);
    }

    _setBusy(busy) {
        this._busy = busy;
        this._updateButton();
    }

    _updateButton() {
        let icon = "fa fa-language";
        let title = "Translate";
        if (this._busy) {
            icon = "fa fa-circle-o-notch fa-spin";
            title = "Translation in progress…";
        } else if (this._translated) {
            icon = "fa fa-check";
            title = "Translated ✓ (remove the " + TAG_DONE + " tag to redo)";
        }
        this._iconNode.className = icon;
        this._linkNode.classList.toggle(
            "inactive",
            this._busy || this._translated
        );
        this._linkNode.title = title;
    }

    _message(kind, text) {
        views.clearMessages(this._statusNode);
        if (kind) {
            views[kind](this._statusNode, text);
        }
    }

    _evtClick(e) {
        e.preventDefault();
        if (this._busy || this._translated) {
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
                // (and otherwise leave the error on screen)
                this._fetchStatus(false, true);
            }
        );
    }

    // keepMessage: the message on screen (a failed click's error) stays
    // unless the latest job has something to say itself.
    _fetchStatus(initial, keepMessage) {
        if (!this._isAttached()) {
            return;
        }
        delete api.cache[this._url]; // api.get() caches by URL; we poll
        api.get(this._url, { noProgress: true }).then(
            (job) => this._render(job, initial, keepMessage),
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

    // The post JSON on this page can predate a job that finished in the
    // background: api.get() caches per URL and the post preloader fetches
    // neighbours early. Such a post has no MT notes, no machine_translated
    // tag, and was last edited before the job ended (the job's own merge is
    // the edit that follows; removing the tag later is a newer edit).
    _isStale(job) {
        if (job.status !== "done" || this._translated || !job.endTime) {
            return false;
        }
        const edited = Date.parse(this._post.lastEditTime);
        return isNaN(edited) || edited < Date.parse(job.endTime);
    }

    _refreshStalePost() {
        if (this._editMode) {
            // don't clobber the edit form; saving would hit a version
            // conflict anyway
            this._message(
                "showInfo",
                "This post was machine-translated after this page was " +
                    "loaded. Reload the page to see the notes."
            );
            return;
        }
        this._post.refresh().then(
            () => this._updateButton(),
            () => {} // still usable; the server refuses a second run
        );
    }

    _render(job, initial, keepMessage) {
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
                // views.showError() takes HTML, and job.error can quote
                // the translator's or the LLM provider's reply verbatim
                this._message(
                    "showError",
                    "Translation failed: " +
                        misc.escapeHtml(job.error || "unknown error")
                );
            }
        } else if (job.status === "done" && this._watching) {
            this._watching = false;
            this._showDone(job);
        } else {
            if (!initial && !keepMessage) {
                this._message(null);
            }
            if (initial && this._isStale(job)) {
                this._refreshStalePost();
            }
        }
    }

    _showDone(job) {
        let text =
            "Translation done: " +
            job.notesAdded +
            (job.notesAdded === 1 ? " note" : " notes") +
            " added";
        if (job.notesReplaced) {
            text += " (" + job.notesReplaced + " previous MT notes replaced)";
        }
        if (job.notesSkipped) {
            text += ", " + job.notesSkipped + " skipped";
        }
        text += ".";
        // A finished run always tags the post machine_translated.
        this._doneHere = true;
        this._updateButton();
        if (this._editMode) {
            // Don't clobber unsaved edits; the post version changed anyway.
            this._message(
                "showSuccess",
                text + " Reload the page to see the new notes."
            );
            return;
        }
        this._post.refresh().then(
            () => {
                this._doneHere = false; // the refreshed tags are the truth
                this._updateButton();
                this._message("showSuccess", text);
            },
            (error) => this._message("showError", error.message)
        );
    }
}

module.exports = PostTranslateControl;
