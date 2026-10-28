"use strict";

const events = require("../events.js");
const settings = require("../models/settings.js");
const keyboard = require("../util/keyboard.js");
const misc = require("../util/misc.js");
const search = require("../util/search.js");
const views = require("../util/views.js");
const TagAutoCompleteControl = require("../controls/tag_auto_complete_control.js");

const template = views.getTemplate("posts-header");

class PostsHeaderView extends events.EventTarget {
    constructor(ctx) {
        super();

        ctx.settings = settings.get();
        this._ctx = ctx;
        this._hostNode = ctx.hostNode;
        views.replaceContent(this._hostNode, template(ctx));

        this._autoCompleteControl = new TagAutoCompleteControl(
            this._queryInputNode,
            {
                confirm: (tag) =>
                    this._autoCompleteControl.replaceSelectedText(
                        misc.escapeSearchTerm(tag.matchingNames[0]),
                        true
                    ),
                isNegationAllowed: true,
            }
        );

        keyboard.bind("p", () => this._focusFirstPostNode());
        search.searchInputNodeFocusHelper(this._queryInputNode);

        for (let safetyButtonNode of this._safetyButtonNodes) {
            safetyButtonNode.addEventListener("click", (e) =>
                this._evtSafetyButtonClick(e)
            );
        }
        this._formNode.addEventListener("submit", (e) =>
            this._evtFormSubmit(e)
        );

        if (this._editModeToggleNode) {
            this._editModeToggleNode.addEventListener("click", (e) =>
                this._evtEditModeToggleClick(e)
            );
        }

        for (let modeNode of this._batchModeNodes) {
            modeNode.addEventListener("click", (e) =>
                this._evtBatchModeClick(e, modeNode)
            );
        }
    }

    get _headerNode() {
        return this._hostNode.querySelector(".post-list-header");
    }

    get _formNode() {
        return this._hostNode.querySelector("form.search");
    }

    get _safetyButtonNodes() {
        return this._hostNode.querySelectorAll("form .safety");
    }

    get _queryInputNode() {
        return this._hostNode.querySelector("form [name=search-text]");
    }

    get _editModeToggleNode() {
        return this._hostNode.querySelector(".edit-mode-toggle");
    }

    get _batchModeNodes() {
        return this._hostNode.querySelectorAll(".batch-mode");
    }

    get editMode() {
        return this._headerNode.classList.contains("edit-mode");
    }

    // Edit mode only reveals the batch tools and arms drag-select; it is not
    // part of the search, so it is applied without reloading the page.
    _evtEditModeToggleClick(e) {
        e.preventDefault();
        const enabled = !this.editMode;
        this._headerNode.classList.toggle("edit-mode", enabled);
        this._editModeToggleNode.classList.toggle("active", enabled);

        let browsingSettings = settings.get();
        browsingSettings.editMode = enabled;
        settings.save(browsingSettings, true);

        this.dispatchEvent(
            new CustomEvent("changeEditMode", {
                detail: { editMode: enabled },
            })
        );

        // leaving edit mode drops the batch mode with it
        if (!enabled && this._ctx.mode) {
            this._selectMode(null);
        }
    }

    // Exactly one batch mode is active at a time: it decides what the
    // selection bar offers, so clicking the active one turns it off.
    _evtBatchModeClick(e, modeNode) {
        e.preventDefault();
        const mode = modeNode.getAttribute("data-mode");
        this._selectMode(this._ctx.mode === mode ? null : mode);
    }

    _selectMode(mode) {
        this._ctx.mode = mode;
        for (let modeNode of this._batchModeNodes) {
            modeNode.classList.toggle(
                "active",
                modeNode.getAttribute("data-mode") === mode
            );
        }
        this.dispatchEvent(
            new CustomEvent("changeMode", { detail: { mode: mode } })
        );
    }

    _evtSafetyButtonClick(e, url) {
        e.preventDefault();
        e.target.classList.toggle("disabled");
        const safety = e.target.getAttribute("data-safety");
        let browsingSettings = settings.get();
        browsingSettings.listPosts[safety] =
            !browsingSettings.listPosts[safety];
        settings.save(browsingSettings, true);
        this.dispatchEvent(
            new CustomEvent("navigate", {
                detail: {
                    parameters: Object.assign({}, this._ctx.parameters, {
                        offset: 0,
                    }),
                },
            })
        );
    }

    _evtFormSubmit(e) {
        e.preventDefault();
        this._navigate();
    }

    _navigate() {
        this._autoCompleteControl.hide();
        let parameters = { query: this._queryInputNode.value };

        // convert falsy values to an empty string "" so that we can correctly compare with the current query
        const prevQuery = this._ctx.parameters.query
            ? this._ctx.parameters.query
            : "";
        parameters.offset =
            parameters.query === prevQuery ? this._ctx.parameters.offset : 0;
        this.dispatchEvent(
            new CustomEvent("navigate", { detail: { parameters: parameters } })
        );
    }

    _focusFirstPostNode() {
        const firstPostNode = document.body.querySelector(
            ".post-list li:first-child a"
        );
        if (firstPostNode) {
            firstPostNode.focus();
        }
    }
}

module.exports = PostsHeaderView;
