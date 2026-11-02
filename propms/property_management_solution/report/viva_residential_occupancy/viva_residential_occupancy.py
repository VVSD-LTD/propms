from propms.property_management_solution.report.viva_report_data import (
	column,
	count_by,
	keep_row,
	load_rows,
	percent_cards,
	pie_chart,
	residential_properties,
	residential_status,
)

PORTFOLIO_STATUSES = {
	"Virgin Plaza": ("Viva-Leased -Customer", "Viva Empty"),
	"Vasta": ("Vasta-Leased- Customer", "Vasta Empty"),
	"RedCross": ("RedCross-Leased- Customer", "RedCross Empty"),
	"Investor": ("Ownership-Self Staying", "Ownership-Leased", "Ownership Empty"),
}
ALL_STATUSES = tuple(status for statuses in PORTFOLIO_STATUSES.values() for status in statuses)

COLUMN_FILTERS = (
	("exact", "property", "property"),
	("exact", "unit_owner", "unit_owner"),
	("multi", "bedroom", "bedroom"),
	("multi", "occupancy_status", "occupancy_status"),
	("exact", "lease_customer", "lease_customer"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)


def residential_records():
	return [
		{
			"property": row.name,
			"unit_owner": row.unit_owner,
			"bedroom": int(row.bedroom or 0),
			"portfolio": row.portfolio,
			"occupancy_status": residential_status(row),
			"lease_customer": row.lease_customer,
			"lease_name": row.lease_name,
			"lease_start_date": row.lease_start_date,
			"lease_end_date": row.lease_end_date,
		}
		for row in residential_properties(load_rows())
	]


def execute(filters=None):
	filters = filters or {}
	portfolio = filters.get("portfolio") or "Entire Building"
	data = residential_records()
	if portfolio != "Entire Building":
		data = [row for row in data if row["portfolio"] == portfolio]
	data = [row for row in data if keep_row(row, filters, COLUMN_FILTERS)]
	counts = count_by(data, "occupancy_status")
	order = PORTFOLIO_STATUSES.get(portfolio, ALL_STATUSES)
	labels = [label for label in order if counts.get(label)]
	chart = pie_chart(
		f"Residential Status — {portfolio}",
		labels,
		[counts[label] for label in labels],
	)
	cards = percent_cards("Apartments", counts, order)
	message = (
		"Residential apartments by owner portfolio. "
		"Investor units occupied by the owner are Ownership-Self Staying. "
		"A managed unit with no Active lease is Empty."
	)
	return _columns(), data, message, chart, cards


def _columns():
	return [
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
