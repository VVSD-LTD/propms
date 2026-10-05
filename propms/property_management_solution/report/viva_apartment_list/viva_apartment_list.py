from propms.property_management_solution.report.viva_report_data import (
	column,
	load_rows,
	residential_properties,
	residential_status,
)


def execute(filters=None):
	filters = filters or {}
	view = filters.get("view") or "Tower A"
	rows = residential_properties(load_rows())
	if view == "Tower A":
		rows = [row for row in rows if (row.name or "").upper().startswith("A")]
	elif view == "Tower B":
		rows = [row for row in rows if (row.name or "").upper().startswith("B")]
	else:
		rows = [row for row in rows if row.portfolio == view]

	data = []
	for index, row in enumerate(rows, start=1):
		data.append(
			{
				"sn": index,
				"property": row.name,
				"bedroom": row.bedroom,
				"unit_owner": row.unit_owner,
				"lessee": row.lease_customer or "VACANT",
				"occupancy_status": residential_status(row),
				"portfolio": row.portfolio,
				"lease_name": row.lease_name,
				"lease_start_date": row.lease_start_date,
				"lease_end_date": row.lease_end_date,
			}
		)
	message = "Apartment list from Residential properties. Lessee is the customer on the current Active lease."
	return _columns(), data, message, None


def _columns():
	return [
		column("sn", "S/N", "Int", width=60),
		column("property", "Apt", "Link", "Property", 220),
		column("bedroom", "BHK", "Int", width=70),
		column("unit_owner", "Property Owner", "Link", "Customer", 260),
		column("lessee", "Lessee", width=240),
		column("occupancy_status", "Status", width=190),
		column("portfolio", "Portfolio", width=120),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]
