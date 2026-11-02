// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Amenity", {
	refresh(frm) {
		lock_day_hours_grid(frm);
		ensure_day_hours_rows(frm);
	},
	onload(frm) {
		lock_day_hours_grid(frm);
		ensure_day_hours_rows(frm);
	},
});

const WEEKDAYS = [
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
];

function lock_day_hours_grid(frm) {
	const field = frm.get_field("day_hours");
	if (!field || !field.grid) return;
	// Fixed Mon–Sun rows only — no add/delete
	field.grid.cannot_add_rows = true;
	field.grid.cannot_delete_rows = true;
	field.grid.refresh();
}

function ensure_day_hours_rows(frm) {
	if (!frm.doc) return;
	const existing = new Set((frm.doc.day_hours || []).map((r) => r.day_of_week).filter(Boolean));
	let added = false;
	WEEKDAYS.forEach((day) => {
		if (!existing.has(day)) {
			frm.add_child("day_hours", {
				day_of_week: day,
				is_open: 1,
				open_time: "06:00:00",
				close_time: "22:00:00",
			});
			added = true;
		}
	});
	if (added) {
		frm.refresh_field("day_hours");
		lock_day_hours_grid(frm);
	}
}
