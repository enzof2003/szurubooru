"use strict";

const events = require("../events.js");
const keyboard = require("../util/keyboard.js");
const views = require("../util/views.js");
const PostSelectionControl = require("../controls/post_selection_control.js");

const template = views.getTemplate("posts-page");

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

            const tagFlipperNode = this._getTagFlipperNode(listItemNode);
            if (tagFlipperNode) {
                tagFlipperNode.addEventListener("click", (e) =>
                    this._evtBulkEditTagsClick(e, post)
                );
            }

            const safetyFlipperNode = this._getSafetyFlipperNode(listItemNode);
            if (safetyFlipperNode) {
                for (let linkNode of safetyFlipperNode.querySelectorAll("a")) {
                    linkNode.addEventListener("click", (e) =>
                        this._evtBulkEditSafetyClick(e, post)
                    );
                }
            }

            const deleteFlipperNode = this._getDeleteFlipperNode(listItemNode);
            if (deleteFlipperNode) {
                deleteFlipperNode.addEventListener("click", (e) =>
                    this._evtBulkToggleDeleteClick(e, post)
                );
            }
        }

        this._syncBulkEditorsHighlights();

        this._poolOrder = [];
        this._selectionControl = null;
        if (ctx.canSelect && this._postListNode) {
            this._initSelection();
        }
    }

    get _listItemNodes() {
        return this._hostNode.querySelectorAll("li");
    }

    get _postListNode() {
        return this._hostNode.querySelector(".post-list");
    }

    get _selectionBarNode() {
        return this._hostNode.querySelector(".post-selection-bar");
    }

    get _createPoolFormNode() {
        return this._hostNode.querySelector(".create-pool-from-selection");
    }

    _getTagFlipperNode(listItemNode) {
        return listItemNode.querySelector(".tag-flipper");
    }

    _getSafetyFlipperNode(listItemNode) {
        return listItemNode.querySelector(".safety-flipper");
    }

    _getDeleteFlipperNode(listItemNode) {
        return listItemNode.querySelector(".delete-flipper");
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
        this._selectionControl.addEventListener("change", (e) =>
            this._syncSelectionBar()
        );

        const barNode = this._selectionBarNode;
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

        const tagLabel = this._describeBulkEditTags();
        for (let node of barNode.querySelectorAll(".tag-selected")) {
            node.textContent = `Add ${tagLabel}`;
        }
        for (let node of barNode.querySelectorAll(".untag-selected")) {
            node.textContent = `Remove ${tagLabel}`;
        }

        bind(".tag-selected", () => this._dispatchForSelection("bulkTag", {}));
        bind(".untag-selected", () =>
            this._dispatchForSelection("bulkUntag", {})
        );
        bind(".safety-selected a", (e, node) =>
            this._dispatchForSelection("bulkChangeSafety", {
                safety: node.getAttribute("data-safety"),
            })
        );
        bind(".mark-selected-deleted", () =>
            this._dispatchForSelection("bulkMarkForDeletion", {
                delete: true,
            })
        );
        bind(".unmark-selected-deleted", () =>
            this._dispatchForSelection("bulkMarkForDeletion", {
                delete: false,
            })
        );
        bind(".select-all", () => this._selectionControl.selectAll());
        bind(".clear-selection", () => this._selectionControl.clear());
        bind(".open-create-pool", () => this._showCreatePoolForm());
        bind(".cancel-create-pool", () => this._hideCreatePoolForm());
        bind(".swap-pool-order", () => this._swapPoolOrder());

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

        const formNode = this._createPoolFormNode;
        if (formNode) {
            formNode.addEventListener("submit", (e) =>
                this._evtCreatePoolSubmit(e)
            );
        }

        keyboard.bind("esc", () => {
            if (this._selectionControl.count) {
                this._selectionControl.clear();
            }
        });

        this._syncSelectionBar();
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

    // Lets the controller redraw the flippers after it changed state the view
    // only sees through its render context (e.g. the deletion list).
    refreshHighlights() {
        this._syncBulkEditorsHighlights();
    }

    _describeBulkEditTags() {
        const tags = (this._ctx.bulkEdit && this._ctx.bulkEdit.tags) || [];
        if (!tags.length) {
            return "tags";
        }
        if (tags.length <= 2) {
            return tags.join(" ");
        }
        return `${tags.length} tags`;
    }

    _dispatchForSelection(eventName, detail) {
        const posts = this.selectedPosts;
        if (!posts.length) {
            return;
        }
        this.dispatchEvent(
            new CustomEvent(
                eventName,
                { detail: Object.assign({ posts: posts }, detail) }
            )
        );
    }

    _syncSelectionBar() {
        const barNode = this._selectionBarNode;
        if (!barNode) {
            return;
        }
        const count = this._selectionControl.count;
        const countNode = barNode.querySelector(".count");
        if (countNode) {
            countNode.textContent = count;
        }
        barNode.hidden = count === 0;
        if (!count) {
            this._hideCreatePoolForm();
        } else if (this._poolCreationOpen) {
            this._resetPoolOrder();
        }
    }

    get _customOrderToggleNode() {
        return this._hostNode.querySelector("input[name=custom-order]");
    }

    get _poolOrderInputNode() {
        return this._hostNode.querySelector("input[name=pool-order]");
    }

    get _poolCreationOpen() {
        const formNode = this._createPoolFormNode;
        return !!formNode && !formNode.hidden;
    }

    setEditMode(state) {
        this._ctx.editMode = state;
        if (this._selectionControl) {
            this._selectionControl.setEnabled(state);
        }
        if (!state) {
            this._hideCreatePoolForm();
            this._syncSelectionBar();
        }
    }

    _showCreatePoolForm() {
        const formNode = this._createPoolFormNode;
        if (!formNode) {
            return;
        }
        formNode.hidden = false;
        this._resetPoolOrder();
        const nameNode = formNode.querySelector("input[name=pool-name]");
        if (nameNode) {
            nameNode.focus();
        }
    }

    _hideCreatePoolForm() {
        const formNode = this._createPoolFormNode;
        if (!formNode) {
            return;
        }
        formNode.hidden = true;
        const nameNode = formNode.querySelector("input[name=pool-name]");
        if (nameNode) {
            nameNode.value = "";
        }
        const customOrderNode = this._customOrderToggleNode;
        if (customOrderNode) {
            customOrderNode.checked = false;
        }
        this._poolOrder = [];
        this._syncCustomOrderField();
        this._syncPoolIndexBadges();
    }

    // ---- pool ordering --------------------------------------------------

    // Keeps the order the user has arranged so far, dropping posts that left
    // the selection and appending newly selected ones in page order.
    _resetPoolOrder() {
        const selected = this._selectionControl
            ? this._selectionControl.selectedIds
            : [];
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
        const selected = new Set(
            this._selectionControl ? this._selectionControl.selectedIds : []
        );

        const unknown = ids.filter((id) => !selected.has(id));
        const duplicated = ids.length !== new Set(ids).size;
        let problem = "";
        if (duplicated) {
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
        const show = this._poolCreationOpen;
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

    _evtCreatePoolSubmit(e) {
        e.preventDefault();
        const formNode = this._createPoolFormNode;
        const nameNode = formNode.querySelector("input[name=pool-name]");
        const categoryNode = formNode.querySelector(
            "select[name=pool-category]"
        );
        const name = (nameNode ? nameNode.value : "").trim();
        if (!name) {
            if (nameNode) {
                nameNode.focus();
            }
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

        const category = categoryNode ? categoryNode.value : "";
        if (!category) {
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

        const posts = this._postsInPoolOrder();
        if (!posts.length) {
            return;
        }
        this.dispatchEvent(
            new CustomEvent("createPool", {
                detail: {
                    posts: posts,
                    names: [name],
                    category: category,
                },
            })
        );
    }

    _postsInPoolOrder() {
        return this._poolOrder
            .map((postId) => this._postIdToPost[postId])
            .filter((post) => post);
    }

    // ---- per-post bulk editors -------------------------------------------

    _evtPostChange(e) {
        const listItemNode = this._postIdToListItemNode[e.detail.post.id];
        for (let node of listItemNode.querySelectorAll("[data-disabled]")) {
            node.removeAttribute("data-disabled");
        }
        this._syncBulkEditorsHighlights();
    }

    _evtBulkEditTagsClick(e, post) {
        e.preventDefault();
        const linkNode = e.target;
        if (linkNode.getAttribute("data-disabled")) {
            return;
        }
        linkNode.setAttribute("data-disabled", true);
        this.dispatchEvent(
            new CustomEvent(
                linkNode.classList.contains("tagged") ? "untag" : "tag",
                {
                    detail: { post: post },
                }
            )
        );
    }

    _evtBulkEditSafetyClick(e, post) {
        e.preventDefault();
        const linkNode = e.target;
        if (linkNode.getAttribute("data-disabled")) {
            return;
        }
        const newSafety = linkNode.getAttribute("data-safety");
        if (post.safety === newSafety) {
            return;
        }
        linkNode.setAttribute("data-disabled", true);
        this.dispatchEvent(
            new CustomEvent("changeSafety", {
                detail: { post: post, safety: newSafety },
            })
        );
    }

    _evtBulkToggleDeleteClick(e, post) {
        e.preventDefault();
        const linkNode = e.target;
        linkNode.classList.toggle("delete");
        this.dispatchEvent(
            new CustomEvent("markForDeletion", {
                detail: {
                    post,
                    delete: linkNode.classList.contains("delete"),
                },
            })
        );
    }

    _syncBulkEditorsHighlights() {
        for (let listItemNode of this._listItemNodes) {
            const postId = listItemNode.getAttribute("data-post-id");
            const post = this._postIdToPost[postId];
            if (!post) {
                continue;
            }

            const tagFlipperNode = this._getTagFlipperNode(listItemNode);
            if (tagFlipperNode) {
                let tagged = true;
                for (let tag of this._ctx.bulkEdit.tags) {
                    tagged &= post.tags.isTaggedWith(tag);
                }
                tagFlipperNode.classList.toggle("tagged", tagged);
            }

            const safetyFlipperNode = this._getSafetyFlipperNode(listItemNode);
            if (safetyFlipperNode) {
                for (let linkNode of safetyFlipperNode.querySelectorAll("a")) {
                    const safety = linkNode.getAttribute("data-safety");
                    linkNode.classList.toggle(
                        "active",
                        post.safety === safety
                    );
                }
            }

            const deleteFlipperNode = this._getDeleteFlipperNode(listItemNode);
            if (deleteFlipperNode) {
                deleteFlipperNode.classList.toggle(
                    "delete",
                    this._ctx.bulkEdit.markedForDeletion.some(
                        (x) => x.id == postId
                    )
                );
            }
        }
    }
}

module.exports = PostsPageView;
