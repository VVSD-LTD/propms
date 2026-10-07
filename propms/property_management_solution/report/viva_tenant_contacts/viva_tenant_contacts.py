from propms.property_management_solution.report.viva_report_data import (
	column,
	commercial_properties,
	commercial_status,
	keep_row,
	load_rows,
	residential_properties,
	residential_status,
)

COMMERCIAL_FILTERS = (
	("multi", "property_group", "property_group"),
	("exact", "property", "property"),
	("like", "tenant_label", "tenant_label"),
	("exact", "lease_customer", "lease_customer"),
	("like", "email", "email"),
	("like", "phone", "phone"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)
RESIDENTIAL_FILTERS = (
	("exact", "property", "property"),
	("exact", "unit_owner", "unit_owner"),
	("multi", "bedroom", "bedroom"),
	("like", "tenant_label", "tenant_label"),
	("exact", "lease_customer", "lease_customer"),
	("multi", "occupancy_status", "occupancy_status"),
	("like", "email", "email"),
	("like", "phone", "phone"),
	("like", "owner_email", "owner_email"),
	("exact", "lease_name", "lease_name"),
	("date", "lease_start_date", "lease_start_date"),
	("date", "lease_end_date", "lease_end_date"),
)


def contact_records(portfolio):
	rows = load_rows()
	if portfolio == "Commercial":
		return [_commercial_row(row) for row in commercial_properties(rows)]
	return [
		_residential_row(row)
		for row in residential_properties(rows)
		if row.portfolio == portfolio
	]


def execute(filters=None):
	filters = filters or {}
	portfolio = filters.get("portfolio") or "Commercial"
	checks = COMMERCIAL_FILTERS if portfolio == "Commercial" else RESIDENTIAL_FILTERS
	data = [row for row in contact_records(portfolio) if keep_row(row, filters, checks)]
	if portfolio == "Commercial":
		return _commercial_columns(), data, "Emails and phones are taken from the lease customer's contacts.", None
	return (
		_residential_columns(portfolio),
		data,
		"Emails and phones are taken from the owner and tenant customer contacts.",
		None,
	)


def _commercial_row(row):
	status = commercial_status(row)
	tenant = row.lease_customer
	if status == "Leased-Internal" and not tenant:
		tenant = row.unit_owner
	if status == "Empty":
		tenant = "EMPTY"
	return {
		"property": row.name,
		"property_group": row.commercial_group,
		"tenant_label": tenant,
		"lease_customer": None if tenant == "EMPTY" else tenant,
		"email": None if status == "Empty" else row.tenant_email,
		"phone": None if status == "Empty" else row.tenant_phone,
		"lease_name": None if status == "Empty" else row.lease_name,
		"lease_start_date": None if status == "Empty" else row.lease_start_date,
		"lease_end_date": None if status == "Empty" else row.lease_end_date,
	}


def _residential_row(row):
	tenant = row.lease_customer or "EMPTY"
	return {
		"property": row.name,
		"unit_owner": row.unit_owner,
		"bedroom": int(row.bedroom or 0),
		"tenant_label": tenant,
		"lease_customer": None if tenant == "EMPTY" else tenant,
		"occupancy_status": residential_status(row),
		"email": row.tenant_email if tenant != "EMPTY" else None,
		"phone": row.tenant_phone if tenant != "EMPTY" else None,
		"owner_email": row.owner_email,
		"lease_name": row.lease_name,
		"lease_start_date": row.lease_start_date,
		"lease_end_date": row.lease_end_date,
	}


def _commercial_columns():
	return [
		column("property_group", "Type", width=130),
		column("property", "Property", "Link", "Property", 180),
		column("tenant_label", "Tenant", width=240),
		column("email", "Email", width=280),
		column("phone", "Phone Number", width=180),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]


def _residential_columns(portfolio):
	columns = [
		column("property", "Property", "Link", "Property", 220),
		column("unit_owner", "Property Owner", "Link", "Customer", 240),
		column("bedroom", "BHK", "Int", width=70),
		column("tenant_label", "Tenant Name", width=220),
		column("lease_name", "Lease Name", "Link", "Lease", 160),
		column("lease_start_date", "Lease Start Date", "Date", width=130),
		column("lease_end_date", "Lease End Date", "Date", width=130),
	]
	if portfolio == "Investor":
		columns.extend(
			[
				column("owner_email", "Email (Owner)", width=240),
				column("email", "Email (Tenant)", width=240),
				column("phone", "Phone Number", width=180),
			]
		)
	else:
		columns.extend(
			[
				column("email", "Email", width=280),
				column("phone", "Phone Number", width=180),
			]
		)
	return columns
