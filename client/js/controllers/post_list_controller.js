"use strict";

const router = require("../router.js");
const api = require("../api.js");
const settings = require("../models/settings.js");
const tasks = require("../util/tasks.js");
const uri = require("../util/uri.js");
const PostList = require("../models/post_list.js");
const PoolCategoryList = require("../models/pool_category_list.js");
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

const MODES = [
    { key: "tag", label: "Mass tag", privilege: "posts:bulk-edit:tags" },
    {
        key: "safety",
        label: "Mass edit safety",
        privilege: "posts:bulk-edit:safety",
        needsSafety: true,
    },
    {
        key: "delete",
        label: "Mass delete",
        privilege: "posts:bulk-edit:delete",
    },
    { key: "pool", label: "Pool creation", privilege: "pools:create" },
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

        this._postsMarkedForDeletion = [];
        this._postsPageView = null;
        this._poolCategories = null;
        this._poolCategoryError = null;

        this._headerView = new PostsHeaderView({
            hostNode: this._pageController.view.pageHeaderHolderNode,
            parameters: ctx.parameters,
            enableSafety: api.safetyEnabled(),
            canUseEditMode: this._availableModes.length > 0,
            editMode: this._editMode,
            availableModes: this._availableModes,
            mode: this._mode,
        });
        this._headerView.addEventListener("navigate", (e) =>
            this._evtNavigate(e)
        );
        this._headerView.addEventListener("changeEditMode", (e) =>
            this._evtChangeEditMode(e)
        );
        this._headerView.addEventListener("changeMode", (e) =>
            this._evtChangeMode(e)
        );

        // Fetched up front rather than when the pool panel opens: the <select>
        // is rendered with the template, so there is no window in which it can
        // be submitted while still empty. In practice this is served from the
        // api cache, since the pool category colour map is already loaded at
        // startup.
        const ready = api.hasPrivilege("pools:create")
            ? PoolCategoryList.get().then(
                  (response) => {
                      this._poolCategories = response.results;
                  },
                  (error) => {
                      this._poolCategories = null;
                      this._poolCategoryError = error.message;
                  }
              )
            : Promise.resolve();

        ready.then(() => this._syncPageController());
    }

    showSuccess(message) {
        this._pageController.showSuccess(message);
    }

    // ---- state -----------------------------------------------------------

    get _editMode() {
        return !!settings.get().editMode;
    }

    get _availableModes() {
        return MODES.filter(
            (mode) =>
                api.hasPrivilege(mode.privilege) &&
                (!mode.needsSafety || api.safetyEnabled())
        ).map((mode) => ({ key: mode.key, label: mode.label }));
    }

    // The batch mode lives in the url so it survives paging. The older
    // safety=1 / delete=1 / tag=... parameters are still understood, so links
    // made before this existed keep working.
    get _mode() {
        const parameters = this._ctx.parameters;
        const available = this._availableModes.map((mode) => mode.key);
        let mode = parameters.mode || null;
        if (!mode) {
            if (parameters.safety) {
                mode = "safety";
            } else if (parameters.delete) {
                mode = "delete";
            } else if (parameters.tag) {
                mode = "tag";
            }
        }
        return mode && available.includes(mode) ? mode : null;
    }

    get _bulkEditTags() {
        return (this._ctx.parameters.tag || "").split(/\s+/).filter((s) => s);
    }

    // ---- navigation ------------------------------------------------------

    _evtNavigate(e) {
        Object.assign(this._ctx.parameters, e.detail.parameters);
        router.showNoDispatch(
            uri.formatClientLink("posts", this._ctx.parameters)
        );
        this._syncPageController();
    }

    _evtChangeEditMode(e) {
        if (this._postsPageView) {
            this._postsPageView.setEditMode(e.detail.editMode);
        }
    }

    // Switching mode changes which overlay the thumbnails carry, so the page
    // is re-rendered - but from the posts already loaded, not refetched.
    _evtChangeMode(e) {
        this._ctx.parameters.mode = e.detail.mode;
        this._ctx.parameters.safety = null;
        this._ctx.parameters.delete = null;
        router.showNoDispatch(
            uri.formatClientLink("posts", this._ctx.parameters)
        );
        this._syncPageController();
    }

    _evtChangeBulkTags(e) {
        this._ctx.parameters.tag = e.detail.tags.join(" ");
        router.showNoDispatch(
            uri.formatClientLink("posts", this._ctx.parameters)
        );
    }

    // ---- batch actions ---------------------------------------------------

    // `warmUpFirst` saves the first post on its own before the rest run
    // concurrently. A tag that does not exist yet is created by whichever
    // request gets there first, and tag_name.name is unique with no locking
    // around the server's read-then-insert - so firing four requests at a
    // missing tag makes them all try to create it and the database rejects
    // all but one. Doing one first means the tag exists for the others, and
    // it also lets them pick up its implications.
    _runOnPosts(posts, worker, describeSuccess, options) {
        options = options || {};
        if (!posts.length) {
            return Promise.resolve();
        }
        const view = this._pageController.view;
        view.clearMessages();

        const run = (items, concurrency) =>
            tasks.runWithConcurrency(items, worker, {
                concurrency: concurrency,
            });

        const outcome =
            options.warmUpFirst && posts.length > 1
                ? run(posts.slice(0, 1), 1).then((first) =>
                      run(posts.slice(1), 4).then((rest) => ({
                          errors: first.errors.concat(rest.errors),
                      }))
                  )
                : run(posts, 4);

        return outcome.then((result) => {
            const succeeded = posts.length - result.errors.length;
            if (succeeded) {
                view.showSuccess(describeSuccess(succeeded));
            }
            const summary = tasks.summarizeErrors(result.errors);
            if (summary) {
                view.showError(summary);
            }
        });
    }

    // One button for both directions: if every selected post already carries
    // every tag, the obvious intent is to take them off again.
    _evtApplyTags(e) {
        const tags = e.detail.tags;
        const posts = e.detail.posts;
        if (!tags.length || !posts.length) {
            return;
        }
        const allTagged = posts.every((post) =>
            tags.every((tag) => post.tags.isTaggedWith(tag))
        );

        if (allTagged) {
            this._runOnPosts(
                posts,
                (post) => {
                    for (let tag of tags) {
                        post.tags.removeByName(tag);
                    }
                    return post.save();
                },
                (count) => `Removed ${tags.join(" ")} from ${count} post(s).`
            );
        } else {
            this._runOnPosts(
                posts,
                (post) =>
                    Promise.all(
                        // addByName() puts the tag on the post straight away
                        // and only then looks it up to pull in implications.
                        // A tag that does not exist yet makes that lookup
                        // fail, so the failure is swallowed deliberately -
                        // otherwise brand new tags could never be applied,
                        // and creating tags by mass tagging is the point.
                        tags.map((tag) =>
                            post.tags.addByName(tag).catch(() => {})
                        )
                    ).then(() => post.save()),
                (count) => `Tagged ${count} post(s) with ${tags.join(" ")}.`,
                { warmUpFirst: true }
            );
        }
    }

    _evtApplySafety(e) {
        const safety = e.detail.safety;
        const posts = e.detail.posts.filter((post) => post.safety !== safety);
        if (!posts.length) {
            this._pageController.view.showInfo(
                `Every selected post is already ${safety}.`
            );
            return;
        }
        this._runOnPosts(
            posts,
            (post) => {
                post.safety = safety;
                return post.save();
            },
            (count) => `Set ${count} post(s) to ${safety}.`
        );
    }

    _evtDeleteSelected(e) {
        const posts = e.detail.posts;
        this._runOnPosts(
            posts,
            (post) => post.delete(),
            (count) => `Deleted ${count} post(s).`
        ).then(() => {
            this._postsMarkedForDeletion.length = 0;
            // the deleted thumbnails are still on screen, and the page is now
            // short, so reload it rather than leaving gaps behind
            this._syncPageController();
        });
    }

    _evtMarkForDeletion(e) {
        this._setMarkedForDeletion([e.detail.post], e.detail.delete);
        if (this._postsPageView) {
            this._postsPageView.refreshHighlights();
        }
    }

    _evtClearDeleteMarks() {
        this._postsMarkedForDeletion.length = 0;
        if (this._postsPageView) {
            this._postsPageView.refreshHighlights();
        }
    }

    // The array is handed to the page view through its render context, so it
    // has to be updated in place - replacing it would leave the view looking
    // at a stale copy.
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
                    this._postsPageView.poolCreated();
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

    // ---- rendering -------------------------------------------------------

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
                const canCreatePool = api.hasPrivilege("pools:create");
                const poolCategories = {};
                let defaultPoolCategory = "";
                for (let category of this._poolCategories || []) {
                    poolCategories[category.name] = category.name;
                    if (category.isDefault || !defaultPoolCategory) {
                        defaultPoolCategory = category.name;
                    }
                }

                Object.assign(pageCtx, {
                    canViewPosts: api.hasPrivilege("posts:view"),
                    enableSafety: api.safetyEnabled(),
                    canBulkEditTags: api.hasPrivilege("posts:bulk-edit:tags"),
                    canBulkEditSafety: api.hasPrivilege(
                        "posts:bulk-edit:safety"
                    ),
                    canBulkDelete: api.hasPrivilege("posts:bulk-edit:delete"),
                    canCreatePool: canCreatePool,
                    canSelect: this._availableModes.length > 0,
                    mode: this._mode,
                    editMode: this._editMode,
                    bulkEditTags: this._bulkEditTags.join(" "),
                    poolCategories: poolCategories,
                    defaultPoolCategory: defaultPoolCategory,
                    bulkEdit: {
                        markedForDeletion: this._postsMarkedForDeletion,
                    },
                    postFlow: settings.get().postFlow,
                });

                const view = new PostsPageView(pageCtx);
                this._postsPageView = view;
                if (canCreatePool && this._poolCategoryError) {
                    this._pageController.view.showError(
                        "Could not load pool categories: " +
                            this._poolCategoryError
                    );
                    this._poolCategoryError = null;
                }

                view.addEventListener("applyTags", (e) =>
                    this._evtApplyTags(e)
                );
                view.addEventListener("applySafety", (e) =>
                    this._evtApplySafety(e)
                );
                view.addEventListener("deleteSelected", (e) =>
                    this._evtDeleteSelected(e)
                );
                view.addEventListener("markForDeletion", (e) =>
                    this._evtMarkForDeletion(e)
                );
                view.addEventListener("clearDeleteMarks", () =>
                    this._evtClearDeleteMarks()
                );
                view.addEventListener("changeBulkTags", (e) =>
                    this._evtChangeBulkTags(e)
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
