"use strict";

const router = require("../router.js");
const api = require("../api.js");
const settings = require("../models/settings.js");
const tasks = require("../util/tasks.js");
const uri = require("../util/uri.js");
const PostList = require("../models/post_list.js");
const topNavigation = require("../models/top_navigation.js");
const PageController = require("../controllers/page_controller.js");
const PostsHeaderView = require("../views/posts_header_view.js");
const PostsPageView = require("../views/posts_page_view.js");
const EmptyView = require("../views/empty_view.js");

const fields = [
    "id",
    "thumbnailUrl",
    "type",
    "safety",
    "score",
    "favoriteCount",
    "commentCount",
    "tags",
    "version",
];

class PostListController {
    constructor(ctx) {
        this._pageController = new PageController();

        if (!api.hasPrivilege("posts:list")) {
            this._view = new EmptyView();
            this._view.showError("You don't have privileges to view posts.");
            return;
        }

        this._ctx = ctx;

        topNavigation.activate("posts");
        topNavigation.setTitle("Listing posts");

        this._headerView = new PostsHeaderView({
            hostNode: this._pageController.view.pageHeaderHolderNode,
            parameters: ctx.parameters,
            enableSafety: api.safetyEnabled(),
            canBulkEditTags: api.hasPrivilege("posts:bulk-edit:tags"),
            canBulkEditSafety: api.hasPrivilege("posts:bulk-edit:safety"),
            canBulkDelete: api.hasPrivilege("posts:bulk-edit:delete"),
            bulkEdit: {
                tags: this._bulkEditTags,
            },
        });
        this._headerView.addEventListener("navigate", (e) =>
            this._evtNavigate(e)
        );

        if (this._headerView._bulkDeleteEditor) {
            this._headerView._bulkDeleteEditor.addEventListener(
                "deleteSelectedPosts",
                (e) => {
                    this._evtDeleteSelectedPosts(e);
                }
            );
        }

        this._postsMarkedForDeletion = [];
        this._postsPageView = null;
        this._syncPageController();
    }

    showSuccess(message) {
        this._pageController.showSuccess(message);
    }

    get _bulkEditTags() {
        return (this._ctx.parameters.tag || "").split(/\s+/).filter((s) => s);
    }

    _evtNavigate(e) {
        router.showNoDispatch(
            uri.formatClientLink("posts", e.detail.parameters)
        );
        Object.assign(this._ctx.parameters, e.detail.parameters);
        this._syncPageController();
    }

    _evtTag(e) {
        Promise.all(
            this._bulkEditTags.map((tag) => e.detail.post.tags.addByName(tag))
        )
            .then(e.detail.post.save())
            .catch((error) => window.alert(error.message));
    }

    _evtUntag(e) {
        for (let tag of this._bulkEditTags) {
            e.detail.post.tags.removeByName(tag);
        }
        e.detail.post.save().catch((error) => window.alert(error.message));
    }

    _evtChangeSafety(e) {
        e.detail.post.safety = e.detail.safety;
        e.detail.post.save().catch((error) => window.alert(error.message));
    }

    _evtMarkForDeletion(e) {
        this._setMarkedForDeletion([e.detail.post], e.detail.delete);
    }

    // The array is handed to the page view through its render context, so it
    // has to be updated in place - replacing it would leave the view looking at
    // a stale copy.
    _setMarkedForDeletion(posts, marked) {
        for (let post of posts) {
            const index = this._postsMarkedForDeletion.findIndex(
                (x) => x.id == post.id
            );
            if (marked && index === -1) {
                this._postsMarkedForDeletion.push(post);
            } else if (!marked && index !== -1) {
                this._postsMarkedForDeletion.splice(index, 1);
            }
        }
    }

    // ---- actions on a selection of posts ---------------------------------

    _runOnSelection(posts, worker, describeSuccess) {
        if (!posts.length) {
            return Promise.resolve();
        }
        const view = this._pageController.view;
        view.clearMessages();
        return tasks
            .runWithConcurrency(posts, worker, { concurrency: 4 })
            .then((outcome) => {
                const succeeded = posts.length - outcome.errors.length;
                if (succeeded) {
                    view.showSuccess(describeSuccess(succeeded));
                }
                const summary = tasks.summarizeErrors(outcome.errors);
                if (summary) {
                    view.showError(summary);
                }
            });
    }

    _evtBulkTag(e) {
        const tags = this._bulkEditTags;
        if (!tags.length) {
            return;
        }
        this._runOnSelection(
            e.detail.posts,
            (post) =>
                Promise.all(tags.map((tag) => post.tags.addByName(tag))).then(
                    () => post.save()
                ),
            (count) => `Tagged ${count} post(s) with ${tags.join(" ")}.`
        );
    }

    _evtBulkUntag(e) {
        const tags = this._bulkEditTags;
        if (!tags.length) {
            return;
        }
        this._runOnSelection(
            e.detail.posts,
            (post) => {
                for (let tag of tags) {
                    post.tags.removeByName(tag);
                }
                return post.save();
            },
            (count) => `Removed ${tags.join(" ")} from ${count} post(s).`
        );
    }

    _evtBulkChangeSafety(e) {
        const safety = e.detail.safety;
        const posts = e.detail.posts.filter((post) => post.safety !== safety);
        if (!posts.length) {
            return;
        }
        this._runOnSelection(
            posts,
            (post) => {
                post.safety = safety;
                return post.save();
            },
            (count) => `Set ${count} post(s) to ${safety}.`
        );
    }

    _evtBulkMarkForDeletion(e) {
        this._setMarkedForDeletion(e.detail.posts, e.detail.delete);
        if (this._postsPageView) {
            this._postsPageView.refreshHighlights();
        }
        const view = this._pageController.view;
        view.clearMessages();
        view.showInfo(
            e.detail.delete
                ? `${this._postsMarkedForDeletion.length} post(s) marked for deletion. Use "Delete selected posts" in the header to confirm.`
                : `${this._postsMarkedForDeletion.length} post(s) still marked for deletion.`
        );
    }

    _evtCreatePool(e) {
        const posts = e.detail.posts;
        const view = this._pageController.view;
        view.clearMessages();
        api.post(uri.formatApiLink("pool"), {
            names: e.detail.names,
            category: e.detail.category,
            description: "",
            posts: posts.map((post) => post.id),
        }).then(
            (response) => {
                const poolLink = uri.formatClientLink("pool", response.id);
                view.showSuccess(
                    `Created pool [${response.names[0]}](${poolLink}) ` +
                        `with ${posts.length} post(s).`
                );
                if (this._postsPageView) {
                    this._postsPageView.clearSelection();
                }
            },
            (error) => {
                view.showError(error.message);
            }
        );
    }

    _evtViewError(e) {
        this._pageController.view.showError(e.detail.message);
    }

    _evtDeleteSelectedPosts(e) {
        if (this._postsMarkedForDeletion.length == 0) return;

        if (
            confirm(
                `Are you sure you want to delete ${this._postsMarkedForDeletion.length} posts?`
            )
        ) {
            Promise.all(
                this._postsMarkedForDeletion.map((post) => post.delete())
            )
                .catch((error) => window.alert(error.message))
                .then(() => {
                    this._postsMarkedForDeletion = [];
                    this._headerView._navigate();
                });
        }
    }

    _syncPageController() {
        this._pageController.run({
            parameters: this._ctx.parameters,
            defaultLimit: parseInt(settings.get().postsPerPage),
            getClientUrlForPage: (offset, limit) => {
                const parameters = Object.assign({}, this._ctx.parameters, {
                    offset: offset,
                    limit: limit,
                });
                return uri.formatClientLink("posts", parameters);
            },
            requestPage: (offset, limit) => {
                return PostList.search(
                    this._ctx.parameters.query,
                    offset,
                    limit,
                    fields
                );
            },
            pageRenderer: (pageCtx) => {
                const canBulkEditTags = api.hasPrivilege(
                    "posts:bulk-edit:tags"
                );
                const canBulkEditSafety = api.hasPrivilege(
                    "posts:bulk-edit:safety"
                );
                const canBulkDelete = api.hasPrivilege(
                    "posts:bulk-edit:delete"
                );
                const canCreatePool = api.hasPrivilege("pools:create");
                Object.assign(pageCtx, {
                    canViewPosts: api.hasPrivilege("posts:view"),
                    enableSafety: api.safetyEnabled(),
                    canBulkEditTags: canBulkEditTags,
                    canBulkEditSafety: canBulkEditSafety,
                    canBulkDelete: canBulkDelete,
                    canCreatePool: canCreatePool,
                    canSelect:
                        canBulkEditTags ||
                        canBulkEditSafety ||
                        canBulkDelete ||
                        canCreatePool,
                    bulkEdit: {
                        tags: this._bulkEditTags,
                        markedForDeletion: this._postsMarkedForDeletion,
                    },
                    postFlow: settings.get().postFlow,
                });
                const view = new PostsPageView(pageCtx);
                this._postsPageView = view;
                view.addEventListener("tag", (e) => this._evtTag(e));
                view.addEventListener("untag", (e) => this._evtUntag(e));
                view.addEventListener("changeSafety", (e) =>
                    this._evtChangeSafety(e)
                );
                view.addEventListener("markForDeletion", (e) =>
                    this._evtMarkForDeletion(e)
                );
                view.addEventListener("bulkTag", (e) => this._evtBulkTag(e));
                view.addEventListener("bulkUntag", (e) =>
                    this._evtBulkUntag(e)
                );
                view.addEventListener("bulkChangeSafety", (e) =>
                    this._evtBulkChangeSafety(e)
                );
                view.addEventListener("bulkMarkForDeletion", (e) =>
                    this._evtBulkMarkForDeletion(e)
                );
                view.addEventListener("createPool", (e) =>
                    this._evtCreatePool(e)
                );
                view.addEventListener("error", (e) => this._evtViewError(e));
                return view;
            },
        });
    }
}

module.exports = (router) => {
    router.enter(["posts"], (ctx, next) => {
        ctx.controller = new PostListController(ctx);
    });
};
