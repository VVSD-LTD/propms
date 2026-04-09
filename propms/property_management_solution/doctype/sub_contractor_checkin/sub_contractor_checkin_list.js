frappe.listview_settings["Sub Contractor Checkin"] = {
	add_fields: ["offshift"],
	get_indicator: function (doc) {
		if (doc.offshift) {
			return [__("Off-Shift"), "yellow", "offshift,=,1"];
		}
	},
	onload: function (listview) {
		listview.page.add_action_item(__("Fetch Shifts"), () => {
			const checkins = listview.get_checked_items().map((checkin) => checkin.name);
			frappe.call({
				method: "propms.property_management_solution.doctype.sub_contractor_checkin.sub_contractor_checkin.bulk_fetch_shift",
				freeze: true,
				args: {
					checkins,
				},
			});
		});
	},
};