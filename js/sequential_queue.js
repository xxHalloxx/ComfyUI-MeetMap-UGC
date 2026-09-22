import { app } from "../../scripts/app.js";

const NODE_TYPE = "MeetMapSequentialRunControl";
const STATE_KEY = "__meetmapSequentialQueueState";
const PATCH_KEY = "__meetmapSequentialQueuePatched";

function widget(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function clampCount(value) {
    const parsed = Number.parseInt(value ?? 1, 10);
    if (!Number.isFinite(parsed)) return 1;
    return Math.max(1, Math.min(10, parsed));
}

function controllerNodes() {
    const nodes = app?.rootGraph?._nodes ?? [];
    return nodes.filter((node) => node?.type === NODE_TYPE);
}

function activeController() {
    return controllerNodes().find((node) => widget(node, "enable_sequential_queue")?.value !== false) ?? null;
}

function randomBatchSeed() {
    // Stay below JS Number's exact-integer limit and well inside ComfyUI's INT range.
    const hi = Math.floor(Math.random() * 0x1fffff);
    const lo = Math.floor(Math.random() * 0x100000000);
    return hi * 0x100000000 + lo;
}

app.registerExtension({
    name: "MeetMap.SequentialVideoQueue",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_TYPE) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const indexWidget = widget(this, "run_index");

            if (indexWidget && !indexWidget.__meetmapSequentialHooked) {
                indexWidget.__meetmapSequentialHooked = true;
                const originalBeforeQueued = indexWidget.beforeQueued;

                indexWidget.beforeQueued = async (context) => {
                    if (typeof originalBeforeQueued === "function") {
                        await originalBeforeQueued.call(indexWidget, context);
                    }

                    const state = this[STATE_KEY];
                    if (!state) return;

                    state.next += 1;
                    indexWidget.value = Math.max(1, Math.min(state.total, state.next));
                    this.setDirtyCanvas?.(true, true);
                };
            }

            return result;
        };
    },

    async setup() {
        if (app[PATCH_KEY]) return;
        app[PATCH_KEY] = true;

        const originalQueuePrompt = app.queuePrompt.bind(app);

        app.queuePrompt = async function (
            number,
            batchCount = 1,
            optionsOrQueueNodeIds = {}
        ) {
            const controller = activeController();
            if (!controller) {
                return originalQueuePrompt(number, batchCount, optionsOrQueueNodeIds);
            }

            const options = Array.isArray(optionsOrQueueNodeIds)
                ? {}
                : (optionsOrQueueNodeIds ?? {});

            // Do not multiply ComfyUI's automatic re-queue feature.
            if (options?.intent?.trigger_source === "auto_queue") {
                return originalQueuePrompt(number, batchCount, optionsOrQueueNodeIds);
            }

            const count = clampCount(widget(controller, "video_count")?.value);
            const baseSeedWidget = widget(controller, "base_seed");
            const randomize = widget(controller, "randomize_batch_seed")?.value !== false;
            const indexWidget = widget(controller, "run_index");

            if (randomize && baseSeedWidget) {
                baseSeedWidget.value = randomBatchSeed();
            }
            if (indexWidget) {
                indexWidget.value = 1;
            }

            controller[STATE_KEY] = { next: 0, total: count };

            try {
                // Native ComfyUI batchCount creates separate prompt submissions.
                // The backend processes them as independent queue jobs, so SaveVideo
                // and previews become available after each completed workflow run.
                return await originalQueuePrompt(
                    number,
                    count,
                    optionsOrQueueNodeIds
                );
            } finally {
                delete controller[STATE_KEY];
            }
        };
    },
});
