"use strict";

const events = require("../events.js");
const views = require("../util/views.js");

const template = views.getTemplate("tag-diagnosis");

// One scannable group of checks: the controls above the table, the table
// itself, and the running summary. Rows are created once and then patched in
// place, because a scan can hold a couple of thousand of them.
class CheckSectionView extends events.EventTarget {
    constructor(hostNode, options) {
        super();
        this._hostNode = hostNode;
        this._canFix = !!options.canFix;
        this._checks = [];
        this._rowNodes = {};
        this._problemsOnly = true;

        this._bind(".scan", () =>
            this.dispatchEvent(new CustomEvent("scan", { detail: {} }))
        );
        this._bind(".stop", () =>
            this.dispatchEvent(new CustomEvent("stop", { detail: {} }))
        );
        this._bind(".fix-all", () =>
            this.dispatchEvent(new CustomEvent("fixAll", { detail: {} }))
        );

        const checkboxNode = this._problemsOnlyNode;
        if (checkboxNode) {
            checkboxNode.addEventListener("change", (e) => {
                this._problemsOnly = checkboxNode.checked;
                this._syncVisibility();
            });
        }
    }

    _bind(selector, handler) {
        for (let node of this._hostNode.querySelectorAll(selector)) {
            node.addEventListener("click", (e) => {
                e.preventDefault();
                handler(e, node);
            });
        }
    }

    get _controlsNode() {
        return this._hostNode.querySelector(".controls");
    }

    get _resultsNode() {
        return this._hostNode.querySelector(".results");
    }

    get _progressNode() {
        return this._hostNode.querySelector(".progress");
    }

    get _problemsOnlyNode() {
        return this._hostNode.querySelector(".problems-only input");
    }

    setScanning(state) {
        for (let node of this._hostNode.querySelectorAll(".scan")) {
            node.hidden = state;
        }
        for (let node of this._hostNode.querySelectorAll(".stop")) {
            node.hidden = !state;
        }
    }

    setProgress(text) {
        const node = this._progressNode;
        if (node) {
            node.textContent = text || "";
        }
    }

    setChecks(checks) {
        this._checks = checks;
        this._rowNodes = {};

        const resultsNode = this._resultsNode;
        views.emptyContent(resultsNode);

        if (!checks.length) {
            const noneNode = document.createElement("p");
            noneNode.className = "summary";
            noneNode.textContent = "No checks to run.";
            resultsNode.appendChild(noneNode);
            return;
        }

        const summaryNode = document.createElement("p");
        summaryNode.className = "summary";
        resultsNode.appendChild(summaryNode);

        const tableNode = document.createElement("table");
        tableNode.innerHTML =
            "<thead><tr>" +
            "<th class='rule'>Check</th>" +
            "<th class='affected'>Posts</th>" +
            "<th class='links'></th>" +
            "</tr></thead><tbody></tbody>";
        const bodyNode = tableNode.querySelector("tbody");
        for (let check of checks) {
            const rowNode = this._makeRow(check);
            this._rowNodes[check.id] = rowNode;
            bodyNode.appendChild(rowNode);
        }
        resultsNode.appendChild(tableNode);

        this._summaryNode = summaryNode;
        this._syncSummary();
        this._syncVisibility();
    }

    _makeRow(check) {
        const rowNode = document.createElement("tr");

        const ruleNode = document.createElement("td");
        ruleNode.className = "rule";
        const labelNode = document.createElement("code");
        labelNode.textContent = check.label;
        ruleNode.appendChild(labelNode);
        const queryNode = document.createElement("span");
        queryNode.className = "query";
        queryNode.textContent = check.query;
        ruleNode.appendChild(queryNode);

        const affectedNode = document.createElement("td");
        affectedNode.className = "affected";

        const linksNode = document.createElement("td");
        linksNode.className = "links";

        rowNode.appendChild(ruleNode);
        rowNode.appendChild(affectedNode);
        rowNode.appendChild(linksNode);
        this._paintRow(rowNode, check);
        return rowNode;
    }

    _paintRow(rowNode, check) {
        const affectedNode = rowNode.querySelector("td.affected");
        const linksNode = rowNode.querySelector("td.links");

        rowNode.classList.toggle("clean", check.affected === 0);
        rowNode.classList.toggle("problem", check.affected > 0);
        rowNode.classList.toggle("failed", !!check.error);

        if (check.error) {
            affectedNode.textContent = "error";
            affectedNode.title = check.error;
        } else if (check.scanning) {
            affectedNode.textContent = "…";
        } else if (check.affected === null) {
            affectedNode.textContent = "—";
        } else {
            affectedNode.textContent = check.affected;
        }

        views.emptyContent(linksNode);
        if (check.affected > 0) {
            const viewNode = document.createElement("a");
            viewNode.href = check.postsLink;
            viewNode.textContent = "view posts";
            linksNode.appendChild(viewNode);

            if (this._canFix) {
                const fixNode = document.createElement("a");
                fixNode.href = "";
                fixNode.className = "fix";
                fixNode.textContent = "fix";
                fixNode.addEventListener("click", (e) => {
                    e.preventDefault();
                    this.dispatchEvent(
                        new CustomEvent("fix", { detail: { check: check } })
                    );
                });
                linksNode.appendChild(fixNode);
            }
        }
    }

    updateCheck(check) {
        const rowNode = this._rowNodes[check.id];
        if (rowNode) {
            this._paintRow(rowNode, check);
            this._syncRowVisibility(rowNode, check);
        }
        this._syncSummary();
    }

    refresh() {
        for (let check of this._checks) {
            const rowNode = this._rowNodes[check.id];
            if (rowNode) {
                this._paintRow(rowNode, check);
            }
        }
        this._syncSummary();
        this._syncVisibility();
    }

    _syncRowVisibility(rowNode, check) {
        const interesting = check.affected > 0 || !!check.error;
        rowNode.hidden = this._problemsOnly && !interesting;
    }

    _syncVisibility() {
        for (let check of this._checks) {
            const rowNode = this._rowNodes[check.id];
            if (rowNode) {
                this._syncRowVisibility(rowNode, check);
            }
        }
    }

    _syncSummary() {
        if (!this._summaryNode) {
            return;
        }
        let scanned = 0;
        let problems = 0;
        let posts = 0;
        let failed = 0;
        for (let check of this._checks) {
            if (check.error) {
                failed += 1;
            } else if (check.affected !== null) {
                scanned += 1;
                if (check.affected > 0) {
                    problems += 1;
                    posts += check.affected;
                }
            }
        }
        let text = `${this._checks.length} check(s)`;
        if (scanned || failed) {
            text += ` · ${scanned} scanned`;
            text += ` · ${problems} with problems`;
            if (problems) {
                text += ` (${posts} post(s) to fix)`;
            }
            if (failed) {
                text += ` · ${failed} failed`;
            }
        } else {
            text += " · not scanned yet";
        }
        this._summaryNode.textContent = text;

        for (let node of this._hostNode.querySelectorAll(".fix-all")) {
            node.hidden = problems === 0;
        }
    }

    get problemChecks() {
        return this._checks.filter((check) => check.affected > 0);
    }

    get checks() {
        return this._checks;
    }
}

class TagDiagnosisView extends events.EventTarget {
    constructor(ctx) {
        super();
        this._ctx = ctx;
        this._hostNode = document.getElementById("content-holder");
        views.replaceContent(this._hostNode, template(ctx));
        views.syncScrollPosition();

        this.implications = new CheckSectionView(
            this._hostNode.querySelector("section.implications"),
            { canFix: ctx.canEditPostTags }
        );
    }

    clearMessages() {
        views.clearMessages(this._hostNode);
    }

    showSuccess(message) {
        views.showSuccess(this._hostNode, message);
    }

    showError(message) {
        views.showError(this._hostNode, message);
    }

    showInfo(message) {
        views.showInfo(this._hostNode, message);
    }
}

module.exports = TagDiagnosisView;
