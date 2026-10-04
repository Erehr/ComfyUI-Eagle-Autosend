import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const LOG = "[Eagle Autosend]";

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

function libraryName(path) {
    return path ? path.split(/[\\/]/).filter(Boolean).pop().replace(/\.library$/i, "") : "";
}

// Remembered in a hidden setting so the dropdown still lists libraries while Eagle is closed.
function libraryOptions(value) {
    const paths = [...getSetting("Eagle.Autosend.LibraryCache", [])];
    if (value && !paths.includes(value)) paths.push(value);
    return [{ text: "Currently open library", value: "" }, ...paths.map(p => ({ text: `${libraryName(p)} (${p})`, value: p }))];
}

async function refreshLibraries() {
    try {
        const res = await api.fetchApi("/eagle/libraries");
        if (!res.ok) return;
        const { libraries } = await res.json();
        await app.ui.settings.setSettingValueAsync("Eagle.Autosend.LibraryCache", libraries);
    } catch (e) {
        console.error(LOG, "could not list Eagle libraries", e);
    }
}

async function syncNow() {
    try {
        const res = await api.fetchApi("/eagle/sync", { method: "POST" });
        const info = await res.json();
        if (info.status === "offline") {
            toast("warn", "Eagle Autosend", `Eagle isn't running at ${info.host}. ${info.pending} image(s) still queued.`);
            return;
        }
        toast("info", "Eagle Autosend", `Sent ${info.sent} image(s) to ${libraryName(info.current) || "Eagle"}. ${info.pending} still queued.`);
        refreshLibraries();
    } catch (e) {
        console.error(LOG, "sync failed", e);
    }
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

async function sendImage(img) {
    const body = {
        filename: img.filename,
        subfolder: img.subfolder ?? "",
        type: img.type,
        folder: folderName(),
        library: getSetting("Eagle.Autosend.Library", ""),
    };

    try {
        const res = await api.fetchApi("/send-to-eagle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });

        if (!res.ok) {
            const text = await res.text().catch(() => "");
            console.error(LOG, `send failed (${res.status})`, body, text);
            return;
        }

        const info = await res.json();
        console.debug(LOG, info.status, body.filename, info);
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
				id: "Eagle.Autosend.Sync",
				name: "Send Queued Images",
				tooltip: "Images are queued while Eagle is closed or another library is open, and retried on every generation. This retries now.",
				type: () => {
					const button = document.createElement("button");
					button.className = "p-button p-component";
					button.textContent = "Sync now";
					button.onclick = async () => {
						button.disabled = true;
						await syncNow();
						button.disabled = false;
					};
					return button;
				},
				defaultValue: null,
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.Library",
				name: "Eagle Library",
				tooltip: "Images are only sent while this library is open in Eagle; otherwise they are queued. The list comes from Eagle's recent libraries.",
				type: "combo",
				defaultValue: "",
				options: libraryOptions,
			});

			app.ui.settings.addSetting({
				id: "Eagle.Autosend.LibraryCache",
				name: "Eagle Library Cache",
				type: "hidden",
				defaultValue: [],
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

			refreshLibraries();
		} catch (e) {
			console.error(LOG, "settings registration failed (autosend still active)", e);
		}
	},
});
