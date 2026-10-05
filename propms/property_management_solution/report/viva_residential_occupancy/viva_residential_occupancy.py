from propms.property_management_solution.report.viva_report_data import (
	column,
	count_by,
	load_rows,
	pie_chart,
	residential_properties,
	residential_status,
	summary_cards,
)

def execute(filters=None):
	filters = filters or {}
	portfolio = filters.get("portfolio") or "Entire Building"
	rows = residential_properties(load_rows())
	if portfolio != "Entire Building":
		rows = [row for row in rows if row.portfolio == portfolio]

	data = []
	for index, row in enumerate(rows, start=1):
		data.append(
			{
				"sn": index,
				"property": row.name,
				"unit_owner": row.unit_owner,
				"bedroom": row.bedroom,
				"portfolio": row.portfolio,
				"occupancy_status": residential_status(row),
				"lease_customer": row.lease_customer,
				"lease_name": row.lease_name,
				"lease_start_date": row.lease_start_date,
				"lease_end_date": row.lease_end_date,
			}
		)

	counts = count_by(data, "occupancy_status")
	labels = list(counts.keys())
	labels.sort(key=lambda label: (-counts[label], label))
	chart = pie_chart(f"Residential Status — {portfolio}", labels, [counts[label] for label in labels])
	cards = summary_cards([("Apartments", len(data))] + [(label, counts[label]) for label in labels])
	message = (
		"Residential apartments by owner portfolio. "
		"Investor units occupied by the owner are Ownership-Self Staying. "
		"A managed unit with no Active lease is Empty."
	)
	return _columns(), data, message, chart, cards


def _columns():
	return [
		column("sn", "SN", "Int", width=60),
		column("property", "Property", "Link", "Property", 220),
		column("unit_owner", "Property Owner", "Link", "Customer", 240),
		column("bedroom", "BHK", "Int", width=70),
		column("portfolio", "Portfolio", width=120),
		column("occupancy_status", "Status", width=190),
		column("lease_customer", "Lease Customer", "Link", "Customer", 220),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]
