# Amenity Booking Request + Auto-Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `Amenity Booking Request` as the always-created tenant intent, Amenity `auto_approval` (replacing `requires_approval`), staff approve → Confirmed Booking, Open Requests block slots, and an auto-filled Series child table of bookings for Desk visibility.

**Architecture:** Tenant APIs insert Request first. If `Amenity.auto_approval`, immediately create Confirmed `Amenity Booking` and mark Request Approved. Otherwise Request stays Open until staff approve/reject. Overlap treats Open Requests + Confirmed Bookings as busy. Series gets read-only child rows synced when bookings are created/updated. Mobile wrappers keep names; response adds `request_id` / `booking_id` / `auto_approved`.

**Tech Stack:** Frappe DocTypes + whitelist APIs, existing amenity overlap/notify/lifecycle patterns, `bench run-tests`, site `dev15-viva2.vvsdtz.com`.

**Spec:** `docs/superpowers/specs/2026-10-05-amenity-booking-request-auto-approval-design.md`

**Commits:** Only when the user explicitly asks — do not commit by default.

---

## File map

| File | Responsibility |
|------|----------------|
| `propms/property_management_solution/doctype/amenity/amenity.json` | Replace `requires_approval` with `auto_approval` (`depends_on: requires_booking`) |
| `propms/property_management_solution/doctype/amenity_booking_request/` | **Create** Request DocType |
| `propms/property_management_solution/doctype/amenity_booking_series_item/` | **Create** child DocType |
| `propms/property_management_solution/doctype/amenity_booking_series/amenity_booking_series.json` | Add `bookings` Table field |
| `propms/patches/v15_0/migrate_amenity_auto_approval.py` | **Create** data migrate `requires_approval` → `auto_approval` |
| `propms/patches/v15_0/migrate_pending_bookings_to_requests.py` | **Create** Pending Booking → Open Request |
| `propms/patches/v15_0/backfill_series_booking_items.py` | **Create** child rows from `Amenity Booking.series` |
| `propms/patches.txt` | Register patches |
| `propms/api/v1/amenities/doctypes.py` | Add `AMENITY_BOOKING_REQUEST` constant |
| `propms/api/v1/amenities/overlap.py` | Busy set includes Open Requests |
| `propms/api/v1/amenities/test_overlap.py` | Extend / add request-busy tests if needed |
| `propms/api/v1/amenities/request.py` | **Create** create/approve/reject/cancel Request + auto Booking |
| `propms/api/v1/amenities/booking.py` | Thin: delegate create to request flow OR keep helpers for insert Booking only |
| `propms/api/v1/amenities/staff_actions.py` | Approve/reject operate on Request ids |
| `propms/api/v1/amenities/user_bookings.py` | List/cancel Requests + Bookings |
| `propms/api/v1/amenities/lifecycle.py` | Expire stale Open Requests |
| `propms/api/v1/amenities/series.py` | Series create → Requests; approve series → Bookings; sync child table |
| `propms/api/v1/amenities/series_items.py` | **Create** sync helpers for Series Item child |
| `propms/api/v1/amenities/list.py` | Expose `auto_approval` instead of `requires_approval` |
| `propms/api/v1/amenities/notify.py` | Request pending / approved / rejected events |
| `propms/api/v1/amenities/__init__.py` | Exports |
| `propms/api/amenities.py` / `propms/api/mobile.py` | Thin wrappers; create response shape |
| `propms/hooks.py` | Scheduler entry for expire Open Requests if needed |
| `docs/flutter_amenity_booking_request_prompt.md` | **Create** Flutter handoff (`git add -f`; `docs/` is gitignored) |

---

### Task 1: Amenity field `auto_approval` + migrate patch

**Files:**
- Modify: `propms/property_management_solution/doctype/amenity/amenity.json`
- Create: `propms/patches/v15_0/migrate_amenity_auto_approval.py`
- Modify: `propms/patches.txt`

- [ ] **Step 1: Update Amenity JSON**

In `field_order`, replace `requires_approval` with `auto_approval`.

Replace the `requires_approval` field definition with:

```json
{
  "default": "0",
  "depends_on": "eval:doc.requires_booking",
  "description": "When checked, tenant Requests are approved automatically and a Confirmed Booking is created. When unchecked, staff must approve the Request before a Booking is created.",
  "fieldname": "auto_approval",
  "fieldtype": "Check",
  "label": "Auto-Approval"
}
```

Also update `viva_amenity/viva_amenity.json` if it is still synced on this site (keep Amenity canonical).

- [ ] **Step 2: Write migrate patch**

```python
# propms/patches/v15_0/migrate_amenity_auto_approval.py
import frappe

def execute():
	if not frappe.db.exists("DocType", "Amenity"):
		return
	# Ensure column exists after migrate reload
	if not frappe.db.has_column("Amenity", "auto_approval"):
		frappe.reload_doc("Property Management Solution", "doctype", "amenity")
	if frappe.db.has_column("Amenity", "requires_approval"):
		frappe.db.sql(
			"""
			UPDATE `tabAmenity`
			SET auto_approval = CASE
				WHEN IFNULL(requires_approval, 0) = 0 THEN 1
				ELSE 0
			END
			"""
		)
	# Drop old column if still present after JSON reload
	if frappe.db.has_column("Amenity", "requires_approval"):
		frappe.db.sql("ALTER TABLE `tabAmenity` DROP COLUMN `requires_approval`")
```

Append to `propms/patches.txt`:
`propms.patches.v15_0.migrate_amenity_auto_approval`

- [ ] **Step 3: Migrate site and smoke**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com migrate
bench --site dev15-viva2.vvsdtz.com mariadb -e "SHOW COLUMNS FROM \`tabAmenity\` LIKE '%approval%'; SELECT name, requires_booking, auto_approval FROM \`tabAmenity\` LIMIT 10;"
```

Expected: column `auto_approval` present; `requires_approval` gone; amenities that previously did not require approval have `auto_approval=1`.

---

### Task 2: DocType `Amenity Booking Request`

**Files:**
- Create: `propms/property_management_solution/doctype/amenity_booking_request/amenity_booking_request.json`
- Create: `propms/property_management_solution/doctype/amenity_booking_request/amenity_booking_request.py`
- Create: `propms/property_management_solution/doctype/amenity_booking_request/__init__.py`
- Modify: `propms/api/v1/amenities/doctypes.py`

- [ ] **Step 1: Create DocType JSON**

Key settings:
- `autoname`: `VABR-.YYYY.-.#####`
- `module`: Property Management Solution
- Status options: `Open\nApproved\nRejected\nCancelled\nExpired`
- Fields: amenity, booking_date, start_time, end_time, status, tenant, tenant_name, property_unit, lease, guests_count, notes, series (Link Amenity Booking Series), booking (Link Amenity Booking), approved_by, approved_on, rejection_reason

Permissions: System Manager + Property Manager (read/write); tenant access via API `ignore_permissions` as today.

- [ ] **Step 2: Controller stub**

```python
# amenity_booking_request.py
import frappe
from frappe.model.document import Document

class AmenityBookingRequest(Document):
	pass
```

- [ ] **Step 3: Constant**

In `doctypes.py`:
```python
AMENITY_BOOKING_REQUEST = "Amenity Booking Request"
```

- [ ] **Step 4: migrate + verify DocType exists**

```bash
bench --site dev15-viva2.vvsdtz.com migrate
bench --site dev15-viva2.vvsdtz.com console <<'PY'
import frappe
print(frappe.db.exists("DocType", "Amenity Booking Request"))
PY
```

Expected: DocType name printed / truthy.

---

### Task 3: Series child table DocType + Series JSON

**Files:**
- Create: `propms/property_management_solution/doctype/amenity_booking_series_item/amenity_booking_series_item.json`
- Create: `propms/property_management_solution/doctype/amenity_booking_series_item/amenity_booking_series_item.py`
- Modify: `propms/property_management_solution/doctype/amenity_booking_series/amenity_booking_series.json`
- Create: `propms/api/v1/amenities/series_items.py`

- [ ] **Step 1: Child DocType** (`istable`: 1)

Fields: `booking` (Link Amenity Booking, reqd, in_list_view), `booking_date` (Date, read_only), `start_time`, `end_time`, `status` (Data, read_only).

- [ ] **Step 2: Parent field on Series**

Add to `field_order` near end: `bookings_section`, `bookings`.

```json
{
  "fieldname": "bookings_section",
  "fieldtype": "Section Break",
  "label": "Bookings in this Series"
},
{
  "fieldname": "bookings",
  "fieldtype": "Table",
  "label": "Bookings",
  "options": "Amenity Booking Series Item",
  "read_only": 1
}
```

- [ ] **Step 3: Sync helper**

```python
# series_items.py
import frappe

def sync_series_booking_row(booking_name):
	"""Upsert read-only child row on Amenity Booking Series from a Booking."""
	b = frappe.db.get_value(
		"Amenity Booking",
		booking_name,
		["name", "series", "booking_date", "start_time", "end_time", "status"],
		as_dict=True,
	)
	if not b or not b.series:
		return
	series = frappe.get_doc("Amenity Booking Series", b.series)
	existing = None
	for row in series.get("bookings") or []:
		if row.booking == b.name:
			existing = row
			break
	if existing:
		existing.booking_date = b.booking_date
		existing.start_time = b.start_time
		existing.end_time = b.end_time
		existing.status = b.status
	else:
		series.append(
			"bookings",
			{
				"booking": b.name,
				"booking_date": b.booking_date,
				"start_time": b.start_time,
				"end_time": b.end_time,
				"status": b.status,
			},
		)
	series.flags.ignore_permissions = True
	series.save(ignore_permissions=True)
```

Call `sync_series_booking_row` whenever a Booking with `series` is inserted or status changes.

- [ ] **Step 4: migrate**

```bash
bench --site dev15-viva2.vvsdtz.com migrate
```

---

### Task 4: Overlap — Open Requests hold the slot

**Files:**
- Modify: `propms/api/v1/amenities/overlap.py`
- Modify: `propms/api/v1/amenities/test_overlap.py` (optional DB-free unit remains; add integration-style helper test if feasible)
- Modify: `propms/api/v1/amenities/slots.py` (busy query)

- [ ] **Step 1: Extend `find_conflicting_booking`**

Rename conceptually to conflict finder that checks:

1. `Amenity Booking` status in `["Confirmed"]` (and `Pending` only during migration window if any remain)
2. `Amenity Booking Request` status `Open`

Return a dict including `"source": "booking"|"request"`.

Sketch:

```python
def find_conflicting_booking(amenity, booking_date, start_time, end_time, buffer_mins, exclude_name=None, exclude_request=None):
	import frappe
	req_end_buf = expand_end_with_buffer(end_time, buffer_mins)

	booking_rows = frappe.get_all(
		"Amenity Booking",
		filters={
			"amenity": amenity,
			"booking_date": str(booking_date),
			"status": ["in", ["Pending", "Confirmed"]],
		},
		fields=["name", "start_time", "end_time", "tenant", "tenant_name", "status"],
		ignore_permissions=True,
	)
	for r in booking_rows or []:
		if exclude_name and r.name == exclude_name:
			continue
		other_end = expand_end_with_buffer(r.end_time, buffer_mins)
		if ranges_overlap(start_time, req_end_buf, r.start_time, other_end):
			r["source"] = "booking"
			return r

	if frappe.db.exists("DocType", "Amenity Booking Request"):
		req_rows = frappe.get_all(
			"Amenity Booking Request",
			filters={
				"amenity": amenity,
				"booking_date": str(booking_date),
				"status": "Open",
			},
			fields=["name", "start_time", "end_time", "tenant", "tenant_name", "status"],
			ignore_permissions=True,
		)
		for r in req_rows or []:
			if exclude_request and r.name == exclude_request:
				continue
			other_end = expand_end_with_buffer(r.end_time, buffer_mins)
			if ranges_overlap(start_time, req_end_buf, r.start_time, other_end):
				r["source"] = "request"
				return r
	return None
```

Update `slots.py` day availability busy list similarly (Confirmed bookings + Open requests).

- [ ] **Step 2: Run existing overlap unit tests**

```bash
bench --site dev15-viva2.vvsdtz.com run-tests --app propms --module propms.api.v1.amenities.test_overlap
```

Expected: PASS (pure helpers unchanged).

---

### Task 5: Request create + auto-approve → Booking

**Files:**
- Create: `propms/api/v1/amenities/request.py`
- Modify: `propms/api/v1/amenities/booking.py`
- Modify: `propms/api/v1/amenities/__init__.py`
- Modify: `propms/api/mobile.py` / `propms/api/amenities.py`

- [ ] **Step 1: Implement `create_booking_request` in `request.py`**

Reuse validations from current `booking.create_booking` (auth, published, dates, step, overlap, audience). Then:

```python
req = frappe.get_doc({
	"doctype": "Amenity Booking Request",
	"amenity": amenity_name,
	"booking_date": target_date,
	"start_time": s_time,
	"end_time": e_time,
	"status": "Open",
	"tenant": frappe.session.user,
	# ... tenant_name, lease, property_unit, guests, notes
}).insert(ignore_permissions=True)

auto = cint(amenity_doc.auto_approval)
booking_name = None
if auto:
	booking_name = _create_confirmed_booking_from_request(req)
	req.status = "Approved"
	req.booking = booking_name
	req.approved_by = frappe.session.user
	req.approved_on = now_datetime()
	req.save(ignore_permissions=True)
	# notify booked
else:
	# notify staff pending request
	pass

frappe.db.commit()
return {
	"status": "success",
	"request_id": req.name,
	"booking_id": booking_name,
	"request_status": req.status,
	"auto_approved": bool(auto),
}
```

`_create_confirmed_booking_from_request` inserts `Amenity Booking` status Confirmed copying fields; if `req.series`, set series and call `sync_series_booking_row`.

- [ ] **Step 2: Point `create_booking` whitelist to request flow**

In `booking.py`, make `create_booking` call `create_booking_request` (keep function name for mobile).

- [ ] **Step 3: Smoke on site**

Pick one amenity with `auto_approval=1` and one with `0`. As a tenant user:

```bash
bench --site dev15-viva2.vvsdtz.com console
# create_booking for auto → request Approved + booking_id set
# create_booking for manual → request Open + booking_id None
# second overlapping Open request must fail
```

---

### Task 6: Staff approve / reject Request

**Files:**
- Modify: `propms/api/v1/amenities/staff_actions.py`
- Modify: `propms/api/v1/amenities/notify.py` as needed

- [ ] **Step 1: Change approve/reject to load Amenity Booking Request**

Accept `booking_id` **or** `request_id` for Flutter compat:

```python
target = request_id or booking_id
# if exists as Request use it; elif old Pending Booking during migration, handle
```

Approve Open Request → create Confirmed Booking → Request Approved + link → notify.  
Reject → reason required → Rejected → notify.

- [ ] **Step 2: Smoke**

Create Open Request as tenant; approve as Administrator; verify Booking exists and slot blocked by Confirmed Booking not Open Request.

---

### Task 7: List / cancel / lifecycle

**Files:**
- Modify: `propms/api/v1/amenities/user_bookings.py`
- Modify: `propms/api/v1/amenities/lifecycle.py`
- Modify: `propms/hooks.py` if new scheduler path needed

- [ ] **Step 1: `get_my_bookings`**

Return combined list with a `kind` field: `"request"` | `"booking"`. Include Open Requests and Bookings for the tenant. Keep existing keys where possible so Flutter can adapt gradually.

- [ ] **Step 2: `cancel_booking`**

If id is Open Request → status Cancelled.  
If id is Confirmed Booking → existing cancel rules.  
If id is Approved Request → cancel linked Booking if allowed.

- [ ] **Step 3: Lifecycle**

`reconcile_stale_open_amenity_requests`: Open Requests with start in the past → `Expired`. Call from read paths + hourly scheduler alongside existing pending reconciler (keep pending reconciler until migration complete).

---

### Task 8: Series Request-first + child sync

**Files:**
- Modify: `propms/api/v1/amenities/series.py`
- Modify: `propms/api/v1/amenities/series_items.py` (from Task 3)

- [ ] **Step 1: On series create**

For each planned occurrence: create Open Request (with `series` set). If amenity `auto_approval`, fulfill each Request → Booking → `sync_series_booking_row`. Update series status Confirmed vs Pending accordingly.

- [ ] **Step 2: On series approve**

Approve all Open Requests for that series (create Bookings + sync child table).

- [ ] **Step 3: Desk smoke**

Open a Series with multiple Confirmed bookings; child table lists each booking date/time/status.

---

### Task 9: Catalog field rename + data patches

**Files:**
- Modify: `propms/api/v1/amenities/list.py` — expose `auto_approval`, stop exposing `requires_approval`
- Create: `propms/patches/v15_0/migrate_pending_bookings_to_requests.py`
- Create: `propms/patches/v15_0/backfill_series_booking_items.py`
- Modify: `propms/patches.txt`

- [ ] **Step 1: Pending → Request migration**

For each `Amenity Booking` with `status=Pending`: create matching Open Request (copy fields); cancel Pending booking with reason `Migrated to Amenity Booking Request` (so only Request holds slot).

- [ ] **Step 2: Backfill series items**

For each Booking with `series` set and Confirmed/Cancelled/etc.: `sync_series_booking_row(name)`.

- [ ] **Step 3: migrate + verify counts**

```bash
bench --site dev15-viva2.vvsdtz.com migrate
```

---

### Task 10: Flutter prompt + Postman note

**Files:**
- Create: `docs/flutter_amenity_booking_request_prompt.md`  
  (`docs/` is gitignored — stage with `git add -f docs/flutter_amenity_booking_request_prompt.md`, same as sibling Flutter prompts)

Document:
- Amenity field `auto_approval` (replaces requires_approval)
- Create response: `request_id`, `booking_id`, `request_status`, `auto_approved`
- Staff approve/reject target Request ids (alias `booking_id` param still accepted)
- My list may include Open requests
- Series Desk child table is backend-only for staff

---

## Spec coverage checklist

| Spec section | Task |
|--------------|------|
| Amenity `auto_approval` + migrate | Task 1 |
| Request DocType | Task 2 |
| Series child table | Task 3, 8 |
| Overlap Open Request holds slot | Task 4 |
| Create + auto Booking | Task 5 |
| Staff approve/reject | Task 6 |
| List/cancel/lifecycle | Task 7 |
| Series Request-first | Task 8 |
| Pending migration + backfill | Task 9 |
| Flutter handoff | Task 10 |

---

## Self-review notes

- No TBD placeholders in task steps.
- Naming locked: DocType `Amenity Booking Request`, autoname `VABR-.YYYY.-.#####`.
- Series approve = approve-all Open Requests for that series (bulk).
- Do not commit unless user asks.
