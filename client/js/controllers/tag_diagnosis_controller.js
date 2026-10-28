"use strict";

const api = require("../api.js");
const tasks = require("../util/tasks.js");
const tagAudit = require("../models/tag_audit.js");
const topNavigation = require("../models/top_navigation.js");
const TagDiagnosisView = require("../views/tag_diagnosis_view.js");
const EmptyView = require("../views/empty_view.js");

class TagDiagnosisController {
    constructor(ctx) {
        if (!api.hasPrivilege("tags:list") || !api.hasPrivilege("posts:list")) {
            this._view = new EmptyView();
            this._view.showError(
                "You don't have privileges to diagnose tags."
            );
            return;
        }

        topNavigation.activate("tags");
        topNavigation.setTitle("Tag diagnosis");

        this._fileRules = tagAudit.loadFileRules();
        this._cancelled = { implications: false, fileRules: false };

        this._view = new TagDiagnosisView({
            canEditPostTags: api.hasPrivilege("posts:edit:tags"),
            canEditTags: api.hasPrivilege("tags:edit"),
            fileRulesText: tagAudit.formatFileRules(this._fileRules),
        });

        this._sections = {
            implications: {
                view: this._view.implications,
                key: "implications",
                build: () => tagAudit.implicationChecks(),
                label: "implication",
            },
            fileRules: {
                view: this._view.fileRules,
                key: "fileRules",
                build: () =>
                    Promise.resolve(
                        tagAudit.fileRuleChecks(this._fileRules)
                    ),
                label: "file rule",
            },
        };

        for (let section of Object.values(this._sections)) {
            section.view.addEventListener("scan", () => this._scan(section));
            section.view.addEventListener("stop", () => {
                this._cancelled[section.key] = true;
                section.view.setProgress("stopping…");
            });
            section.view.addEventListener("fix", (e) =>
                this._fix(section, [e.detail.check])
            );
            section.view.addEventListener("fixAll", () =>
                this._fix(section, section.view.problemChecks)
            );
        }

        this._view.addEventListener("saveRules", (e) =>
            this._evtSaveRules(e)
        );
        this._view.addEventListener("resetRules", () =>
            this._evtResetRules()
        );
    }

    _evtSaveRules(e) {
        this._view.clearMessages();
        const parsed = tagAudit.parseFileRules(e.detail.text);
        if (parsed.errors.length) {
            this._view.showError(parsed.errors.join("\n"));
            return;
        }
        this._fileRules = parsed.rules;
        tagAudit.saveFileRules(this._fileRules);
        this._view.setRulesText(tagAudit.formatFileRules(this._fileRules));
        this._sections.fileRules.view.setChecks([]);
        this._view.showSuccess(
            `Saved ${this._fileRules.length} rule(s). Scan again to apply them.`
        );
    }

    _evtResetRules() {
        this._view.clearMessages();
        this._fileRules = tagAudit.resetFileRules();
        this._view.setRulesText(tagAudit.formatFileRules(this._fileRules));
        this._sections.fileRules.view.setChecks([]);
        this._view.showInfo("Rules reset to the defaults.");
    }

    _scan(section) {
        this._view.clearMessages();
        this._cancelled[section.key] = false;
        section.view.setScanning(true);
        section.view.setProgress("collecting checks…");

        section
            .build()
            .then((checks) => {
                section.view.setChecks(checks);
                if (!checks.length) {
                    return { errors: [], cancelled: false };
                }
                return tagAudit.scan(checks, {
                    onResult: (check) => section.view.updateCheck(check),
                    onProgress: (done, total) =>
                        section.view.setProgress(
                            `scanning ${done} / ${total}…`
                        ),
                    isCancelled: () => this._cancelled[section.key],
                });
            })
            .then((outcome) => {
                section.view.setScanning(false);
                section.view.setProgress(
                    outcome.cancelled ? "stopped" : "done"
                );
                const summary = tasks.summarizeErrors(outcome.errors);
                if (summary) {
                    this._view.showError(
                        `Some ${section.label} checks could not be run. ${summary}`
                    );
                }
            })
            .catch((error) => {
                section.view.setScanning(false);
                section.view.setProgress("");
                this._view.showError(error.message);
            });
    }

    _fix(section, checks) {
        if (!checks.length) {
            return;
        }
        this._view.clearMessages();

        const posts = checks.reduce(
            (total, check) => total + (check.affected || 0),
            0
        );
        const what =
            checks.length === 1
                ? `"${checks[0].label}"`
                : `${checks.length} checks`;
        if (
            !window.confirm(
                `Add the missing tags for ${what}?\n\n` +
                    `This edits about ${posts} post(s) and cannot be undone ` +
                    `automatically.`
            )
        ) {
            return;
        }

        this._cancelled[section.key] = false;
        section.view.setScanning(true);

        let edited = 0;
        let failed = 0;
        let truncated = false;

        // Checks are applied one after another: they can overlap, and a post
        // fixed by one changes what the next one matches.
        const applyNext = (index) => {
            if (index >= checks.length || this._cancelled[section.key]) {
                return Promise.resolve();
            }
            const check = checks[index];
            section.view.setProgress(
                `fixing ${index + 1} / ${checks.length}: ${check.label}…`
            );
            return tagAudit
                .applyCheck(check, {
                    isCancelled: () => this._cancelled[section.key],
                })
                .then((outcome) => {
                    edited += outcome.attempted - outcome.failed;
                    failed += outcome.failed;
                    truncated = truncated || outcome.truncated;
                    return tagAudit
                        .countPosts(check.query)
                        .then((remaining) => {
                            check.affected = remaining;
                            section.view.updateCheck(check);
                        });
                })
                .then(() => applyNext(index + 1));
        };

        applyNext(0)
            .then(() => {
                section.view.setScanning(false);
                section.view.setProgress(
                    this._cancelled[section.key] ? "stopped" : "done"
                );
                if (edited) {
                    this._view.showSuccess(`Updated ${edited} post(s).`);
                }
                if (failed) {
                    this._view.showError(
                        `${failed} post(s) could not be updated.`
                    );
                }
                if (truncated) {
                    this._view.showInfo(
                        `Some checks matched more than ${tagAudit.APPLY_CAP} ` +
                            `posts; run the fix again to continue.`
                    );
                }
                if (!edited && !failed) {
                    this._view.showInfo("Nothing needed changing.");
                }
            })
            .catch((error) => {
                section.view.setScanning(false);
                section.view.setProgress("");
                this._view.showError(error.message);
            });
    }
}

module.exports = (router) => {
    router.enter(["tag-diagnosis"], (ctx, next) => {
        ctx.controller = new TagDiagnosisController(ctx);
    });
};
