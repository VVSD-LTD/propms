"""Shared data for the VIVA occupancy reports.

Rows come from Property plus the current Active lease. Status labels follow the
requested workbooks: commercial rented status, and residential status by owner
portfolio (Virgin Plaza, Vasta, RedCross, Investor).
"""

import frappe

MANAGED_OWNERS = {
	"Virgin Plaza": ("Virgin Plaza Limited", "Virgin Plaza Limited (USD)"),
	"Vasta": ("Vasta Properties Limited",),
	"RedCross": ("RedCross",),
}

PORTFOLIO_ORDER = ("Virgin Plaza", "Vasta", "RedCross", "Investor")
COMMERCIAL_GROUPS = ("Warehouse", "Ground Floor", "First Floor", "Second Floor", "Other")
COMMERCIAL_STATUSES = ("Leased-Customer", "Leased-Internal", "Empty")

PIE_COLORS = [
	"#2490ef",
	"#29cd42",
	"#f5a623",
	"#e24c4c",
	"#8e5cf6",
	"#13c2c2",
	"#f07c00",
	"#6c757d",
	"#d63384",
]

_NAME_STOP = {
	"LTD",
	"LIMITED",
	"USD",
	"MR",
	"MRS",
	"MS",
	"AND",
	"THE",
	"CO",
	"COMPANY",
	"PLC",
	"PRIVATE",
	"PUBLIC",
	"TZS",
	"TSHS",
}


def column(fieldname, label, fieldtype="Data", options=None, width=140):
	col = {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": fieldtype,
		"width": width,
	}
	if options:
		col["options"] = options
	# An empty first cell is treated as numeric and the whole column is pushed right.
	# Text, links, and dates stay left to right, same as the other report columns.
	if fieldtype not in ("Int", "Float", "Currency", "Percent"):
		col["align"] = "left"
	return col


def pie_chart(title, labels, values):
	return {
		"title": title,
		"data": {
			"labels": labels,
			"datasets": [{"name": "Properties", "values": values}],
		},
		"type": "pie",
		"height": 300,
		"colors": PIE_COLORS[: len(labels)],
	}


def bar_chart(title, labels, datasets):
	return {
		"title": title,
		"data": {"labels": labels, "datasets": datasets},
		"type": "bar",
		"height": 300,
		"colors": PIE_COLORS[: len(datasets)],
		"barOptions": {"stacked": 0, "spaceRatio": 0.4},
	}


def summary_cards(pairs):
	indicators = ["Blue", "Green", "Orange", "Red", "Grey"]
	cards = []
	for idx, (label, value) in enumerate(pairs):
		cards.append(
			{
				"label": label,
				"value": value,
				"datatype": "Int",
				"indicator": indicators[idx % len(indicators)],
			}
		)
	return cards


def status_shares(counts, order):
	"""Status, count, and percentage. The last occupied status absorbs rounding so the shares add to 100."""
	labels = list(order)
	labels.extend(label for label in counts if label not in labels)
	total = sum(counts.get(label, 0) for label in labels)
	occupied = [label for label in labels if counts.get(label)]
	last_occupied = occupied[-1] if occupied else None
	shares = []
	running = 0.0
	for label in labels:
		count = counts.get(label, 0)
		if not total or not count:
			share = 0
		elif label == last_occupied:
			share = round(100 - running, 2)
		else:
			share = round(count * 100.0 / total, 2)
			running += share
		shares.append((label, count, share))
	return total, shares


def percent_cards(total_label, counts, order):
	"""Count cards only. The share of each status is drawn on the pie."""
	labels = list(order)
	labels.extend(label for label in counts if label not in labels)
	total = sum(counts.get(label, 0) for label in labels)
	cards = [
		{"label": total_label, "value": total, "datatype": "Int", "indicator": "Blue"},
	]
	indicators = ["Green", "Orange", "Red", "Purple", "Grey", "Blue"]
	for idx, label in enumerate(labels):
		cards.append(
			{
				"label": label,
				"value": counts.get(label, 0),
				"datatype": "Int",
				"indicator": indicators[idx % len(indicators)],
			}
		)
	return cards


def filter_values(filters, fieldname):
	value = (filters or {}).get(fieldname)
	if value in (None, "", []):
		return []
	if isinstance(value, str):
		text = value.strip()
		if text.startswith("["):
			parsed = frappe.parse_json(text)
			value = parsed if isinstance(parsed, list) else [text]
		else:
			value = [text]
	return [item for item in value if item not in (None, "")]


def keep_row(row, filters, checks):
	filters = filters or {}
	for check in checks:
		kind = check[0]
		if kind == "multi":
			chosen = {str(item) for item in filter_values(filters, check[1])}
			if chosen and str(row.get(check[2]) if row.get(check[2]) is not None else "") not in chosen:
				return False
		elif kind == "exact":
			value = filters.get(check[1])
			if value and str(row.get(check[2]) or "") != str(value):
				return False
		elif kind == "like":
			query = str(filters.get(check[1]) or "").strip().lower()
			if query and query not in str(row.get(check[2]) or "").lower():
				return False
		elif kind == "date":
			chosen = filters.get(check[1])
			if not chosen:
				continue
			row_value = row.get(check[2])
			if not row_value or frappe.utils.getdate(row_value) != frappe.utils.getdate(chosen):
				return False
	return True


def number_rows(rows):
	numbered = []
	for index, row in enumerate(rows, start=1):
		copy = dict(row)
		copy["sn"] = index
		numbered.append(copy)
	return numbered


def load_rows():
	properties = frappe.db.sql(
		"""
		SELECT
			name, type, floor_name, unit_owner, status, marketing_status,
			builtup_area, carpet_area, bedroom, company, remarks, is_group
		FROM `tabProperty`
		WHERE IFNULL(status, '') != 'Removed'
			AND IFNULL(marketing_status, '') != 'Removed'
			AND IFNULL(is_group, 0) = 0
		""",
		as_dict=True,
	)
	leases = frappe.db.sql(
		"""
		SELECT
			l.property,
			l.name AS lease_name,
			l.lease_customer,
			l.start_date,
			l.end_date,
			l.builtup_area AS lease_builtup,
			l.carpet_area AS lease_carpet,
			l.property_user
		FROM `tabLease` l
		WHERE l.lease_status = 'Active'
			AND l.name = (
				SELECT ml.name
				FROM `tabLease` ml
				WHERE ml.property = l.property
					AND ml.lease_status = 'Active'
				ORDER BY ml.start_date DESC, ml.creation DESC
				LIMIT 1
			)
		""",
		as_dict=True,
	)
	lease_by_property = {row.property: row for row in leases}
	contacts = _customer_contacts()
	user_contacts = _property_user_contacts([row.property_user for row in leases if row.property_user])

	prepared = []
	for prop in properties:
		lease = lease_by_property.get(prop.name)
		customer = (lease.lease_customer if lease else None) or ""
		prop.lease_name = lease.lease_name if lease else None
		prop.lease_customer = customer or None
		prop.lease_start_date = lease.start_date if lease else None
		prop.lease_end_date = lease.end_date if lease else None
		prop.lease_builtup = lease.lease_builtup if lease else None
		prop.lease_carpet = lease.lease_carpet if lease else None
		prop.owner_email, prop.owner_phone = contacts.get(prop.unit_owner, ("", ""))
		tenant_email, tenant_phone = contacts.get(customer, ("", ""))
		if lease and lease.property_user and lease.property_user in user_contacts:
			extra_email, extra_phone = user_contacts[lease.property_user]
			tenant_email = _join_unique(tenant_email, extra_email)
			tenant_phone = _join_unique(tenant_phone, extra_phone)
		prop.tenant_email = tenant_email
		prop.tenant_phone = tenant_phone
		prop.portfolio = portfolio_of(prop.unit_owner)
		prop.commercial_group = commercial_group(prop)
		prepared.append(prop)
	return prepared


def commercial_properties(rows):
	kept = [row for row in rows if _is_commercial_unit(row)]
	kept.sort(key=lambda row: (COMMERCIAL_GROUPS.index(row.commercial_group), row.name or ""))
	return kept


def residential_properties(rows):
	kept = [row for row in rows if row.type == "Residential"]
	kept.sort(key=lambda row: ((row.name or "").upper()))
	return kept


def commercial_status(prop):
	customer = prop.lease_customer
	if customer:
		if _is_internal_customer(customer):
			return "Leased-Internal"
		return "Leased-Customer"
	if (prop.status or "") == "Common Area (Not for lease)":
		return "Leased-Internal"
	return "Empty"


def residential_status(prop):
	customer = prop.lease_customer
	if prop.portfolio == "Investor":
		if not customer:
			return "Ownership Empty"
		if same_party(customer, prop.unit_owner):
			return "Ownership-Self Staying"
		return "Ownership-Leased"
	if not customer or _is_internal_customer(customer):
		return {
			"Virgin Plaza": "Viva Empty",
			"Vasta": "Vasta Empty",
			"RedCross": "RedCross Empty",
		}.get(prop.portfolio, "Empty")
	return {
		"Virgin Plaza": "Viva-Leased -Customer",
		"Vasta": "Vasta-Leased- Customer",
		"RedCross": "RedCross-Leased- Customer",
	}.get(prop.portfolio, "Leased-Customer")


def portfolio_of(owner):
	owner = owner or ""
	for portfolio, names in MANAGED_OWNERS.items():
		if owner in names:
			return portfolio
	lower = owner.lower()
	if "virgin plaza" in lower:
		return "Virgin Plaza"
	if lower.startswith("vasta properties"):
		return "Vasta"
	if "redcross" in lower.replace(" ", "") or "red cross" in lower:
		return "RedCross"
	return "Investor"


def commercial_group(prop):
	floor = (prop.floor_name or "").upper()
	name = (prop.name or "").upper()
	if name.startswith("W") or (floor == "BASEMENT" and prop.type == "Warehouse"):
		return "Warehouse"
	if floor == "GROUND FLOOR" or name.startswith("G") or name.startswith("R"):
		return "Ground Floor"
	if floor == "1ST FLOOR" or name.startswith("1."):
		return "First Floor"
	if floor == "2ND FLOOR" or name.startswith("2."):
		return "Second Floor"
	return "Other"


def same_party(left, right):
	left_tokens = _tokens(left)
	right_tokens = _tokens(right)
	if not left_tokens or not right_tokens:
		return False
	left_set, right_set = set(left_tokens), set(right_tokens)
	if left_set == right_set:
		return True
	if left_set <= right_set or right_set <= left_set:
		return min(len(left_set), len(right_set)) >= 2 or (
			len(left_set) == 1 and len(right_set) == 1
		)
	shared = left_set & right_set
	return len(shared) >= 2 and (len(shared) / min(len(left_set), len(right_set))) >= 0.6


def count_by(rows, key):
	counts = {}
	for row in rows:
		label = key(row) if callable(key) else row.get(key)
		counts[label] = counts.get(label, 0) + 1
	return counts


def _is_commercial_unit(prop):
	if prop.type not in ("Commercial", "Warehouse"):
		return False
	name = (prop.name or "").upper()
	if any(token in name for token in ("PARKING", "PAKRING", "CCTV", "STORAGE")):
		return False
	if name.startswith("ALL PARKING"):
		return False
	if (prop.status or "") == "Common Area (Not for lease)":
		# Offices and warehouses VIVA occupies itself are part of the occupancy list.
		return name.startswith("W") or name in {"2.01", "2.05", "2.10"}
	return True


def _is_internal_customer(customer):
	text = (customer or "").strip().lower()
	return "virgin plaza" in text or "viva towers" in text


def _tokens(value):
	text = (value or "").upper().replace("&", " AND ")
	cleaned = "".join(ch if ch.isalnum() else " " for ch in text)
	return [part for part in cleaned.split() if part not in _NAME_STOP and len(part) > 1]


def _customer_contacts():
	rows = frappe.db.sql(
		"""
		SELECT dl.link_name AS customer, c.email_id, c.mobile_no, c.phone
		FROM `tabContact` c
		INNER JOIN `tabDynamic Link` dl
			ON dl.parent = c.name
			AND dl.parenttype = 'Contact'
			AND dl.link_doctype = 'Customer'
		WHERE IFNULL(dl.link_name, '') != ''
		""",
		as_dict=True,
	)
	child_emails = frappe.db.sql(
		"""
		SELECT dl.link_name AS customer, ce.email_id
		FROM `tabContact Email` ce
		INNER JOIN `tabContact` c ON c.name = ce.parent
		INNER JOIN `tabDynamic Link` dl
			ON dl.parent = c.name
			AND dl.parenttype = 'Contact'
			AND dl.link_doctype = 'Customer'
		WHERE IFNULL(ce.email_id, '') != ''
			AND IFNULL(dl.link_name, '') != ''
		""",
		as_dict=True,
	)
	child_phones = frappe.db.sql(
		"""
		SELECT dl.link_name AS customer, cp.phone
		FROM `tabContact Phone` cp
		INNER JOIN `tabContact` c ON c.name = cp.parent
		INNER JOIN `tabDynamic Link` dl
			ON dl.parent = c.name
			AND dl.parenttype = 'Contact'
			AND dl.link_doctype = 'Customer'
		WHERE IFNULL(cp.phone, '') != ''
			AND IFNULL(dl.link_name, '') != ''
		""",
		as_dict=True,
	)
	addresses = frappe.db.sql(
		"""
		SELECT dl.link_name AS customer, a.email_id, a.phone
		FROM `tabAddress` a
		INNER JOIN `tabDynamic Link` dl
			ON dl.parent = a.name
			AND dl.parenttype = 'Address'
			AND dl.link_doctype = 'Customer'
		WHERE IFNULL(dl.link_name, '') != ''
		""",
		as_dict=True,
	)
	customers = frappe.db.sql(
		"""
		SELECT name AS customer, email_id, mobile_no
		FROM `tabCustomer`
		""",
		as_dict=True,
	)
	bucket = {}

	def add(customer, email=None, phone=None):
		if not customer:
			return
		emails, phones = bucket.setdefault(customer, (set(), set()))
		email = _clean_contact(email)
		phone = _clean_contact(phone)
		if email:
			emails.add(email)
		if phone:
			phones.add(phone)

	for row in customers:
		add(row.customer, row.email_id, row.mobile_no)
	for row in addresses:
		add(row.customer, row.email_id, row.phone)
	for row in rows:
		add(row.customer, row.email_id, row.mobile_no)
		add(row.customer, None, row.phone)
	for row in child_emails:
		add(row.customer, row.email_id, None)
	for row in child_phones:
		add(row.customer, None, row.phone)
	return {customer: (_slash(emails), _slash(phones)) for customer, (emails, phones) in bucket.items()}


def _property_user_contacts(contact_names):
	names = [name for name in contact_names if name]
	if not names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT name, email_id, mobile_no, phone
		FROM `tabContact`
		WHERE name IN %(names)s
		""",
		{"names": names},
		as_dict=True,
	)
	extra_emails = frappe.db.sql(
		"""
		SELECT parent AS name, email_id
		FROM `tabContact Email`
		WHERE parent IN %(names)s AND IFNULL(email_id, '') != ''
		""",
		{"names": names},
		as_dict=True,
	)
	extra_phones = frappe.db.sql(
		"""
		SELECT parent AS name, phone
		FROM `tabContact Phone`
		WHERE parent IN %(names)s AND IFNULL(phone, '') != ''
		""",
		{"names": names},
		as_dict=True,
	)
	bucket = {name: (set(), set()) for name in names}
	for row in rows:
		_add_pair(bucket, row.name, row.email_id, row.mobile_no)
		_add_pair(bucket, row.name, None, row.phone)
	for row in extra_emails:
		_add_pair(bucket, row.name, row.email_id, None)
	for row in extra_phones:
		_add_pair(bucket, row.name, None, row.phone)
	return {name: (_slash(emails), _slash(phones)) for name, (emails, phones) in bucket.items()}


def _add_pair(bucket, name, email, phone):
	if name not in bucket:
		return
	emails, phones = bucket[name]
	email = _clean_contact(email)
	phone = _clean_contact(phone)
	if email:
		emails.add(email)
	if phone:
		phones.add(phone)


def _clean_contact(value):
	return " ".join(str(value or "").replace("\xa0", " ").split())


def _slash(values):
	return " / ".join(sorted(values))


def _join_unique(left, right):
	parts = []
	for value in (left, right):
		for piece in (value or "").split("/"):
			piece = piece.strip()
			if piece and piece not in parts:
				parts.append(piece)
	return " / ".join(parts)
