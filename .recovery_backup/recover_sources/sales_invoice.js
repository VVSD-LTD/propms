frappe.ui.form.on('Sales Invoice', {
	refresh: function(frm) {
		// Do NOT autofill on refresh — clearing/setting fields marks the form dirty ("Not Saved")
		propms_electricity_topup.refresh(frm);
	},
	lease: function(frm) {
		propms_electricity_autofill.from_lease(frm, true);
	},
	lease_name: function(frm) {
		propms_electricity_autofill.from_lease(frm, true);
	},
	is_pos: function(frm) {
		propms_electricity_autofill.from_lease(frm, false);
	},
	property_name: function(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "customer", "");
		if (frm.doc.cost_center) {
			frappe.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "Property",
					fieldname: "status",
					filters: {
						name: frm.doc.cost_center
					},
				},
				callback: function(r, rt) {
					if (r.message) {
						if (r.message.status == "On Lease") {
							frappe.call({
								method: "frappe.client.get_value",
								args: {
									doctype: "Lease",
									fieldname: "customer",
									filters: {
										property: frm.doc.cost_center
									},
								},
								callback: function(r, rt) {
									if (r.message) {
										frappe.model.set_value(cdt, cdn, "customer", r.message.customer);
									}
								}
							});
						}
					}
				}
			});
		} else {
			frappe.model.set_value(cdt, cdn, "customer", "");
		}
	}
});

frappe.ui.form.on('Sales Invoice Item', {
	item_code: function(frm) {
		propms_electricity_autofill.from_lease(frm, false);
	},
	items_remove: function(frm) {
		propms_electricity_autofill.from_lease(frm, false);
	}
});

var propms_electricity_autofill = {
	_busy: false,
	_catalog: null,

	load_catalog: function(callback) {
		if (propms_electricity_autofill._catalog) {
			callback(propms_electricity_autofill._catalog);
			return;
		}
		frappe.call({
			method: 'propms.api.v1.electricity.electricity.get_electricity_catalog_api',
			callback: function(r) {
				propms_electricity_autofill._catalog = r.message || {
					item_tanesco: 'Electricity - TANESCO',
					item_generator: 'Electricity - Generator',
					lease_item: 'Electricity',
					item_codes: ['Electricity - TANESCO', 'Electricity - Generator']
				};
				callback(propms_electricity_autofill._catalog);
			},
			error: function() {
				propms_electricity_autofill._catalog = {
					item_tanesco: 'Electricity - TANESCO',
					item_generator: 'Electricity - Generator',
					lease_item: 'Electricity',
					item_codes: ['Electricity - TANESCO', 'Electricity - Generator']
				};
				callback(propms_electricity_autofill._catalog);
			}
		});
	},

	has_electricity_items: function(frm, catalog) {
		var codes = (catalog && catalog.item_codes) || [];
		return (frm.doc.items || []).some(function(row) {
			return codes.indexOf(row.item_code) !== -1;
		});
	},

	set_if_changed: function(frm, fieldname, value) {
		if (!frm.fields_dict[fieldname]) {
			return;
		}
		var next = value || '';
		var cur = frm.doc[fieldname] || '';
		if (String(cur) === String(next)) {
			return;
		}
		frm.set_value(fieldname, next);
	},

	clear_populated_fields: function(frm) {
		if (frm.doc.docstatus !== 0 || frm.doc.selcom_order_id) {
			return;
		}
		propms_electricity_autofill.set_if_changed(frm, 'lease_item', '');
		propms_electricity_autofill.set_if_changed(frm, 'meter_number', '');
	},

	/**
	 * @param {boolean} clear_first - true when lease/lease_name changed (drop stale meter)
	 */
	from_lease: function(frm, clear_first) {
		if (frm.doc.docstatus !== 0 || frm.doc.selcom_order_id) {
			return;
		}
		if (propms_electricity_autofill._busy) {
			return;
		}

		propms_electricity_autofill.load_catalog(function(catalog) {
			if (clear_first) {
				propms_electricity_autofill.clear_populated_fields(frm);
			}

			if (!propms_electricity_autofill.has_electricity_items(frm, catalog)) {
				propms_electricity_autofill.clear_populated_fields(frm);
				return;
			}

			var lease = (frm.doc.lease || frm.doc.lease_name || '').trim();
			if (!lease) {
				propms_electricity_autofill.clear_populated_fields(frm);
				return;
			}

			propms_electricity_autofill._busy = true;
			frappe.call({
				method: 'propms.api.v1.electricity.manual_pos_topup.get_electricity_autofill_for_lease',
				args: { lease: lease },
				callback: function(r) {
					propms_electricity_autofill._busy = false;
					var current = (frm.doc.lease || frm.doc.lease_name || '').trim();
					if (current !== lease) {
						return;
					}
					var info = r.message || {};
					propms_electricity_autofill.set_if_changed(frm, 'lease_item', info.lease_item || '');
					propms_electricity_autofill.set_if_changed(frm, 'meter_number', info.meter_number || '');
					// Bill Lease POS Customer (not Lease Customer)
					if (info.customer || info.pos_customer) {
						propms_electricity_autofill.set_if_changed(
							frm,
							'customer',
							info.customer || info.pos_customer
						);
					}
				},
				error: function() {
					propms_electricity_autofill._busy = false;
				}
			});
		});
	}
};

var propms_electricity_topup = {
	refresh: function(frm) {
		frm.remove_custom_button(__('Retry Afritrack Top-up'));
		if (frm.doc.docstatus !== 1) {
			return;
		}

		propms_electricity_autofill.load_catalog(function(catalog) {
			var elec_lease = (catalog && catalog.lease_item) || 'Electricity';
			if (frm.doc.lease_item !== elec_lease || !cint(frm.doc.is_pos)) {
				return;
			}
			if (!(frm.doc.meter_number || '').trim()) {
				return;
			}

			frappe.call({
				method: 'propms.api.v1.electricity.manual_pos_topup.get_electricity_topup_status',
				args: { sales_invoice: frm.doc.name },
				callback: function(r) {
					var msg = r.message || {};
					if (!msg.show_retry) {
						if (msg.status === 'Success') {
							frm.dashboard.set_headline_alert(
								__('Meter topped up via Afritrack'),
								'green'
							);
						}
						return;
					}
					frm.add_custom_button(__('Retry Afritrack Top-up'), function() {
						frappe.call({
							method: 'propms.api.v1.electricity.manual_pos_topup.retry_manual_electricity_pos_topup',
							args: { sales_invoice: frm.doc.name },
							freeze: true,
							freeze_message: __('Calling Afritrack…'),
							callback: function(res) {
								var result = res.message || {};
								if (result.status === 'success' || result.idempotent) {
									frappe.show_alert({
										message: __('Meter top-up succeeded'),
										indicator: 'green'
									});
								} else {
									frappe.msgprint({
										title: __('Afritrack top-up'),
										message: __('Status: {0}. Check Afritrack Top-up Log for details.', [result.status || result.reason || 'unknown']),
										indicator: 'orange'
									});
								}
								frm.reload_doc();
							}
						});
					}).addClass('btn-primary');
				}
			});
		});
	}
};
