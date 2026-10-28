"use strict";

const api = require("../api.js");
const uri = require("../util/uri.js");
const tasks = require("../util/tasks.js");
const Post = require("./post.js");
const TagList = require("./tag_list.js");

const PAGE_SIZE = 100;
const FILE_RULES_STORAGE_KEY = "tagDiagnosisFileRules";

// How many posts a single "fix" pass will touch. A mistyped rule can match the
// whole booru, so the apply step is deliberately bounded and repeatable rather
// than unbounded.
const APPLY_CAP = 1000;

// Rules derived from what szurubooru already detected about the file itself:
// the post type it assigned, and the sound/loop flags it set while processing
// the upload. Tag names match the ones this booru already uses.
const DEFAULT_FILE_RULES = [
    { condition: "type:video", tags: ["video"] },
    { condition: "type:animation", tags: ["gif"] },
    { condition: "type:animation,video", tags: ["animated"] },
    { condition: "flag:sound", tags: ["sound_warning"] },
];

// Escapes the characters szurubooru's search parser treats specially, so that a
// tag like `2b_(nier:automata)` or `th07.5` is matched literally instead of
// being read as a named token or a range.
function escapeTerm(text) {
    return String(text).replace(/([\\:.,*])/g, "\\$1");
}

// The search that returns exactly the posts in violation of a check: they match
// the condition but are missing at least one of the tags it requires.
function buildQuery(condition, tags) {
    return [condition]
        .concat(tags.map((tag) => "-" + escapeTerm(tag)))
        .join(" ");
}

function countPosts(query) {
    // Deliberately not routed through PostList.search(): that decorates the
    // query with the browsing safety filters, which would hide part of the
    // booru from an audit that is supposed to cover all of it.
    return api
        .get(
            uri.formatApiLink("posts", {
                query: query,
                limit: 1,
                fields: "id",
            })
        )
        .then((response) => response.total);
}

// Walks every page of a search and returns the posts, carrying the version and
// tags needed to save them again without a second request each.
function fetchPosts(query, maxCount) {
    const posts = [];
    const step = (offset) =>
        api
            .get(
                uri.formatApiLink("posts", {
                    query: query,
                    offset: offset,
                    limit: PAGE_SIZE,
                    fields: "id,version,tags",
                })
            )
            .then((response) => {
                for (let item of response.results) {
                    posts.push(Post.fromResponse(item));
                }
                const seen = offset + response.results.length;
                if (
                    !response.results.length ||
                    posts.length >= maxCount ||
                    seen >= response.total
                ) {
                    return { posts: posts, total: response.total };
                }
                return step(seen);
            });
    return step(0);
}

function loadFileRules() {
    try {
        const stored = JSON.parse(
            localStorage.getItem(FILE_RULES_STORAGE_KEY)
        );
        if (Array.isArray(stored)) {
            return stored.filter(
                (rule) =>
                    rule &&
                    rule.condition &&
                    Array.isArray(rule.tags) &&
                    rule.tags.length
            );
        }
    } catch (e) {
        // fall through to the defaults
    }
    return DEFAULT_FILE_RULES.map((rule) => ({
        condition: rule.condition,
        tags: [...rule.tags],
    }));
}

function saveFileRules(rules) {
    localStorage.setItem(FILE_RULES_STORAGE_KEY, JSON.stringify(rules));
}

function resetFileRules() {
    localStorage.removeItem(FILE_RULES_STORAGE_KEY);
    return loadFileRules();
}

// Rules are edited as text, one per line: `<search> -> <tag> [<tag>...]`.
function parseFileRules(text) {
    const rules = [];
    const errors = [];
    const lines = text.split("\n");
    for (let [index, line] of lines.entries()) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) {
            continue;
        }
        const parts = trimmed.split(/\s*(?:->|=>)\s*/);
        if (parts.length !== 2 || !parts[0] || !parts[1]) {
            errors.push(`Line ${index + 1}: expected "<search> -> <tags>"`);
            continue;
        }
        const tags = parts[1].split(/\s+/).filter((tag) => tag);
        if (!tags.length) {
            errors.push(`Line ${index + 1}: no tags given`);
            continue;
        }
        rules.push({ condition: parts[0], tags: tags });
    }
    return { rules: rules, errors: errors };
}

function formatFileRules(rules) {
    return rules
        .map((rule) => `${rule.condition} -> ${rule.tags.join(" ")}`)
        .join("\n");
}

function makeCheck(group, label, condition, tags) {
    const query = buildQuery(condition, tags);
    return {
        id: `${group}:${condition}:${tags.join(" ")}`,
        group: group,
        label: label,
        condition: condition,
        tags: tags,
        query: query,
        // the post list filtered to exactly the posts in violation
        postsLink: uri.formatClientLink("posts", { query: query }),
        affected: null,
        scanning: false,
        error: null,
    };
}

function fileRuleChecks(rules) {
    return rules.map((rule) =>
        makeCheck(
            "file",
            `${rule.condition} → ${rule.tags.join(" ")}`,
            rule.condition,
            rule.tags
        )
    );
}

// Turns the booru's own tag implications into checks. An implication only fires
// when a tag is applied, so posts tagged before the implication existed never
// got the implied tag - that backlog is exactly what this finds.
function implicationChecks() {
    const checks = [];
    const collect = (offset) =>
        TagList.search("", offset, PAGE_SIZE, [
            "names",
            "implications",
            "usages",
        ]).then((response) => {
            for (let tag of response.results) {
                // nothing is tagged with it, so nothing can be missing
                if (!tag.postCount) {
                    continue;
                }
                const parent = tag.names[0];
                for (let implied of tag.implications) {
                    const child = implied.names[0];
                    checks.push(
                        makeCheck(
                            "implication",
                            `${parent} → ${child}`,
                            escapeTerm(parent),
                            [child]
                        )
                    );
                }
            }
            const seen = offset + response.results.length;
            if (!response.results.length || seen >= response.total) {
                return checks;
            }
            return collect(seen);
        });
    return collect(0);
}

// Counts the violations for every check, a few at a time, reporting each result
// as it lands so the table can fill in progressively.
function scan(checks, options) {
    options = options || {};
    const onResult = options.onResult || (() => {});
    return tasks.runWithConcurrency(
        checks,
        (check) => {
            check.scanning = true;
            check.error = null;
            return countPosts(check.query).then(
                (total) => {
                    check.scanning = false;
                    check.affected = total;
                    onResult(check);
                    return total;
                },
                (error) => {
                    check.scanning = false;
                    check.affected = null;
                    check.error = error.message || "Search failed";
                    onResult(check);
                    throw error;
                }
            );
        },
        {
            concurrency: options.concurrency || 4,
            onProgress: options.onProgress,
            isCancelled: options.isCancelled,
        }
    );
}

// Adds a check's tags to every post that is missing them. Posts are collected
// first and only then written, because saving changes what the query matches.
function applyCheck(check, options) {
    options = options || {};
    const onProgress = options.onProgress || (() => {});
    return fetchPosts(check.query, APPLY_CAP).then((found) =>
        tasks
            .runWithConcurrency(
                found.posts,
                (post) =>
                    Promise.all(
                        check.tags.map((tag) => post.tags.addByName(tag))
                    ).then(() => post.save()),
                {
                    concurrency: options.concurrency || 4,
                    onProgress: onProgress,
                    isCancelled: options.isCancelled,
                }
            )
            .then((outcome) => ({
                attempted: found.posts.length,
                total: found.total,
                truncated: found.total > found.posts.length,
                failed: outcome.errors.length,
                errors: outcome.errors,
            }))
    );
}

module.exports = {
    DEFAULT_FILE_RULES: DEFAULT_FILE_RULES,
    APPLY_CAP: APPLY_CAP,
    escapeTerm: escapeTerm,
    buildQuery: buildQuery,
    countPosts: countPosts,
    implicationChecks: implicationChecks,
    fileRuleChecks: fileRuleChecks,
    loadFileRules: loadFileRules,
    saveFileRules: saveFileRules,
    resetFileRules: resetFileRules,
    parseFileRules: parseFileRules,
    formatFileRules: formatFileRules,
    scan: scan,
    applyCheck: applyCheck,
};
