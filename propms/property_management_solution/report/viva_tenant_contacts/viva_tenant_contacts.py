from propms.property_management_solution.report.viva_report_data import (
	column,
	commercial_properties,
	commercial_status,
	load_rows,
	residential_properties,
	residential_status,
)


def execute(filters=None):
	filters = filters or {}
	portfolio = filters.get("portfolio") or "Commercial"
	rows = load_rows()
	if portfolio == "Commercial":
		source = commercial_properties(rows)
		data = [_commercial_row(index, row) for index, row in enumerate(source, start=1)]
		return _commercial_columns(), data, "Emails and phones are taken from the lease customer's contacts.", None

	source = [row for row in residential_properties(rows) if row.portfolio == portfolio]
	data = [_residential_row(index, row, portfolio) for index, row in enumerate(source, start=1)]
	return _residential_columns(portfolio), data, "Emails and phones are taken from the owner and tenant customer contacts.", None


def _commercial_row(index, row):
	status = commercial_status(row)
	tenant = row.lease_customer
	if status == "Leased-Internal" and not tenant:
		tenant = row.unit_owner
	if status == "Empty":
		tenant = "EMPTY"
	return {
		"sn": index,
		"property": row.name,
		"property_group": row.commercial_group,
		"tenant": None if tenant == "EMPTY" else tenant,
		"tenant_label": tenant,
		"email": None if status == "Empty" else row.tenant_email,
		"phone": None if status == "Empty" else row.tenant_phone,
	}


def _residential_row(index, row, portfolio):
	tenant = row.lease_customer or "EMPTY"
	record = {
		"sn": index,
		"property": row.name,
		"unit_owner": row.unit_owner,
		"bedroom": row.bedroom,
		"tenant": None if tenant == "EMPTY" else tenant,
		"tenant_label": tenant,
		"occupancy_status": residential_status(row),
		"email": row.tenant_email,
		"phone": row.tenant_phone,
		"owner_email": row.owner_email,
	}
	if portfolio != "Investor":
		record["email"] = row.tenant_email if tenant != "EMPTY" else None
		record["phone"] = row.tenant_phone if tenant != "EMPTY" else None
	return record


def _commercial_columns():
	return [
		column("sn", "SN", "Int", width=60),
		column("property_group", "Type", width=130),
		column("property", "Office Number", "Link", "Property", 180),
		column("tenant_label", "Tenant", width=240),
		column("email", "Email", width=280),
		column("phone", "Phone Number", width=180),
	]


def _residential_columns(portfolio):
	columns = [
		column("sn", "SN", "Int", width=60),
		column("property", "Apartment Number", "Link", "Property", 220),
		column("unit_owner", "Property Owner", "Link", "Customer", 240),
		column("bedroom", "BHK", "Int", width=70),
		column("tenant_label", "Tenant Name", width=220),
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
