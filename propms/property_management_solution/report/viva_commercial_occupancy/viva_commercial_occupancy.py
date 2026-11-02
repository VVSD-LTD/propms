from propms.property_management_solution.report.viva_report_data import (
	COMMERCIAL_GROUPS,
	COMMERCIAL_STATUSES,
	bar_chart,
	column,
	commercial_properties,
	commercial_status,
	count_by,
	keep_row,
	load_rows,
	percent_cards,
	pie_chart,
)

COLUMN_FILTERS = (
	("multi", "property_group", "property_group"),
	("exact", "property", "property"),
	("exact", "unit_owner", "unit_owner"),
	("multi", "occupancy_status", "occupancy_status"),
	("exact", "lease_customer", "lease_customer"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)


def commercial_records():
	records = []
	for row in commercial_properties(load_rows()):
		status = commercial_status(row)
		customer = row.lease_customer
		if status == "Leased-Internal" and not customer:
			customer = row.unit_owner
		if status == "Empty":
			customer = None
		records.append(
			{
				"property_group": row.commercial_group,
				"property": row.name,
				"unit_owner": row.unit_owner,
				"occupancy_status": status,
				"lease_customer": customer,
				"lease_name": None if status == "Empty" else row.lease_name,
				"lease_start_date": None if status == "Empty" else row.lease_start_date,
				"lease_end_date": None if status == "Empty" else row.lease_end_date,
			}
		)
	return records


def execute(filters=None):
	filters = filters or {}
	data = [row for row in commercial_records() if keep_row(row, filters, COLUMN_FILTERS)]
	counts = count_by(data, "occupancy_status")
	chart = _chart(filters, data, counts)
	cards = percent_cards("Properties", counts, COMMERCIAL_STATUSES)
	message = "Commercial offices and warehouses. Status uses the current Active lease. A unit with no lease that VIVA keeps as common area is Leased-Internal."
	return _columns(), data, message, chart, cards


def _columns():
	return [
		column("property_group", "Type", width=130),
		column("property", "Property", "Link", "Property", 180),
		column("unit_owner", "Property Owner", "Link", "Customer", 180),
		column("occupancy_status", "Status", width=150),
		column("lease_customer", "Lease Customer", "Link", "Customer", 280),
		column("lease_name", "Lease Name", "Link", "Lease", 280),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]


def _chart(filters, data, counts):
	if filters.get("chart_view") == "Bar":
		datasets = []
		for status in COMMERCIAL_STATUSES:
			datasets.append(
				{
					"name": status,
					"values": [
						sum(
							1
							for row in data
							if row["property_group"] == group and row["occupancy_status"] == status
						)
						for group in COMMERCIAL_GROUPS
					],
				}
			)
		return bar_chart("Commercial Space by Floor", list(COMMERCIAL_GROUPS), datasets)

	labels = [status for status in COMMERCIAL_STATUSES if counts.get(status)]
	values = [counts[status] for status in labels]
	return pie_chart("Commercial Rented Status", labels, values)
