"use strict";

const events = require("../events.js");
const keyboard = require("../util/keyboard.js");
const views = require("../util/views.js");
const PostSelectionControl = require("../controls/post_selection_control.js");
const TagAutoCompleteControl = require("../controls/tag_auto_complete_control.js");

const template = views.getTemplate("posts-page");

const SAFETIES = ["safe", "sketchy", "unsafe"];

class PostsPageView extends events.EventTarget {
    constructor(ctx) {
        super();
        this._ctx = ctx;
        this._hostNode = ctx.hostNode;
        views.replaceContent(this._hostNode, template(ctx));

        this._postIdToPost = {};
        for (let post of ctx.response.results) {
            this._postIdToPost[post.id] = post;
            post.addEventListener("change", (e) => this._evtPostChange(e));
        }

        this._postIdToListItemNode = {};
        for (let listItemNode of this._listItemNodes) {
            const postId = listItemNode.getAttribute("data-post-id");
            const post = this._postIdToPost[postId];
            this._postIdToListItemNode[postId] = listItemNode;

            const deleteFlipperNode = this._getDeleteFlipperNode(listItemNode);
            if (deleteFlipperNode) {
                deleteFlipperNode.addEventListener("click", (e) =>
                    this._evtMarkForDeletionClick(e, post)
                );
            }
        }

        this._chosenSafety = null;
        this._deleteConfirming = false;
        this._poolPhase = "naming";
        this._poolOrder = [];

        this._selectionControl = null;
        if (ctx.canSelect && this._postListNode) {
            this._initSelection();
        }
        this._syncDeleteMarks();
    }

    // ---- nodes -----------------------------------------------------------

    get _listItemNodes() {
        return this._hostNode.querySelectorAll("li");
    }

    get _postListNode() {
        return this._hostNode.querySelector(".post-list");
    }

    get _barNode() {
        return this._hostNode.querySelector(".post-selection-bar");
    }

    get _bulkTagInputNode() {
        return this._hostNode.querySelector("input[name=bulk-tag]");
    }

    get _poolFormNode() {
        return this._hostNode.querySelector(".create-pool-from-selection");
    }

    get _customOrderToggleNode() {
        return this._hostNode.querySelector("input[name=custom-order]");
    }

    get _poolOrderInputNode() {
        return this._hostNode.querySelector("input[name=pool-order]");
    }

    _getDeleteFlipperNode(listItemNode) {
        return listItemNode.querySelector(".delete-flipper");
    }

    _panelNode(mode) {
        return this._hostNode.querySelector(`.panel-${mode || "none"}`);
    }

    // ---- selection -------------------------------------------------------

    _initSelection() {
        this._selectionControl = new PostSelectionControl({
            // the whole content area, so a drag can start in the margin beside
            // the grid rather than only in the gaps between thumbnails
            surfaceNode:
                document.getElementById("content-holder") ||
                this._postListNode,
            itemsNode: this._postListNode,
            enabled: this._ctx.editMode,
        });
        this._selectionControl.addEventListener("change", () =>
            this._evtSelectionChange()
        );

        const barNode = this._barNode;
        if (!barNode) {
            return;
        }

        const bind = (selector, handler) => {
            for (let node of barNode.querySelectorAll(selector)) {
                node.addEventListener("click", (e) => {
                    e.preventDefault();
                    handler(e, node);
                });
            }
        };

        bind(".select-all", () => this._selectionControl.selectAll());
        bind(".clear-selection", () => this._selectionControl.clear());

        bind(".apply-tags", () => this._evtApplyTags());
        bind(".safety-picker a", (e, node) =>
            this._chooseSafety(node.getAttribute("data-safety"))
        );
        bind(".apply-safety", () => this._evtApplySafety());

        bind(".request-delete", () => this._setDeleteConfirming(true));
        bind(".cancel-delete", () => this._setDeleteConfirming(false));
        bind(".confirm-delete", () => this._evtConfirmDelete());
        bind(".clear-marks", () => this._evtClearMarks());

        bind(".start-pool-order", () => this._enterPoolOrdering());
        bind(".cancel-pool-order", () => this._leavePoolOrdering());
        bind(".swap-pool-order", () => this._swapPoolOrder());

        const tagInputNode = this._bulkTagInputNode;
        if (tagInputNode) {
            this._tagAutoCompleteControl = new TagAutoCompleteControl(
                tagInputNode,
                {
                    confirm: (tag) =>
                        this._tagAutoCompleteControl.replaceSelectedText(
                            tag.names[0],
                            true
                        ),
                }
            );
            // keep the tag list in the url so it survives changing page
            tagInputNode.addEventListener("change", () =>
                this.dispatchEvent(
                    new CustomEvent("changeBulkTags", {
                        detail: { tags: this._bulkTags },
                    })
                )
            );
        }

        const customOrderNode = this._customOrderToggleNode;
        if (customOrderNode) {
            customOrderNode.addEventListener("change", () =>
                this._syncCustomOrderField()
            );
        }
        const orderInputNode = this._poolOrderInputNode;
        if (orderInputNode) {
            orderInputNode.addEventListener("input", () =>
                this._evtPoolOrderInput()
            );
        }

        const poolFormNode = this._poolFormNode;
        if (poolFormNode) {
            poolFormNode.addEventListener("submit", (e) =>
                this._evtPoolSubmit(e)
            );
        }

        keyboard.bind("esc", () => {
            if (this._deleteConfirming) {
                this._setDeleteConfirming(false);
            } else if (this._selectionControl.count) {
                this._selectionControl.clear();
            }
        });

        this._syncBar();
    }

    get selectedPosts() {
        if (!this._selectionControl) {
            return [];
        }
        return this._selectionControl.selectedIds
            .map((postId) => this._postIdToPost[postId])
            .filter((post) => post);
    }

    clearSelection() {
        if (this._selectionControl) {
            this._selectionControl.clear();
        }
    }

    setEditMode(state) {
        this._ctx.editMode = state;
        if (this._selectionControl) {
            this._selectionControl.setEnabled(state);
        }
        if (!state) {
            this._resetTransientState();
            this._syncBar();
        }
    }

    _resetTransientState() {
        this._deleteConfirming = false;
        this._leavePoolOrdering();
    }

    _evtSelectionChange() {
        if (this._deleteConfirming) {
            // the set being confirmed just changed under us
            this._deleteConfirming = false;
        }
        if (this._poolPhase === "ordering") {
            this._reconcilePoolOrder();
        }
        this._syncBar();
    }

    // ---- the bar ---------------------------------------------------------

    get _mode() {
        return this._ctx.mode || null;
    }

    get _markedPosts() {
        return (this._ctx.bulkEdit && this._ctx.bulkEdit.markedForDeletion) || [];
    }

    // Deleting acts on the selection plus anything marked with the trash
    // overlay, which is how a delete list can span several pages.
    get _deleteTargets() {
        const seen = new Set();
        const targets = [];
        for (let post of this.selectedPosts.concat(this._markedPosts)) {
            if (!seen.has(post.id)) {
                seen.add(post.id);
                targets.push(post);
            }
        }
        return targets;
    }

    get _bulkTags() {
        const node = this._bulkTagInputNode;
        return node ? node.value.split(/\s+/).filter((tag) => tag) : [];
    }

    _syncBar() {
        const barNode = this._barNode;
        if (!barNode) {
            return;
        }
        const count = this._selectionControl
            ? this._selectionControl.count
            : 0;
        const marked = this._markedPosts.length;
        const mode = this._mode;

        const countNode = barNode.querySelector(".count");
        if (countNode) {
            countNode.textContent = count;
        }
        const markedSummaryNode = barNode.querySelector(".marked-summary");
        if (markedSummaryNode) {
            markedSummaryNode.hidden = !marked;
            const markedCountNode =
                markedSummaryNode.querySelector(".marked-count");
            if (markedCountNode) {
                markedCountNode.textContent = marked;
            }
        }

        // in delete mode a cross-page list of marks is reason enough to show
        const relevant = count > 0 || (mode === "delete" && marked > 0);
        barNode.hidden = !this._ctx.editMode || !relevant;

        for (let panelNode of barNode.querySelectorAll(".mode-panel")) {
            panelNode.hidden = true;
        }
        const panelNode = this._panelNode(mode);
        if (panelNode) {
            panelNode.hidden = false;
        }

        this._syncSafetyPanel();
        this._syncDeletePanel();
        this._syncPoolPanel();

        if (!relevant) {
            this._resetTransientState();
        }
    }

    // ---- mass tag --------------------------------------------------------

    _evtApplyTags() {
        const tags = this._bulkTags;
        const posts = this.selectedPosts;
        if (!tags.length || !posts.length) {
            return;
        }
        this.dispatchEvent(
            new CustomEvent("applyTags", {
                detail: { posts: posts, tags: tags },
            })
        );
    }

    // ---- mass safety -----------------------------------------------------

    _chooseSafety(safety) {
        this._chosenSafety = safety;
        this._syncSafetyPanel();
    }

    _syncSafetyPanel() {
        const panelNode = this._panelNode("safety");
        if (!panelNode) {
            return;
        }
        for (let node of panelNode.querySelectorAll(".safety-picker a")) {
            node.classList.toggle(
                "active",
                node.getAttribute("data-safety") === this._chosenSafety
            );
        }
        const chosenNode = panelNode.querySelector(".safety-chosen");
        if (chosenNode) {
            chosenNode.textContent = this._chosenSafety
                ? `set to ${this._chosenSafety}`
                : "no rating picked";
        }
        for (let node of panelNode.querySelectorAll(".apply-safety")) {
            node.classList.toggle("disabled", !this._chosenSafety);
        }
    }

    _evtApplySafety() {
        const posts = this.selectedPosts;
        if (!this._chosenSafety || !posts.length) {
            return;
        }
        this.dispatchEvent(
            new CustomEvent("applySafety", {
                detail: { posts: posts, safety: this._chosenSafety },
            })
        );
    }

    // ---- mass delete -----------------------------------------------------

    _setDeleteConfirming(state) {
        this._deleteConfirming = state && this._deleteTargets.length > 0;
        this._syncDeletePanel();
    }

    _syncDeletePanel() {
        const panelNode = this._panelNode("delete");
        if (!panelNode) {
            return;
        }
        const targets = this._deleteTargets;
        const marked = this._markedPosts.length;

        const noticeNode = panelNode.querySelector(".delete-notice");
        if (noticeNode) {
            noticeNode.classList.toggle("danger", this._deleteConfirming);
            if (this._deleteConfirming) {
                noticeNode.textContent =
                    `This permanently deletes ${targets.length} post(s) ` +
                    `and cannot be undone. Confirm to proceed.`;
            } else if (marked) {
                noticeNode.textContent =
                    `${targets.length} post(s) would be deleted: the ` +
                    `selection plus ${marked} marked with the trash icon.`;
            } else {
                noticeNode.textContent =
                    `${targets.length} selected post(s) would be deleted. ` +
                    `Use the trash icon to build a list across pages.`;
            }
        }

        const stage1 = panelNode.querySelector(".delete-stage-1");
        const stage2 = panelNode.querySelector(".delete-stage-2");
        if (stage1) {
            stage1.hidden = this._deleteConfirming;
        }
        if (stage2) {
            stage2.hidden = !this._deleteConfirming;
        }
        for (let node of panelNode.querySelectorAll(".request-delete")) {
            node.textContent = `Delete ${targets.length} post(s)`;
            node.classList.toggle("disabled", !targets.length);
        }
        for (let node of panelNode.querySelectorAll(".confirm-delete")) {
            node.textContent = `Yes, permanently delete ${targets.length} post(s)`;
        }
        for (let node of panelNode.querySelectorAll(".clear-marks")) {
            node.hidden = !marked;
        }
    }

    _evtConfirmDelete() {
        const posts = this._deleteTargets;
        if (!posts.length) {
            return;
        }
        this._deleteConfirming = false;
        this.dispatchEvent(
            new CustomEvent("deleteSelected", { detail: { posts: posts } })
        );
    }

    _evtClearMarks() {
        this.dispatchEvent(new CustomEvent("clearDeleteMarks", { detail: {} }));
    }

    _evtMarkForDeletionClick(e, post) {
        e.preventDefault();
        const linkNode = e.target;
        linkNode.classList.toggle("delete");
        this.dispatchEvent(
            new CustomEvent("markForDeletion", {
                detail: {
                    post: post,
                    delete: linkNode.classList.contains("delete"),
                },
            })
        );
    }

    // Called by the controller after it changed state the view only sees
    // through its render context (the deletion list).
    refreshHighlights() {
        this._syncDeleteMarks();
        this._syncBar();
    }

    _syncDeleteMarks() {
        const markedIds = new Set(this._markedPosts.map((post) => post.id));
        for (let listItemNode of this._listItemNodes) {
            const postId = listItemNode.getAttribute("data-post-id");
            const flipperNode = this._getDeleteFlipperNode(listItemNode);
            if (flipperNode) {
                flipperNode.classList.toggle(
                    "delete",
                    markedIds.has(Number(postId)) || markedIds.has(postId)
                );
            }
        }
    }

    // ---- pool creation ---------------------------------------------------

    _syncPoolPanel() {
        const panelNode = this._panelNode("pool");
        if (!panelNode) {
            return;
        }
        const ordering = this._poolPhase === "ordering";

        const namingRow = panelNode.querySelector(".pool-naming");
        const orderingRow = panelNode.querySelector(".pool-ordering");
        if (namingRow) {
            namingRow.hidden = ordering;
        }
        if (orderingRow) {
            orderingRow.hidden = !ordering;
        }
        for (let node of panelNode.querySelectorAll(".start-pool-order")) {
            node.hidden = ordering;
        }
        for (let node of panelNode.querySelectorAll(
            ".pool-confirm-buttons"
        )) {
            node.hidden = !ordering;
        }
        for (let node of panelNode.querySelectorAll(".pool-hint")) {
            node.hidden = !ordering;
        }

        const summaryNode = panelNode.querySelector(".pool-summary");
        if (summaryNode) {
            summaryNode.textContent = ordering ? this._poolDescription() : "";
        }
    }

    _poolDescription() {
        const panelNode = this._panelNode("pool");
        const nameNode = panelNode.querySelector("input[name=pool-name]");
        const categoryNode = panelNode.querySelector(
            "select[name=pool-category]"
        );
        const name = nameNode ? nameNode.value.trim() : "";
        const category = categoryNode ? categoryNode.value : "";
        return category ? `${name} (${category})` : name;
    }

    _enterPoolOrdering() {
        const panelNode = this._panelNode("pool");
        if (!panelNode) {
            return;
        }
        const nameNode = panelNode.querySelector("input[name=pool-name]");
        const categoryNode = panelNode.querySelector(
            "select[name=pool-category]"
        );
        const name = nameNode ? nameNode.value.trim() : "";
        if (!name) {
            if (nameNode) {
                nameNode.focus();
            }
            return;
        }
        if (!categoryNode || !categoryNode.value) {
            // never silently post an empty category - the server rejects it
            // with a confusing "Pool category '' not found"
            this.dispatchEvent(
                new CustomEvent("error", {
                    detail: {
                        message:
                            "No pool category is available, so the pool " +
                            "cannot be created. Check that pool categories " +
                            "exist and that you may list them.",
                    },
                })
            );
            return;
        }
        if (!this.selectedPosts.length) {
            return;
        }

        this._poolPhase = "ordering";
        this._poolOrder = this._selectionControl.selectedIds.slice();
        this._syncPoolOrderField();
        this._syncPoolIndexBadges();
        this._syncPoolPanel();
    }

    _leavePoolOrdering() {
        this._poolPhase = "naming";
        this._poolOrder = [];
        const customOrderNode = this._customOrderToggleNode;
        if (customOrderNode) {
            customOrderNode.checked = false;
        }
        this._syncCustomOrderField();
        this._syncPoolIndexBadges();
        this._syncPoolPanel();
    }

    // Keeps the arrangement made so far, dropping posts that left the
    // selection and appending newly selected ones in page order.
    _reconcilePoolOrder() {
        const selected = this._selectionControl.selectedIds;
        if (!selected.length) {
            this._leavePoolOrdering();
            return;
        }
        const selectedSet = new Set(selected);
        const kept = this._poolOrder.filter((id) => selectedSet.has(id));
        const keptSet = new Set(kept);
        for (let id of selected) {
            if (!keptSet.has(id)) {
                kept.push(id);
            }
        }
        this._poolOrder = kept;
        this._syncPoolOrderField();
        this._syncPoolIndexBadges();
    }

    _swapPoolOrder() {
        this._poolOrder = [...this._poolOrder].reverse();
        this._syncPoolOrderField();
        this._syncPoolIndexBadges();
    }

    _syncCustomOrderField() {
        const toggleNode = this._customOrderToggleNode;
        const inputNode = this._poolOrderInputNode;
        if (!inputNode) {
            return;
        }
        const on = !!(toggleNode && toggleNode.checked);
        inputNode.hidden = !on;
        if (on) {
            this._syncPoolOrderField();
        } else {
            inputNode.setCustomValidity("");
            inputNode.classList.remove("invalid");
        }
    }

    _syncPoolOrderField() {
        const inputNode = this._poolOrderInputNode;
        if (inputNode && !inputNode.hidden) {
            inputNode.value = this._poolOrder.join(" ");
            inputNode.setCustomValidity("");
            inputNode.classList.remove("invalid");
        }
    }

    // The field is authoritative while it is valid; an incomplete list is left
    // alone so that typing is not fought, but it is flagged.
    _evtPoolOrderInput() {
        const inputNode = this._poolOrderInputNode;
        if (!inputNode) {
            return;
        }
        const ids = inputNode.value.split(/[\s,]+/).filter((id) => id);
        const selected = new Set(this._selectionControl.selectedIds);

        const unknown = ids.filter((id) => !selected.has(id));
        let problem = "";
        if (ids.length !== new Set(ids).size) {
            problem = "The same post is listed more than once.";
        } else if (unknown.length) {
            problem = `Not in the selection: ${unknown.join(" ")}`;
        } else if (ids.length !== selected.size) {
            problem = `Expected ${selected.size} post ID(s), got ${ids.length}.`;
        }

        inputNode.setCustomValidity(problem);
        inputNode.classList.toggle("invalid", !!problem);
        if (!problem) {
            this._poolOrder = ids;
            this._syncPoolIndexBadges();
        }
    }

    _syncPoolIndexBadges() {
        const show = this._poolPhase === "ordering";
        const indexById = {};
        this._poolOrder.forEach((id, index) => {
            indexById[id] = index + 1;
        });

        for (let listItemNode of this._listItemNodes) {
            const postId = listItemNode.getAttribute("data-post-id");
            let badgeNode = listItemNode.querySelector(".pool-index");
            const index = show ? indexById[postId] : undefined;
            if (index === undefined) {
                if (badgeNode) {
                    badgeNode.remove();
                }
                continue;
            }
            if (!badgeNode) {
                badgeNode = document.createElement("span");
                badgeNode.className = "pool-index";
                listItemNode.appendChild(badgeNode);
            }
            badgeNode.textContent = index;
        }
    }

    _evtPoolSubmit(e) {
        e.preventDefault();
        if (this._poolPhase !== "ordering") {
            this._enterPoolOrdering();
            return;
        }
        const orderInputNode = this._poolOrderInputNode;
        if (
            orderInputNode &&
            !orderInputNode.hidden &&
            orderInputNode.classList.contains("invalid")
        ) {
            this.dispatchEvent(
                new CustomEvent("error", {
                    detail: {
                        message:
                            "The custom order is not a valid arrangement of " +
                            "the selected posts.",
                    },
                })
            );
            return;
        }

        const panelNode = this._panelNode("pool");
        const nameNode = panelNode.querySelector("input[name=pool-name]");
        const categoryNode = panelNode.querySelector(
            "select[name=pool-category]"
        );
        const posts = this._poolOrder
            .map((postId) => this._postIdToPost[postId])
            .filter((post) => post);
        if (!posts.length) {
            return;
        }
        this.dispatchEvent(
            new CustomEvent("createPool", {
                detail: {
                    posts: posts,
                    names: [nameNode.value.trim()],
                    category: categoryNode.value,
                },
            })
        );
    }

    poolCreated() {
        const panelNode = this._panelNode("pool");
        if (panelNode) {
            const nameNode = panelNode.querySelector("input[name=pool-name]");
            if (nameNode) {
                nameNode.value = "";
            }
        }
        this._leavePoolOrdering();
        this.clearSelection();
    }

    // ---- misc ------------------------------------------------------------

    _evtPostChange(e) {
        const listItemNode = this._postIdToListItemNode[e.detail.post.id];
        if (!listItemNode) {
            return;
        }
        for (let node of listItemNode.querySelectorAll("[data-disabled]")) {
            node.removeAttribute("data-disabled");
        }
    }
}

module.exports = PostsPageView;
