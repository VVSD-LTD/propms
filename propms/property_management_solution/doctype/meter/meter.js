// Copyright (c) 2019, Aakvatech and contributors
// For license information, please see license.txt

frappe.ui.form.on("Meter", {
	onload(frm) {
		frm.set_query("meter_type", function () {
			return {
				filters: [["Item", "reading_required", "=", "1"]],
			};
		});
	},
	refresh(frm) {
		toggle_trackspm_sections(frm);
		lock_trackspm_fields(frm);
	},
	meter_type(frm) {
		toggle_trackspm_sections(frm);
	},
});

function is_cooking_gas(frm) {
	return (frm.doc.meter_type || "").trim() === "Cooking Gas";
}

function toggle_trackspm_sections(frm) {
	const show = !is_cooking_gas(frm);
	[
		"trackspm_section",
		"trackspm_location_section",
		"trackspm_tanesco_section",
		"trackspm_generator_section",
	].forEach(function (df) {
		frm.toggle_display(df, show);
	});
}

function lock_trackspm_fields(frm) {
	(frm.meta.fields || []).forEach(function (df) {
		if (df.fieldname && df.fieldname.indexOf("trackspm_") === 0) {
			frm.set_df_property(df.fieldname, "read_only", 1);
		}
	});
}
