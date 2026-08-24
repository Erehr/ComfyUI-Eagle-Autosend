import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const LOG = "[Eagle Autosend]";

// Don't nag once per image: at most one "Eagle is closed" notice per minute.
const OFFLINE_NOTICE_COOLDOWN_MS = 60000;
let lastOfflineNotice = 0;

// Function to save a setting to the backend
async function setSetting(key, value) {
    try {
        await api.fetchApi("/eagle/set_setting", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key, value }),
        });
    } catch (e) {
        console.error(LOG, "Failed to save Eagle settings", e);
    }
}

function getSetting(id, fallback) {
    try {
        const v = app.ui.settings.getSettingValue(id, fallback);
        return v === undefined || v === null ? fallback : v;
    } catch (e) {
        return fallback;
    }
}

function isEnabled() {
    return !!getSetting("Eagle.Autosend.Enable", true);
}

function folderName() {
    return getSetting("Eagle.Autosend.FolderName", "") || null;
}

function toast(severity, summary, detail) {
    try {
        if (app.extensionManager?.toast?.add) {
            app.extensionManager.toast.add({ severity, summary, detail, life: 6000 });
            return true;
        }
    } catch (e) { /* fall through to console */ }
    return false;
}

// Eagle isn't running. Report it according to the user's preference, at most
// once per cooldown window, and never as a thrown error.
function reportOffline(info) {
    const mode = getSetting("Eagle.Autosend.OfflineNotice", "Notification");
    if (mode === "Silent") return;

    const now = Date.now();
    if (now - lastOfflineNotice < OFFLINE_NOTICE_COOLDOWN_MS) return;
    lastOfflineNotice = now;

    const host = info?.host || "the configured host";
    const msg = `Eagle isn't running at ${host} - generated images are not being sent.`;

    console.warn(LOG, msg);
    if (mode === "Notification") {
        toast("warn", "Eagle Autosend", msg);
    }
}

async function sendImage(img) {
    const body = {
        filename: img.filename,
        subfolder: img.subfolder ?? "",
        type: img.type,
        folder: folderName(),
    };

    try {
        const res = await api.fetchApi("/send-to-eagle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });

        if (res.status === 503) {
            // Eagle is closed - an expected condition, not an error.
            const info = await res.json().catch(() => ({}));
            if (!info?.quiet) reportOffline(info);
            return;
        }

        if (!res.ok) {
            const text = await res.text().catch(() => "");
            console.error(LOG, `send failed (${res.status})`, body, text);
            return;
        }

        console.debug(LOG, "sent", body.filename);
    } catch (e) {
        console.error(LOG, "send request threw", body, e);
    }
}

app.registerExtension({
	name: "Comfy.EagleAutosend",
	async setup() {
		// Register the listener FIRST, so a failure further down (settings API
		// changes, CSV fetch, etc.) can never leave us without it.
		api.addEventListener("executed", ({ detail }) => {
			if (!isEnabled()) return;

			const images = detail?.output?.images;
			if (!images?.length) return;

			for (const img of images) {
				if (img.type !== "output") {
					console.debug(LOG, "skipping non-output image", img);
					continue;
				}
				sendImage(img);
			}
		});

		try {
			// Fetch CSV files for the dropdown
			let csvOptions = [];
			try {
				const response = await api.fetchApi("/eagle/list_csv_files");
				const csvFiles = await response.json();
				csvOptions = csvFiles.map(file => ({ text: file, value: file }));
			} catch (e) {
				console.error(LOG, "could not list CSV files", e);
			}
			if (csvOptions.length === 0) {
				csvOptions.push({ text: "No CSV files found", value: "" });
			}

			// Register all settings under the "Eagle" group.
			// Settings are added in reverse order to appear correctly in the UI.
			app.ui.settings.addSetting({
				id: "Eagle.Autosend.OfflineNotice",
				name: "When Eagle Is Not Running",
				tooltip: "How to report that images could not be sent because Eagle is closed.",
				type: "combo",
				defaultValue: "Notification",
				options: ["Silent", "Console", "Notification"].map(v => ({ text: v, value: v })),
				onChange: (newVal) => setSetting("eagle.autosend.offlineNotice", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.TagsAlias",
				name: "Tag Alias Handling",
				type: "combo",
				defaultValue: "Use main",
				options: ["Use alias", "Use main", "Use both"].map(v => ({ text: v, value: v })),
				onChange: (newVal) => setSetting("eagle.autosend.tagsAlias", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.TagsCSV",
				name: "Tag Filter CSV File",
				type: "combo",
				defaultValue: csvOptions.length > 0 ? csvOptions[0].value : "",
				options: csvOptions,
				onChange: (newVal) => setSetting("eagle.autosend.tagsCsv", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.Tags",
				name: "Tag Source",
				type: "combo",
				defaultValue: "Positive",
				options: ["None", "Positive", "Positive (filtered)"].map(v => ({ text: v, value: v })),
				onChange: (newVal) => setSetting("eagle.autosend.tags", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.Annotation",
				name: "Annotation Content",
				type: "combo",
				defaultValue: "Parameters",
				options: ["None", "Parameters", "Prompt", "Positive Prompt"].map(v => ({ text: v, value: v })),
				onChange: (newVal) => setSetting("eagle.autosend.annotation", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.FolderName",
				name: "Eagle Folder Name",
				type: "text",
				defaultValue: "",
				onChange: (newVal) => setSetting("eagle.autosend.folderName", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.Enable",
				name: "Enable Autosend to Eagle",
				type: "boolean",
				defaultValue: true,
				onChange: (newVal) => setSetting("eagle.autosend.enable", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.Token",
				name: "Eagle API Token",
				type: "text",
				defaultValue: "",
				onChange: (newVal) => setSetting("eagle.autosend.token", newVal),
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.HostUrl",
				name: "Eagle Host URL",
				type: "text",
				defaultValue: "http://localhost:41595",
				onChange: (newVal) => setSetting("eagle.autosend.hostUrl", newVal),
			});
		} catch (e) {
			console.error(LOG, "settings registration failed (autosend still active)", e);
		}
	},
});
