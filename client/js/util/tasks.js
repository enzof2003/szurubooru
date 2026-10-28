"use strict";

const DEFAULT_CONCURRENCY = 4;

// Runs `worker` over every item with a bounded number of requests in flight.
//
// Unlike Promise.all() this keeps going when an individual item fails, which is
// what batch operations over posts need: one post with a stale version must not
// abort the remaining hundred. Resolves with the results indexed like `items`,
// plus every error that was collected along the way.
//
// options:
//   concurrency  how many workers may run at once (default 4)
//   onProgress   called as (finished, total) after each item settles
//   isCancelled  polled after each item; once true, no new work is started
function runWithConcurrency(items, worker, options) {
    options = options || {};
    const concurrency = Math.max(
        1,
        options.concurrency || DEFAULT_CONCURRENCY
    );
    const onProgress = options.onProgress || (() => {});
    const isCancelled = options.isCancelled || (() => false);

    const total = items.length;
    const results = new Array(total);
    const errors = [];
    let next = 0;
    let active = 0;
    let finished = 0;
    let cancelled = false;

    return new Promise((resolve) => {
        const pump = () => {
            while (!cancelled && active < concurrency && next < total) {
                const index = next;
                next += 1;
                active += 1;
                Promise.resolve()
                    .then(() => worker(items[index], index))
                    .then(
                        (result) => {
                            results[index] = result;
                        },
                        (error) => {
                            errors.push({
                                item: items[index],
                                index: index,
                                error: error,
                            });
                        }
                    )
                    .then(() => {
                        active -= 1;
                        finished += 1;
                        onProgress(finished, total);
                        if (isCancelled()) {
                            cancelled = true;
                        }
                        pump();
                    });
            }
            if (active === 0 && (cancelled || next >= total)) {
                resolve({
                    results: results,
                    errors: errors,
                    cancelled: cancelled,
                });
            }
        };
        pump();
    });
}

// Turns the errors collected by runWithConcurrency() into one short message.
// Repeated failures are almost always the same cause, so only the first few
// distinct messages are worth showing.
function summarizeErrors(errors, limit) {
    if (!errors.length) {
        return null;
    }
    const messages = [];
    for (let entry of errors) {
        const message = entry.error ? entry.error.message : "Unknown error";
        if (!messages.includes(message)) {
            messages.push(message);
        }
    }
    const shown = messages.slice(0, limit || 3);
    let text = `${errors.length} operation(s) failed: ${shown.join("; ")}`;
    if (messages.length > shown.length) {
        text += `; and ${messages.length - shown.length} other error(s)`;
    }
    return text;
}

module.exports = {
    runWithConcurrency: runWithConcurrency,
    summarizeErrors: summarizeErrors,
};
