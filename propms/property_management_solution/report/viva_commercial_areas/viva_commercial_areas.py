from propms.property_management_solution.report.viva_report_data import (
	COMMERCIAL_GROUPS,
	bar_chart,
	column,
	commercial_properties,
	commercial_status,
	load_rows,
)


def execute(filters=None):
	filters = filters or {}
	floor = filters.get("floor") or "All"
	only_empty = filters.get("only_empty")
	rows = commercial_properties(load_rows())
	if floor != "All":
		rows = [row for row in rows if row.commercial_group == floor]
	if only_empty:
		rows = [row for row in rows if commercial_status(row) == "Empty"]

	data = []
	for index, row in enumerate(rows, start=1):
		data.append(
			{
				"sn": index,
				"floor": row.commercial_group,
				"property": row.name,
				"builtup_area": row.builtup_area,
				"carpet_area": row.carpet_area,
				"lease_builtup": row.lease_builtup,
				"lease_carpet": row.lease_carpet,
				"occupancy_status": commercial_status(row),
				"lease_customer": row.lease_customer,
			}
		)

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
	message = "Areas are the Property built-up and carpet figures. Lease columns repeat the area stored on the Active lease."
	return _columns(), data, message, chart, cards


def _columns():
	return [
		column("sn", "SN", "Int", width=60),
		column("floor", "Floor", width=130),
		column("property", "Property", "Link", "Property", 180),
		column("builtup_area", "ERP Built Up", "Float", width=120),
		column("carpet_area", "Carpet", "Float", width=110),
		column("lease_builtup", "Lease Built Up", "Float", width=130),
		column("lease_carpet", "Lease Carpet", "Float", width=120),
		column("occupancy_status", "Status", width=150),
		column("lease_customer", "Lease Customer", "Link", "Customer", 220),
	]
