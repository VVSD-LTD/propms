from propms.property_management_solution.report.viva_report_data import (
	COMMERCIAL_GROUPS,
	bar_chart,
	column,
	commercial_properties,
	commercial_status,
	keep_row,
	load_rows,
)

COLUMN_FILTERS = (
	("exact", "property", "property"),
	("multi", "occupancy_status", "occupancy_status"),
	("exact", "lease_customer", "lease_customer"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)


def area_records():
	records = []
	for row in commercial_properties(load_rows()):
		status = commercial_status(row)
		records.append(
			{
				"floor": row.commercial_group,
				"property": row.name,
				"builtup_area": row.builtup_area,
				"carpet_area": row.carpet_area,
				"lease_builtup": row.lease_builtup,
				"lease_carpet": row.lease_carpet,
				"occupancy_status": status,
				"lease_customer": None if status == "Empty" else row.lease_customer or row.unit_owner,
				"lease_name": None if status == "Empty" else row.lease_name,
				"lease_start_date": None if status == "Empty" else row.lease_start_date,
				"lease_end_date": None if status == "Empty" else row.lease_end_date,
			}
		)
	return records


def execute(filters=None):
	filters = filters or {}
	floor = filters.get("floor") or "All"
	data = area_records()
	if floor != "All":
		data = [row for row in data if row["floor"] == floor]
	data = [row for row in data if keep_row(row, filters, COLUMN_FILTERS)]

	built_by_floor = []
	for group in COMMERCIAL_GROUPS:
		built_by_floor.append(
			round(sum((row["builtup_area"] or 0) for row in data if row["floor"] == group), 2)
		)
	chart = bar_chart(
		"Built Up Area by Floor",
		list(COMMERCIAL_GROUPS),
		[{"name": "Built Up Area", "values": built_by_floor}],
	)
	total_built = round(sum((row["builtup_area"] or 0) for row in data), 2)
	total_carpet = round(sum((row["carpet_area"] or 0) for row in data), 2)
	cards = [
		{"label": "Properties", "value": len(data), "datatype": "Int", "indicator": "Blue"},
		{"label": "Built Up Area", "value": total_built, "datatype": "Float", "indicator": "Green"},
		{"label": "Carpet Area", "value": total_carpet, "datatype": "Float", "indicator": "Orange"},
	]
	message = "Built-up and carpet come from the Property. Lease name and dates come from the current Active lease."
	return _columns(), data, message, chart, cards


def _columns():
	return [
		column("floor", "Floor", width=130),
		column("property", "Property", "Link", "Property", 180),
		column("builtup_area", "Built Up", "Float", width=120),
		column("carpet_area", "Carpet", "Float", width=110),
		# column("lease_builtup", "Lease Built Up", "Float", width=130),
		# column("lease_carpet", "Lease Carpet", "Float", width=120),
		column("occupancy_status", "Status", width=150),
		column("lease_customer", "Lease Customer", "Link", "Customer", 220),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]
