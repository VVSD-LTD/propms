from propms.property_management_solution.report.viva_report_data import (
	COMMERCIAL_GROUPS,
	COMMERCIAL_STATUSES,
	bar_chart,
	column,
	commercial_properties,
	commercial_status,
	count_by,
	load_rows,
	pie_chart,
	summary_cards,
)


def execute(filters=None):
	filters = filters or {}
	rows = commercial_properties(load_rows())
	for row in rows:
		row.occupancy_status = commercial_status(row)
		if row.occupancy_status == "Leased-Internal" and not row.lease_customer:
			row.lease_customer = row.unit_owner

	data = []
	for index, row in enumerate(rows, start=1):
		data.append(
			{
				"sn": index,
				"property_group": row.commercial_group,
				"property": row.name,
				"unit_owner": row.unit_owner,
				"occupancy_status": row.occupancy_status,
				"lease_customer": row.lease_customer if row.occupancy_status != "Empty" else None,
				"lease_name": row.lease_name,
				"lease_start_date": row.lease_start_date,
				"lease_end_date": row.lease_end_date,
			}
		)

	counts = count_by(data, "occupancy_status")
	chart = _chart(filters, data, counts)
	cards = summary_cards(
		[("Properties", len(data))]
		+ [(status, counts.get(status, 0)) for status in COMMERCIAL_STATUSES]
	)
	message = "Commercial offices and warehouses. Status uses the current Active lease. A unit with no lease that VIVA keeps as common area is Leased-Internal."
	return _columns(), data, message, chart, cards


def _columns():
	return [
		column("sn", "SN", "Int", width=60),
		column("property_group", "Type", width=130),
		column("property", "Property", "Link", "Property", 180),
		column("unit_owner", "Property Owner", "Link", "Customer", 180),
		column("occupancy_status", "Status", width=150),
		column("lease_customer", "Lease Customer", "Link", "Customer", 220),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
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
