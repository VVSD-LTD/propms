from propms.property_management_solution.report.viva_report_data import (
	column,
	keep_row,
	load_rows,
	residential_properties,
	residential_status,
)

COLUMN_FILTERS = (
	("exact", "property", "property"),
	("multi", "bedroom", "bedroom"),
	("exact", "unit_owner", "unit_owner"),
	("like", "lessee", "lessee"),
	("multi", "occupancy_status", "occupancy_status"),
	("multi", "portfolio", "portfolio"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)


def apartment_records():
	records = []
	for row in residential_properties(load_rows()):
		name = row.name or ""
		records.append(
			{
				"property": name,
				"bedroom": int(row.bedroom or 0),
				"unit_owner": row.unit_owner,
				"lessee": row.lease_customer or "VACANT",
				"lease_customer": row.lease_customer,
				"occupancy_status": residential_status(row),
				"portfolio": row.portfolio,
				"tower": "Tower A" if name.upper().startswith("A") else "Tower B" if name.upper().startswith("B") else "Other",
				"lease_name": row.lease_name,
				"lease_start_date": row.lease_start_date,
				"lease_end_date": row.lease_end_date,
			}
		)
	return records


def execute(filters=None):
	filters = filters or {}
	view = filters.get("view") or "Tower A"
	data = apartment_records()
	if view == "Tower A":
		data = [row for row in data if row["tower"] == "Tower A"]
	elif view == "Tower B":
		data = [row for row in data if row["tower"] == "Tower B"]
	else:
		data = [row for row in data if row["portfolio"] == view]
	data = [row for row in data if keep_row(row, filters, COLUMN_FILTERS)]
	message = "Apartment list from Residential properties. Lessee is the customer on the current Active lease."
	return _columns(), data, message, None


def _columns():
	return [
		column("property", "Property", "Link", "Property", 220),
		column("bedroom", "BHK", "Int", width=70),
		column("unit_owner", "Property Owner", "Link", "Customer", 260),
		column("lessee", "Lessee", width=240),
		column("occupancy_status", "Status", width=190),
		column("portfolio", "Portfolio", width=120),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]
