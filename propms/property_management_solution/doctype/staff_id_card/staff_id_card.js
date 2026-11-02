// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Staff ID Card", {
	setup(frm) {
		frm.set_query("lease", () => ({
			filters: {
				lease_status: "Active",
				...(frm.doc.property_unit ? { property: frm.doc.property_unit } : {}),
			},
		}));
	},

	refresh(frm) {
		frm.set_df_property("job_role_other", "reqd", frm.doc.job_role === "Other");
		_toggle_staff_fields(frm);
		// Quota UI paused — client has not approved yet.
		// _show_quota_alert(frm);
		["quota_info", "is_paid_issuance", "fee_note"].forEach((f) => {
			frm.set_df_property(f, "hidden", 1);
		});

		if (frm.is_new() && !frm.doc.submitted_by) {
			frm.doc.submitted_by = frappe.session.user;
		}

		// Approve/Reject only after Frappe Submit (docstatus 1) while still pending
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.status === "Submitted" &&
			!frm.is_new()
		) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.prompt(
					[
						{
							fieldname: "id_card_no",
							fieldtype: "Data",
							label: __("ID Card No."),
							reqd: 1,
						},
						{
							fieldname: "issue_date",
							fieldtype: "Date",
							label: __("Date of Issue"),
							reqd: 1,
							default: frappe.datetime.get_today(),
						},
						{
							fieldname: "scanned_id_image",
							fieldtype: "Attach Image",
							label: __("Scanned ID Image"),
							reqd: 1,
						},
					],
					(values) => {
						frappe.call({
							method: "propms.api.mobile.approve_staff_id_card",
							args: {
								card_id: frm.doc.name,
								id_card_no: values.id_card_no,
								issue_date: values.issue_date,
								scanned_id_image: values.scanned_id_image,
							},
							freeze: true,
							callback(r) {
								if (r.message && r.message.status === "success") {
									frm.reload_doc();
								} else {
									frappe.msgprint(
										(r.message && r.message.message) || __("Approve failed")
									);
								}
							},
						});
					},
					__("Approve Staff ID Card"),
					__("Approve")
				);
			}, __("Actions"));

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					[
						{
							fieldname: "rejection_reason",
							fieldtype: "Small Text",
							label: __("Rejection Reason"),
							reqd: 1,
						},
					],
					(values) => {
						frappe.call({
							method: "propms.api.mobile.reject_staff_id_card",
							args: {
								card_id: frm.doc.name,
								rejection_reason: values.rejection_reason,
							},
							freeze: true,
							callback(r) {
								if (r.message && r.message.status === "success") {
									frm.reload_doc();
								} else {
									frappe.msgprint(
										(r.message && r.message.message) || __("Reject failed")
									);
								}
							},
						});
					},
					__("Reject Staff ID Card"),
					__("Reject")
				);
			}, __("Actions"));
		}
	},

	property_unit(frm) {
		if (!frm.doc.property_unit) {
			_clear_resident(frm);
			return;
		}
		_load_resident_for_unit(frm);
	},

	validate(frm) {
		if (!frm.doc.property_unit) {
			frappe.msgprint({
				title: __("Apartment / Unit required"),
				indicator: "red",
				message: __(
					"Select <b>Apartment / Unit</b> at the top of the form first. Resident details will fill automatically."
				),
			});
			frappe.validated = false;
			return;
		}

		// If unit chosen but resident not filled yet (async race), resolve sync before save
		if (!frm.doc.tenant) {
			const r = frappe.call({
				method:
					"propms.property_management_solution.doctype.staff_id_card.staff_id_card.get_resident_for_unit",
				args: { property_unit: frm.doc.property_unit },
				async: false,
			});
			const info = r && r.message;
			if (info) {
				if ((info.residents || []).length > 1 && !frm.doc.tenant) {
					// Pick first for save; user can re-open to change unit / we already prompt on change
					_apply_resident_info(frm, info);
				} else {
					_apply_resident_info(frm, info);
				}
			}
		}

		if (!frm.doc.tenant) {
			frappe.msgprint({
				title: __("Resident not found"),
				indicator: "red",
				message: __(
					"No resident App User on an Active Lease for this apartment. Link a user on Tenant Details, then try again."
				),
			});
			frappe.validated = false;
		}
	},

	job_role(frm) {
		frm.set_df_property("job_role_other", "reqd", frm.doc.job_role === "Other");
	},
});

function cint(v) {
	return frappe.utils ? frappe.utils.cint(v) : parseInt(v || 0, 10) || 0;
}

function _load_resident_for_unit(frm, tenant) {
	frappe.call({
		method:
			"propms.property_management_solution.doctype.staff_id_card.staff_id_card.get_resident_for_unit",
		args: { property_unit: frm.doc.property_unit, tenant: tenant || null },
		freeze: true,
		freeze_message: __("Loading resident for unit…"),
		callback(r) {
			const info = r.message;
			if (!info) {
				return;
			}
			const residents = info.residents || [];
			// Only ask when 2+ residents; single resident fills automatically
			if (!tenant && residents.length > 1) {
				const options = residents.map(
					(x) => `${x.tenant_name} (${x.tenant})`
				);
				frappe.prompt(
					[
						{
							fieldname: "resident",
							fieldtype: "Select",
							label: __("Resident"),
							options: options.join("\n"),
							reqd: 1,
							default: options[0],
						},
					],
					(values) => {
						const matched = residents.find(
							(x) => `${x.tenant_name} (${x.tenant})` === values.resident
						);
						_load_resident_for_unit(frm, matched ? matched.tenant : null);
					},
					__("Select resident for this unit"),
					__("Continue")
				);
				return;
			}
			_apply_resident_info(frm, info);
		},
	});
}

function _set_ro(frm, field, value) {
	// Read-only fields sometimes ignore set_value — unlock, set, re-lock
	frm.set_df_property(field, "read_only", 0);
	frm.doc[field] = value;
	if (frm.fields_dict[field]) {
		frm.fields_dict[field].set_value(value);
	}
	frm.set_df_property(field, "read_only", 1);
}

function _apply_resident_info(frm, info) {
	_set_ro(frm, "lease", info.lease);
	_set_ro(frm, "tenant", info.tenant);
	_set_ro(frm, "tenant_name", info.tenant_name);
	_set_ro(frm, "resident_mobile", info.resident_mobile);
	_set_ro(frm, "resident_email", info.resident_email);
	// Quota fields hidden / paused — keep values filled for when re-enabled.
	// _set_ro(frm, "quota_info", info.quota_info);
	// _set_ro(frm, "fee_note", info.fee_note);
	// frm.doc.is_paid_issuance = cint(info.is_paid_issuance);
	// if (frm.fields_dict.is_paid_issuance) {
	// 	frm.fields_dict.is_paid_issuance.refresh();
	// }

	if (info.tenant && info.tenant !== frappe.session.user) {
		frm.doc.on_behalf = 1;
		frm.doc.declaration_accepted = 1;
		if (frm.fields_dict.on_behalf) {
			frm.fields_dict.on_behalf.refresh();
		}
		if (frm.fields_dict.declaration_accepted) {
			frm.fields_dict.declaration_accepted.refresh();
		}
	}
	if (frm.is_new() && !frm.doc.submitted_by) {
		_set_ro(frm, "submitted_by", frappe.session.user);
	}

	frm.dirty();
	// Quota UI paused — client has not approved yet.
	// _show_quota_alert(frm, info);
	// if (cint(info.free_slots_remaining) <= 0) {
	// 	frappe.msgprint({
	// 		title: __("Free quota used"),
	// 		indicator: "orange",
	// 		message: __(
	// 			"This resident already has {0} free Staff ID cards. Further cards cost Tshs 20,000/= — you cannot save until management handles payment.",
	// 			[info.free_card_limit]
	// 		),
	// 	});
	// }
}

function _clear_resident(frm) {
	["lease", "tenant", "tenant_name", "resident_mobile", "resident_email", "quota_info", "fee_note"].forEach(
		(f) => {
			frm.doc[f] = null;
			if (frm.fields_dict[f]) {
				frm.fields_dict[f].refresh();
			}
		}
	);
	frm.doc.is_paid_issuance = 0;
	frm.doc.on_behalf = 0;
	frm.dirty();
}

function _toggle_staff_fields(frm) {
	const locked = ["Approved", "Rejected", "Cancelled"].includes(frm.doc.status);
	[
		"property_unit",
		"staff_full_name",
		"nationality",
		"gender",
		"date_of_birth",
		"staff_mobile",
		"job_role",
		"job_role_other",
		"passport_photo",
		"id_copy",
		"other_document",
	].forEach((field) => {
		frm.set_df_property(field, "read_only", locked ? 1 : 0);
	});
}

function _show_quota_alert(frm, info) {
	frm.dashboard.clear_headline();
	const text = (info && info.quota_info) || frm.doc.quota_info;
	if (!text) {
		return;
	}
	const remaining =
		info && info.free_slots_remaining != null
			? cint(info.free_slots_remaining)
			: null;
	const color = remaining === 0 ? "orange" : "blue";
	frm.dashboard.set_headline_alert(text, color);
}
