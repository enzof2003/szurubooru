"use strict";

const events = require("../events.js");
const views = require("../util/views.js");

// A mousedown only becomes a marquee drag once the cursor has travelled this
// far, so that ordinary clicks still open the post they landed on.
const DRAG_THRESHOLD = 6;
// How close to the viewport edge the cursor must get before the page scrolls
// along with the drag, and how fast it then scrolls per animation frame.
const EDGE_SCROLL_MARGIN = 60;
const EDGE_SCROLL_SPEED = 22;

// Things that keep their own click handling: a mousedown on them must never
// start a selection drag, and a click on them must not clear the selection.
const PASSTHROUGH_SELECTOR = [
    ".edit-overlay",
    ".post-selection-bar",
    ".post-list-header",
    ".page-nav",
    ".messages",
    "input",
    "button",
    "select",
    "textarea",
    "label",
    "a.button",
].join(", ");

function _isPassthrough(node) {
    return !!(node && node.closest && node.closest(PASSTHROUGH_SELECTOR));
}

function _isAdditive(e) {
    return e.ctrlKey || e.metaKey || e.shiftKey;
}

// Lets the user pick a set of posts out of a post list: drag a marquee over the
// thumbnails, ctrl/cmd-click to toggle one, shift-click to extend a range.
//
// The drag surface is deliberately wider than the thumbnails themselves (it is
// the whole content area, margins included) so that a drag can be started from
// the empty space beside the grid as well as from a thumbnail.
class PostSelectionControl extends events.EventTarget {
    constructor(options) {
        super();
        this._surfaceNode = options.surfaceNode;
        this._itemsNode = options.itemsNode;
        this._enabled = !!options.enabled;

        this._selectedIds = new Set();
        this._anchorId = null;

        this._dragging = false;
        this._pending = false;
        this._suppressClick = false;
        this._marqueeNode = null;
        this._baseSelection = null;
        this._scrollFrame = null;

        this._boundMouseMove = (e) => this._evtMouseMove(e);
        this._boundMouseUp = (e) => this._evtMouseUp(e);
        this._boundMouseDown = (e) => this._evtMouseDown(e);
        this._boundClick = (e) => this._evtClick(e);
        this._boundDragStart = (e) => this._evtDragStart(e);

        this._surfaceNode.addEventListener("mousedown", this._boundMouseDown);
        // Capture, so that a modifier-click is claimed before the router's
        // document-level handler can treat it as plain navigation.
        this._surfaceNode.addEventListener("click", this._boundClick, true);
        // Thumbnails are links wrapping an <img>, so without this the browser
        // starts its own link/image drag and we never see mousemove at all -
        // which is why a drag could not be started on a post.
        this._surfaceNode.addEventListener("dragstart", this._boundDragStart);

        // The page view that owns this control is thrown away and rebuilt on
        // every page render, while the surface node outlives it, so the
        // listeners have to come off with the list they belong to.
        views.monitorNodeRemoval(this._itemsNode, () => this.destroy());
    }

    destroy() {
        this._endDrag();
        this._surfaceNode.removeEventListener(
            "mousedown",
            this._boundMouseDown
        );
        this._surfaceNode.removeEventListener("click", this._boundClick, true);
        this._surfaceNode.removeEventListener(
            "dragstart",
            this._boundDragStart
        );
        window.removeEventListener("mousemove", this._boundMouseMove);
        window.removeEventListener("mouseup", this._boundMouseUp);
    }

    get enabled() {
        return this._enabled;
    }

    setEnabled(state) {
        this._enabled = !!state;
        if (!this._enabled) {
            this._endDrag();
            this.clear();
        }
    }

    get selectedIds() {
        // DOM order, which is what the user sees and what pools expect
        return this._items
            .filter((item) => this._selectedIds.has(item.id))
            .map((item) => item.id);
    }

    get count() {
        return this.selectedIds.length;
    }

    get _items() {
        return [...this._itemsNode.querySelectorAll("li[data-post-id]")].map(
            (node) => ({ id: node.getAttribute("data-post-id"), node: node })
        );
    }

    has(postId) {
        return this._selectedIds.has(String(postId));
    }

    clear() {
        if (!this._selectedIds.size) {
            return;
        }
        this._applySelection(new Set());
    }

    selectAll() {
        this._applySelection(new Set(this._items.map((item) => item.id)));
    }

    _applySelection(ids) {
        this._selectedIds = ids;
        this._syncHighlights();
        this.dispatchEvent(
            new CustomEvent("change", {
                detail: { selectedIds: this.selectedIds },
            })
        );
    }

    _syncHighlights() {
        for (let item of this._items) {
            item.node.classList.toggle(
                "selected",
                this._selectedIds.has(item.id)
            );
        }
    }

    _evtDragStart(e) {
        if (this._enabled && !_isPassthrough(e.target)) {
            e.preventDefault();
        }
    }

    _evtMouseDown(e) {
        if (!this._enabled || e.button !== 0 || _isPassthrough(e.target)) {
            return;
        }
        this._pending = true;
        this._origin = {
            clientX: e.clientX,
            clientY: e.clientY,
            pageX: e.clientX + window.scrollX,
            pageY: e.clientY + window.scrollY,
        };
        this._pointer = { x: e.clientX, y: e.clientY };
        this._additive = _isAdditive(e);
        if (this._additive) {
            // keep the browser from starting a text selection or opening the
            // link in a new tab/window
            e.preventDefault();
        }
        window.addEventListener("mousemove", this._boundMouseMove);
        window.addEventListener("mouseup", this._boundMouseUp);
    }

    _evtMouseMove(e) {
        if (!this._pending && !this._dragging) {
            return;
        }
        this._pointer = { x: e.clientX, y: e.clientY };
        if (!this._dragging) {
            const travelled = Math.max(
                Math.abs(e.clientX - this._origin.clientX),
                Math.abs(e.clientY - this._origin.clientY)
            );
            if (travelled < DRAG_THRESHOLD) {
                return;
            }
            this._beginDrag();
        }
        e.preventDefault();
        this._updateDrag();
    }

    _beginDrag() {
        this._dragging = true;
        this._pending = false;

        const listRect = this._itemsNode.getBoundingClientRect();
        this._marqueeOrigin = {
            x: listRect.left + window.scrollX,
            y: listRect.top + window.scrollY,
        };
        // Page coordinates stay valid while the page scrolls under the drag, so
        // they are measured once instead of on every mouse move.
        this._itemRects = this._items.map((item) => {
            const rect = item.node.getBoundingClientRect();
            return {
                id: item.id,
                left: rect.left + window.scrollX,
                top: rect.top + window.scrollY,
                right: rect.right + window.scrollX,
                bottom: rect.bottom + window.scrollY,
            };
        });
        this._baseSelection = this._additive
            ? new Set(this._selectedIds)
            : new Set();

        this._marqueeNode = document.createElement("div");
        this._marqueeNode.className = "selection-marquee";
        this._itemsNode.appendChild(this._marqueeNode);
        this._itemsNode.classList.add("selecting");

        this._scrollFrame = window.requestAnimationFrame(() =>
            this._edgeScroll()
        );
    }

    _updateDrag() {
        const current = {
            x: this._pointer.x + window.scrollX,
            y: this._pointer.y + window.scrollY,
        };
        const box = {
            left: Math.min(this._origin.pageX, current.x),
            top: Math.min(this._origin.pageY, current.y),
            right: Math.max(this._origin.pageX, current.x),
            bottom: Math.max(this._origin.pageY, current.y),
        };

        this._marqueeNode.style.left = `${box.left - this._marqueeOrigin.x}px`;
        this._marqueeNode.style.top = `${box.top - this._marqueeOrigin.y}px`;
        this._marqueeNode.style.width = `${box.right - box.left}px`;
        this._marqueeNode.style.height = `${box.bottom - box.top}px`;

        const ids = new Set(this._baseSelection);
        for (let rect of this._itemRects) {
            if (
                rect.left < box.right &&
                rect.right > box.left &&
                rect.top < box.bottom &&
                rect.bottom > box.top
            ) {
                ids.add(rect.id);
            }
        }
        this._applySelection(ids);
    }

    _edgeScroll() {
        if (!this._dragging) {
            return;
        }
        let delta = 0;
        if (this._pointer.y < EDGE_SCROLL_MARGIN) {
            delta = -EDGE_SCROLL_SPEED;
        } else if (this._pointer.y > window.innerHeight - EDGE_SCROLL_MARGIN) {
            delta = EDGE_SCROLL_SPEED;
        }
        if (delta) {
            window.scrollBy(0, delta);
            this._updateDrag();
        }
        this._scrollFrame = window.requestAnimationFrame(() =>
            this._edgeScroll()
        );
    }

    _endDrag() {
        this._pending = false;
        if (!this._dragging) {
            return;
        }
        this._dragging = false;
        if (this._scrollFrame) {
            window.cancelAnimationFrame(this._scrollFrame);
            this._scrollFrame = null;
        }
        if (this._marqueeNode) {
            this._marqueeNode.remove();
            this._marqueeNode = null;
        }
        this._itemsNode.classList.remove("selecting");
    }

    _evtMouseUp(e) {
        window.removeEventListener("mousemove", this._boundMouseMove);
        window.removeEventListener("mouseup", this._boundMouseUp);
        const wasDragging = this._dragging;
        this._endDrag();
        if (wasDragging) {
            // the mouseup is followed by a click on whatever the drag ended over
            this._suppressClick = true;
        }
    }

    _evtClick(e) {
        if (this._suppressClick) {
            this._suppressClick = false;
            e.preventDefault();
            e.stopPropagation();
            return;
        }
        if (!this._enabled || _isPassthrough(e.target)) {
            return;
        }

        const listItemNode = e.target.closest
            ? e.target.closest("li[data-post-id]")
            : null;

        if (!listItemNode) {
            // a plain click on the empty space around the thumbnails
            if (!_isAdditive(e) && this._selectedIds.size) {
                this.clear();
            }
            return;
        }

        const postId = listItemNode.getAttribute("data-post-id");

        if (e.shiftKey && this._anchorId !== null) {
            e.preventDefault();
            e.stopPropagation();
            this._selectRange(this._anchorId, postId);
            return;
        }
        if (e.ctrlKey || e.metaKey || e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            const ids = new Set(this._selectedIds);
            if (ids.has(postId)) {
                ids.delete(postId);
            } else {
                ids.add(postId);
            }
            this._anchorId = postId;
            this._applySelection(ids);
            return;
        }
        // plain click: leave navigation alone
    }

    _selectRange(fromId, toId) {
        const ids = this._items.map((item) => item.id);
        const from = ids.indexOf(fromId);
        const to = ids.indexOf(toId);
        if (from === -1 || to === -1) {
            return;
        }
        const selection = new Set(this._selectedIds);
        for (let i = Math.min(from, to); i <= Math.max(from, to); i++) {
            selection.add(ids[i]);
        }
        this._applySelection(selection);
    }
}

module.exports = PostSelectionControl;
