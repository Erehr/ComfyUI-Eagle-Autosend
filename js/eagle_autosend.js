import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

// Function to save a setting to the backend
async function setSetting(key, value) {
    try {
        await api.fetchApi("/eagle/set_setting", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key, value }),
        });
    } catch (e) {
        console.error("Failed to save Eagle settings", e);
    }
}

app.registerExtension({
	name: "Comfy.EagleAutosend",
	async setup() {
        // Fetch CSV files for the dropdown
        const response = await fetch("/eagle/list_csv_files");
        const csvFiles = await response.json();
        const csvOptions = csvFiles.map(file => ({ text: file, value: file }));
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

		// Main event listener
		api.addEventListener("executed", ({ detail }) => {
            if (!app.ui.settings.getSettingValue('Eagle.Autosend.Enable', true)) {
                return;
            }

			if (detail?.output?.images) {
                const folderName = app.ui.settings.getSettingValue('Eagle.Autosend.FolderName', '');

				for (const img of detail.output.images) {
					if (img.type !== "output") continue;

					const body = {
						filename: img.filename,
						subfolder: img.subfolder,
						type: img.type,
						folder: folderName || null,
					};

					api.fetchApi("/send-to-eagle", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(body),
					});
				}
			}
		});
	},
});
