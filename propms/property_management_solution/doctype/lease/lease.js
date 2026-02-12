cur_frm.add_fetch('property', 'unit_owner', 'property_owner');

frappe.ui.form.on('Lease', {
	setup: function(frm) {
		frm.set_query("lease_item", "lease_item", function() {
			return {
				"filters": [
                    ["item_group","=", "Lease Items"],
				]
			};
		});
		frm.set_query("property", function() {
			return {
				"filters": {
                    "company": frm.doc.company,
				},
			};
		});
	},
	after_save: function(frm) {
		make_lease_invoice_schedule(frm);
	},

	refresh: function(frm) {
		cur_frm.add_custom_button(__("Make Invoice Schedule"), function() {
			make_lease_invoice_schedule(cur_frm);
		});
		cur_frm.add_custom_button(__("Generate Pending Invoice"), function() {
			generate_pending_invoice();
		});
		cur_frm.add_custom_button(__("Make Invoice Schedule for all Lease"), function() {
			getAllLease(cur_frm);
		});

        // Add custom buttons for Accounts Receivable and Accounting Ledger
        if (!frm.doc.__islocal) {
            // Add "Accounts Receivable" custom button
            frm.add_custom_button(
                __("Accounts Receivable"),
                function () {
                    frappe.set_route("query-report", "Accounts Receivable", {
                        party_type: "Customer",
                        party: frm.doc.lease_customer,
                    });
                },
                __("View")
            );

            // Add "Accounting Ledger" custom button
            frm.add_custom_button(
                __("Accounting Ledger"),
                function () {
                    frappe.set_route("query-report", "General Ledger", {
                        party_type: "Customer",
                        party: frm.doc.lease_customer,
                    });
                },
                __("View")
            );
        }
	},
	skip_end_date: function(frm) {
		if (frm.doc.skip_end_date) {
			frm.set_df_property('end_date', 'hidden', 1);
		}else{
			frm.set_df_property('end_date', 'hidden', 0);
		}
	},
	onload: function(frm) {
			frappe.realtime.on("lease_invoice_schedule_progress", function(data) {
			if (data.reload && data.reload === 1) {
				frm.reload_doc();
			}
			if (data.progress) {
				let progress_bar = $(cur_frm.dashboard.progress_area).find(".progress-bar");
				if (progress_bar) {
					$(progress_bar).removeClass("progress-bar-danger").addClass("progress-bar-success progress-bar-striped");
					$(progress_bar).css("width", data.progress+"%");
				}
			}
		});
	},
	validate: function(frm) {
		// Validate lease items before saving
		validate_lease_items(frm);
		
		if (frm.doc.skip_end_date) {
			frappe.call({
				method: "frappe.client.get",
				args: {
					doctype: "Property Management Settings"
				},
				callback: function(r) {
					if (r.message) {
						let settings = r.message;
						if (settings.make_single_invoice_on_lease && !frm.doc.end_date) {
							frm.clear_table('lease_invoice_schedule');
							$.each(frm.doc.lease_item || [], function(i, row) {
								let invoice_entry = frm.add_child('lease_invoice_schedule');
								invoice_entry.lease_item_name = row.lease_item;
								invoice_entry.rate = row.amount;
								invoice_entry.paid_by = row.paid_by;
								invoice_entry.date_to_invoice = frm.doc.start_date; // Use start date as invoice date
								invoice_entry.qty = 1;
							});
							frm.refresh_field('lease_invoice_schedule');
						}
					}
				}
			});
		}
    }
});

frappe.ui.form.on('Lease Item', {
	valid_from: function(frm, cdt, cdn) {
		validate_lease_item_dates(frm, cdt, cdn);
	},
	valid_to: function(frm, cdt, cdn) {
		validate_lease_item_dates(frm, cdt, cdn);
	},
	lease_item: function(frm, cdt, cdn) {
		// Check for overlapping dates when lease item changes
		validate_lease_item_dates(frm, cdt, cdn);
	}
});

// Validate individual lease item dates
function validate_lease_item_dates(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	
	if (!row.valid_from && !row.valid_to) {
		// No validation needed if both are empty
		return;
	}
	
	let lease_start = frappe.datetime.str_to_obj(frm.doc.start_date);
	let lease_end = frm.doc.end_date ? frappe.datetime.str_to_obj(frm.doc.end_date) : null;
	
	// Validate valid_from
	if (row.valid_from) {
		let valid_from = frappe.datetime.str_to_obj(row.valid_from);
		
		// valid_from should not be before lease start_date
		if (valid_from < lease_start) {
			frappe.model.set_value(cdt, cdn, 'valid_from', '');
			frappe.msgprint({
				title: __('Invalid Date'),
				indicator: 'red',
				message: __('Valid From date cannot be before Lease Start Date ({0})', [frm.doc.start_date])
			});
			frappe.validated = false;
			return;
		}
		
		// valid_from should not be after lease end_date (if skip_end_date is not checked)
		if (lease_end && !frm.doc.skip_end_date && valid_from > lease_end) {
			frappe.model.set_value(cdt, cdn, 'valid_from', '');
			frappe.msgprint({
				title: __('Invalid Date'),
				indicator: 'red',
				message: __('Valid From date cannot be after Lease End Date ({0})', [frm.doc.end_date])
			});
			frappe.validated = false;
			return;
		}
	}
	
	// Validate valid_to
	if (row.valid_to) {
		let valid_to = frappe.datetime.str_to_obj(row.valid_to);
		
		// valid_to should not be before lease start_date
		if (valid_to < lease_start) {
			frappe.model.set_value(cdt, cdn, 'valid_to', '');
			frappe.msgprint({
				title: __('Invalid Date'),
				indicator: 'red',
				message: __('Valid To date cannot be before Lease Start Date ({0})', [frm.doc.start_date])
			});
			frappe.validated = false;
			return;
		}
		
		// valid_to should not be after lease end_date (if skip_end_date is not checked)
		if (lease_end && !frm.doc.skip_end_date && valid_to > lease_end) {
			frappe.model.set_value(cdt, cdn, 'valid_to', '');
			frappe.msgprint({
				title: __('Invalid Date'),
				indicator: 'red',
				message: __('Valid To date cannot be after Lease End Date ({0})', [frm.doc.end_date])
			});
			frappe.validated = false;
			return;
		}
	}
	
	// Validate valid_from vs valid_to
	if (row.valid_from && row.valid_to) {
		let valid_from = frappe.datetime.str_to_obj(row.valid_from);
		let valid_to = frappe.datetime.str_to_obj(row.valid_to);
		
		if (valid_from > valid_to) {
			frappe.model.set_value(cdt, cdn, 'valid_to', '');
			frappe.msgprint({
				title: __('Invalid Date Range'),
				indicator: 'red',
				message: __('Valid From date cannot be after Valid To date')
			});
			frappe.validated = false;
			return;
		}
	}
	
	// Check for overlapping dates with other lease items
	check_date_overlaps(frm, row);
}

// Check for date overlaps between lease items with the same name
function check_date_overlaps(frm, current_row) {
	if (!current_row.lease_item) {
		return;
	}
	
	let current_start = current_row.valid_from ? frappe.datetime.str_to_obj(current_row.valid_from) : frappe.datetime.str_to_obj(frm.doc.start_date);
	let current_end = current_row.valid_to ? frappe.datetime.str_to_obj(current_row.valid_to) : (frm.doc.end_date ? frappe.datetime.str_to_obj(frm.doc.end_date) : null);
	
	// If no end date and skip_end_date is checked, we can't validate overlaps properly
	if (!current_end && frm.doc.skip_end_date) {
		return;
	}
	
	let has_overlap = false;
	let overlap_details = null;
	
	$.each(frm.doc.lease_item || [], function(i, row) {
		// Skip the current row
		if (row.name === current_row.name) {
			return;
		}
		
		// Only check rows with the same lease_item
		if (row.lease_item !== current_row.lease_item) {
			return;
		}
		
		let row_start = row.valid_from ? frappe.datetime.str_to_obj(row.valid_from) : frappe.datetime.str_to_obj(frm.doc.start_date);
		let row_end = row.valid_to ? frappe.datetime.str_to_obj(row.valid_to) : (frm.doc.end_date ? frappe.datetime.str_to_obj(frm.doc.end_date) : null);
		
		// If either row has no end date and skip_end_date is checked, skip validation
		if ((!row_end || !current_end) && frm.doc.skip_end_date) {
			return;
		}
		
		// Check for overlap
		// Overlap occurs if: start1 <= end2 AND start2 <= end1
		if (current_start <= row_end && row_start <= current_end) {
			has_overlap = true;
			overlap_details = {
				item: row.lease_item,
				existing_from: row.valid_from || frm.doc.start_date,
				existing_to: row.valid_to || frm.doc.end_date,
				current_from: current_row.valid_from || frm.doc.start_date,
				current_to: current_row.valid_to || frm.doc.end_date
			};
			return false; // Break the loop
		}
	});
	
	if (has_overlap) {
		frappe.msgprint({
			title: __('Date Overlap Detected'),
			indicator: 'red',
			message: __('Lease item <b>{0}</b> has overlapping dates:<br><br>' +
				'Existing: {1} to {2}<br>' +
				'Current: {3} to {4}<br><br>' +
				'Please adjust the dates to avoid overlap.',
				[overlap_details.item, overlap_details.existing_from, overlap_details.existing_to,
				 overlap_details.current_from, overlap_details.current_to])
		});
		frappe.validated = false;
	}
}

// Comprehensive validation before save
function validate_lease_items(frm) {
	if (!frm.doc.lease_item || frm.doc.lease_item.length === 0) {
		return;
	}
	
	let lease_start = frappe.datetime.str_to_obj(frm.doc.start_date);
	let lease_end = frm.doc.end_date && !frm.doc.skip_end_date ? frappe.datetime.str_to_obj(frm.doc.end_date) : null;
	
	// Build a map of lease items with their date ranges
	let item_ranges = {};
	
	$.each(frm.doc.lease_item || [], function(i, row) {
		// Validate individual dates
		if (row.valid_from) {
			let valid_from = frappe.datetime.str_to_obj(row.valid_from);
			
			if (valid_from < lease_start) {
				frappe.msgprint({
					title: __('Invalid Date'),
					indicator: 'red',
					message: __('Row {0}: Valid From date cannot be before Lease Start Date ({1})', [row.idx, frm.doc.start_date])
				});
				frappe.validated = false;
				return false;
			}
			
			if (lease_end && valid_from > lease_end) {
				frappe.msgprint({
					title: __('Invalid Date'),
					indicator: 'red',
					message: __('Row {0}: Valid From date cannot be after Lease End Date ({1})', [row.idx, frm.doc.end_date])
				});
				frappe.validated = false;
				return false;
			}
		}
		
		if (row.valid_to) {
			let valid_to = frappe.datetime.str_to_obj(row.valid_to);
			
			if (valid_to < lease_start) {
				frappe.msgprint({
					title: __('Invalid Date'),
					indicator: 'red',
					message: __('Row {0}: Valid To date cannot be before Lease Start Date ({1})', [row.idx, frm.doc.start_date])
				});
				frappe.validated = false;
				return false;
			}
			
			if (lease_end && valid_to > lease_end) {
				frappe.msgprint({
					title: __('Invalid Date'),
					indicator: 'red',
					message: __('Row {0}: Valid To date cannot be after Lease End Date ({1})', [row.idx, frm.doc.end_date])
				});
				frappe.validated = false;
				return false;
			}
		}
		
		if (row.valid_from && row.valid_to) {
			let valid_from = frappe.datetime.str_to_obj(row.valid_from);
			let valid_to = frappe.datetime.str_to_obj(row.valid_to);
			
			if (valid_from > valid_to) {
				frappe.msgprint({
					title: __('Invalid Date Range'),
					indicator: 'red',
					message: __('Row {0}: Valid From date cannot be after Valid To date', [row.idx])
				});
				frappe.validated = false;
				return false;
			}
		}
		
		// Build date ranges for overlap checking
		if (row.lease_item) {
			let item_start = row.valid_from ? frappe.datetime.str_to_obj(row.valid_from) : lease_start;
			let item_end = row.valid_to ? frappe.datetime.str_to_obj(row.valid_to) : lease_end;
			
			// Only check if we have valid dates
			if (item_start && item_end) {
				if (!item_ranges[row.lease_item]) {
					item_ranges[row.lease_item] = [];
				}
				
				item_ranges[row.lease_item].push({
					idx: row.idx,
					start: item_start,
					end: item_end,
					valid_from: row.valid_from || frm.doc.start_date,
					valid_to: row.valid_to || frm.doc.end_date
				});
			}
		}
	});
	
	// Check for overlaps
	for (let item_name in item_ranges) {
		let ranges = item_ranges[item_name];
		
		// Sort by start date
		ranges.sort((a, b) => a.start - b.start);
		
		// Check each pair for overlap
		for (let i = 0; i < ranges.length - 1; i++) {
			for (let j = i + 1; j < ranges.length; j++) {
				let range1 = ranges[i];
				let range2 = ranges[j];
				
				// Check if they overlap: start1 <= end2 AND start2 <= end1
				if (range1.start <= range2.end && range2.start <= range1.end) {
					frappe.msgprint({
						title: __('Date Overlap Detected'),
						indicator: 'red',
						message: __('Lease item <b>{0}</b> has overlapping dates:<br><br>' +
							'Row {1}: {2} to {3}<br>' +
							'Row {4}: {5} to {6}<br><br>' +
							'Please adjust the dates to avoid overlap.',
							[item_name, range1.idx, range1.valid_from, range1.valid_to,
							 range2.idx, range2.valid_from, range2.valid_to])
					});
					frappe.validated = false;
					return false;
				}
			}
		}
	}
}

var make_lease_invoice_schedule = function(frm){
	var doc = frm.doc;
	frappe.call({
		method: 		"propms.property_management_solution.doctype.lease.lease.make_lease_invoice_schedule",
		args: {leasedoc: doc.name},
		callback: function(){
			cur_frm.reload_doc();
		}
	});
};

var generate_pending_invoice = function(){
	frappe.call({
		method: "propms.lease_invoice.leaseInvoiceAutoCreate",
		args: {},
		callback: function(){
			cur_frm.reload_doc();
		}
	});
};

var getAllLease = function(){
	frappe.confirm(
		'Are you sure to initiate this long process?',
		function(){
			frappe.call({
				method: "propms.property_management_solution.doctype.lease.lease.getAllLease",
				args: {},
				callback: function(){
					cur_frm.reload_doc();
				}
			});
		},
		function(){
			frappe.msgprint(__("Closed before starting long process!"));
			window.close();
		}
	)
};