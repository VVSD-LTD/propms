# Amenity Exclusive Range Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed slot-duration amenity booking with exclusive free-form start→end ranges, day availability API, per-amenity buffer/approval/cancel rules, and staff approve/reject — so Flutter can ship the hybrid busy-strip + pickers UX.

**Architecture:** Pure helpers in `overlap.py` own interval math (buffer, free gaps, step validation). `slots.py` gains `get_amenity_day_availability`; `booking.py` / `user_bookings.py` enforce exclusive create/cancel; new `staff_actions.py` for approve/reject; `lifecycle.py` auto-cancels stale Pending; DocType JSON fields drive per-amenity policy. Mobile wrappers stay thin on `propms/api/mobile.py` and `propms/api/amenities.py`.

**Tech Stack:** Frappe DocTypes + whitelist APIs, `frappe.utils` datetime, existing amenity notify (WS + FCM), unittest via `bench run-tests`.

**Spec:** `docs/superpowers/specs/2026-09-28-amenity-range-booking-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `propms/property_management_solution/doctype/viva_amenity/viva_amenity.json` | New policy fields; deprecate slot engine |
| `propms/property_management_solution/doctype/viva_amenity_booking/viva_amenity_booking.json` | Pending/Rejected + audit/reject fields |
| `propms/api/v1/amenities/overlap.py` | **Create** — exclusive overlap, buffer expand, free gaps, step snap checks |
| `propms/api/v1/amenities/test_overlap.py` | **Create** — unit tests for overlap helpers (no DB) |
| `propms/api/v1/amenities/slots.py` | Add `get_amenity_day_availability`; keep `get_available_slots` as deprecated adapter |
| `propms/api/v1/amenities/booking.py` | Exclusive create + Pending/Confirmed + validations |
| `propms/api/v1/amenities/user_bookings.py` | Cancel-before-hours policy |
| `propms/api/v1/amenities/staff_actions.py` | **Create** — approve / reject |
| `propms/api/v1/amenities/lifecycle.py` | Auto-cancel Pending past start |
| `propms/api/v1/amenities/notify.py` | Approve/reject tenant notify events |
| `propms/api/v1/amenities/list.py` | Expose new amenity fields in catalog/detail |
| `propms/api/v1/amenities/__init__.py` | Export new APIs |
| `propms/api/amenities.py` | Top-level routers |
| `propms/api/mobile.py` | Flutter wrappers |
| `propms/hooks.py` | Scheduler already hourly — extend lifecycle entry |
| `docs/flutter_amenity_range_booking_prompt.md` | **Create** — Flutter handoff |
| `docs/postman/PropMS_Full_API.postman_collection.json` | New requests (force-add if gitignored) |

---

### Task 1: DocType fields (Amenity + Booking)

**Files:**
- Modify: `propms/property_management_solution/doctype/viva_amenity/viva_amenity.json`
- Modify: `propms/property_management_solution/doctype/viva_amenity_booking/viva_amenity_booking.json`

- [ ] **Step 1: Update Viva Amenity JSON**

In `field_order`, after `slot_duration_mins` (keep field for now), add:
`booking_time_step_mins`, `cleanup_buffer_mins`, `requires_approval`, `cancel_before_hours`.

Add fields (Int/Check as below). Change `slot_duration_mins` label description to note deprecated for booking engine; set `"reqd": 0` so new amenities are not forced to set it.

```json
{
  "default": "30",
  "description": "Start/End times must align to this step (minutes).",
  "fieldname": "booking_time_step_mins",
  "fieldtype": "Int",
  "label": "Booking Time Step (Minutes)",
  "reqd": 1
},
{
  "default": "0",
  "description": "Minutes blocked after each booking end before the next can start.",
  "fieldname": "cleanup_buffer_mins",
  "fieldtype": "Int",
  "label": "Cleanup Buffer (Minutes)"
},
{
  "default": "0",
  "description": "If checked, new bookings start as Pending until staff approve.",
  "fieldname": "requires_approval",
  "fieldtype": "Check",
  "label": "Requires Approval"
},
{
  "default": "2",
  "description": "Tenant may cancel until this many hours before start.",
  "fieldname": "cancel_before_hours",
  "fieldtype": "Int",
  "label": "Cancel Before Hours"
}
```

Update `slot_duration_mins`:
```json
"description": "Deprecated for booking engine. Prefer Booking Time Step. Kept for backward compatibility.",
"reqd": 0
```

- [ ] **Step 2: Update Viva Amenity Booking JSON**

Change `status` options to:
```
Pending
Confirmed
Completed
Cancelled
No Show
Rejected
```

Default status remains `Confirmed` (create API will set Pending when needed).

Add after `cancellation_reason` in field_order: `rejection_reason`, `approved_by`, `approved_on`.

```json
{
  "fieldname": "rejection_reason",
  "fieldtype": "Small Text",
  "label": "Rejection Reason"
},
{
  "fieldname": "approved_by",
  "fieldtype": "Link",
  "label": "Approved By",
  "options": "User",
  "read_only": 1
},
{
  "fieldname": "approved_on",
  "fieldtype": "Datetime",
  "label": "Approved On",
  "read_only": 1
}
```

- [ ] **Step 3: Migrate site**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com migrate
```

Expected: migrate completes; Amenity form shows new fields.

- [ ] **Step 4: Backfill defaults for existing amenities**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com console <<'PY'
import frappe
for name in frappe.get_all("Viva Amenity", pluck="name"):
    doc = frappe.get_doc("Viva Amenity", name)
    step = doc.booking_time_step_mins or doc.slot_duration_mins or 30
    frappe.db.set_value("Viva Amenity", name, {
        "booking_time_step_mins": step,
        "cleanup_buffer_mins": doc.cleanup_buffer_mins or 0,
        "requires_approval": doc.requires_approval or 0,
        "cancel_before_hours": doc.cancel_before_hours if doc.cancel_before_hours is not None else 2,
    }, update_modified=False)
frappe.db.commit()
print("backfill ok", frappe.db.count("Viva Amenity"))
PY
```

- [ ] **Step 5: Commit**

```bash
git add propms/property_management_solution/doctype/viva_amenity/viva_amenity.json \
  propms/property_management_solution/doctype/viva_amenity_booking/viva_amenity_booking.json
git commit -m "feat(amenities): range booking policy fields on Amenity and Booking"
```

---

### Task 2: Overlap helpers + unit tests

**Files:**
- Create: `propms/api/v1/amenities/overlap.py`
- Create: `propms/api/v1/amenities/test_overlap.py`

- [ ] **Step 1: Write failing tests**

Create `propms/api/v1/amenities/test_overlap.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import unittest
from datetime import time


class TestAmenityOverlap(unittest.TestCase):
	def test_ranges_overlap(self):
		from propms.api.v1.amenities.overlap import ranges_overlap

		self.assertTrue(ranges_overlap("18:00:00", "21:00:00", "19:00:00", "20:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", "21:00:00", "21:00:00", "22:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", "21:00:00", "16:00:00", "18:00:00"))

	def test_buffer_blocks_next_start(self):
		from propms.api.v1.amenities.overlap import expand_end_with_buffer, ranges_overlap

		end_buf = expand_end_with_buffer("21:00:00", 30)
		self.assertEqual(end_buf, "21:30:00")
		self.assertTrue(ranges_overlap("18:00:00", end_buf, "21:00:00", "22:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", end_buf, "21:30:00", "22:00:00"))

	def test_free_gaps(self):
		from propms.api.v1.amenities.overlap import compute_free_gaps

		gaps = compute_free_gaps(
			open_time="06:00:00",
			close_time="22:00:00",
			busy=[
				{"start_time": "10:00:00", "end_with_buffer": "12:30:00"},
				{"start_time": "18:00:00", "end_with_buffer": "21:30:00"},
			],
		)
		self.assertEqual(gaps[0]["start_time"], "06:00:00")
		self.assertEqual(gaps[0]["end_time"], "10:00:00")
		self.assertEqual(gaps[1]["start_time"], "12:30:00")
		self.assertEqual(gaps[1]["end_time"], "18:00:00")
		self.assertEqual(gaps[2]["start_time"], "21:30:00")
		self.assertEqual(gaps[2]["end_time"], "22:00:00")

	def test_time_on_step(self):
		from propms.api.v1.amenities.overlap import is_time_on_step

		self.assertTrue(is_time_on_step("18:00:00", 30))
		self.assertTrue(is_time_on_step("18:30:00", 30))
		self.assertFalse(is_time_on_step("18:15:00", 30))
		self.assertTrue(is_time_on_step("18:15:00", 15))
```

- [ ] **Step 2: Run tests — expect FAIL (import error)**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com run-tests --module propms.api.v1.amenities.test_overlap
```

Expected: FAIL — `No module named ...overlap` or ImportError.

- [ ] **Step 3: Implement `overlap.py`**

```python
# -*- coding: utf-8 -*-
"""Exclusive amenity range math (buffer, gaps, step). No DB I/O."""

from __future__ import unicode_literals
from datetime import datetime, timedelta


def _parse_hms(val):
	s = str(val or "00:00:00").strip()
	if len(s) == 5:
		s = s + ":00"
	parts = s.split(":")
	h, m = int(parts[0]), int(parts[1])
	sec = int(parts[2]) if len(parts) > 2 else 0
	return h * 3600 + m * 60 + sec


def _format_hms(total_seconds):
	total_seconds = int(total_seconds)
	if total_seconds < 0:
		total_seconds = 0
	h = total_seconds // 3600
	m = (total_seconds % 3600) // 60
	s = total_seconds % 60
	return f"{h:02d}:{m:02d}:{s:02d}"


def ranges_overlap(start_a, end_a, start_b, end_b):
	"""True if [start_a, end_a) overlaps [start_b, end_b). Touching endpoints do not overlap."""
	a0, a1 = _parse_hms(start_a), _parse_hms(end_a)
	b0, b1 = _parse_hms(start_b), _parse_hms(end_b)
	return not (a1 <= b0 or b1 <= a0)


def expand_end_with_buffer(end_time, buffer_mins):
	buf = max(0, int(buffer_mins or 0))
	return _format_hms(_parse_hms(end_time) + buf * 60)


def is_time_on_step(time_str, step_mins):
	step = max(1, int(step_mins or 1))
	secs = _parse_hms(time_str)
	return (secs % (step * 60)) == 0


def compute_free_gaps(open_time, close_time, busy):
	"""busy items need start_time + end_with_buffer. Returns sorted free gaps inside open–close."""
	open_s, close_s = _parse_hms(open_time), _parse_hms(close_time)
	blocks = []
	for b in busy or []:
		blocks.append((_parse_hms(b["start_time"]), _parse_hms(b["end_with_buffer"])))
	blocks.sort()
	merged = []
	for s, e in blocks:
		s = max(s, open_s)
		e = min(e, close_s)
		if e <= s:
			continue
		if not merged or s > merged[-1][1]:
			merged.append([s, e])
		else:
			merged[-1][1] = max(merged[-1][1], e)
	gaps = []
	cursor = open_s
	for s, e in merged:
		if s > cursor:
			gaps.append({"start_time": _format_hms(cursor), "end_time": _format_hms(s)})
		cursor = max(cursor, e)
	if cursor < close_s:
		gaps.append({"start_time": _format_hms(cursor), "end_time": _format_hms(close_s)})
	return gaps


def find_conflicting_booking(amenity, booking_date, start_time, end_time, buffer_mins, exclude_name=None):
	"""DB helper: return first conflicting Pending/Confirmed booking dict or None."""
	import frappe
	from frappe.utils import cint

	rows = frappe.get_all(
		"Viva Amenity Booking",
		filters={
			"amenity": amenity,
			"booking_date": str(booking_date),
			"status": ["in", ["Pending", "Confirmed"]],
		},
		fields=["name", "start_time", "end_time", "tenant", "tenant_name", "status"],
		ignore_permissions=True,
	)
	req_end_buf = expand_end_with_buffer(end_time, buffer_mins)
	for r in rows or []:
		if exclude_name and r.name == exclude_name:
			continue
		other_end_buf = expand_end_with_buffer(r.end_time, buffer_mins)
		if ranges_overlap(start_time, req_end_buf, r.start_time, other_end_buf):
			return r
	return None
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com run-tests --module propms.api.v1.amenities.test_overlap
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add propms/api/v1/amenities/overlap.py propms/api/v1/amenities/test_overlap.py
git commit -m "feat(amenities): exclusive range overlap helpers and unit tests"
```

---

### Task 3: Day availability API

**Files:**
- Modify: `propms/api/v1/amenities/slots.py`
- Modify: `propms/api/v1/amenities/list.py` (`_amenity_list_fields` + detail)
- Modify: `propms/api/v1/amenities/__init__.py`

- [ ] **Step 1: Add `get_amenity_day_availability` to `slots.py`**

Reuse `_parse_time_str` already in file. Append:

```python
@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_day_availability(amenity=None, booking_date=None):
	"""Busy intervals + free gaps for exclusive range booking UI."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not amenity or not frappe.db.exists("Viva Amenity", amenity):
			return {"status": "error", "message": "Valid amenity is required"}

		from propms.api.v1.amenities.list import _is_amenity_staff
		from propms.api.v1.amenities.overlap import compute_free_gaps, expand_end_with_buffer

		doc = frappe.get_doc("Viva Amenity", amenity)
		if not doc.is_active:
			return {"status": "error", "message": f"{doc.amenity_name} is currently inactive"}

		target_date = getdate(booking_date or nowdate())
		today_date = getdate(nowdate())
		max_advance = cint(doc.max_advance_days or 7)
		max_date = getdate(add_days(today_date, max_advance))
		if target_date < today_date:
			return {"status": "error", "message": "Cannot view availability for a past date"}
		if target_date > max_date:
			return {
				"status": "error",
				"message": f"Bookings can only be made up to {max_advance} days in advance (until {max_date}).",
			}

		open_t = _parse_time_str(doc.open_time or "06:00:00").strftime("%H:%M:%S")
		close_t = _parse_time_str(doc.close_time or "22:00:00").strftime("%H:%M:%S")
		step = max(1, cint(getattr(doc, "booking_time_step_mins", None) or doc.slot_duration_mins or 30))
		buffer_mins = max(0, cint(getattr(doc, "cleanup_buffer_mins", None) or 0))
		is_staff = _is_amenity_staff()

		existing = frappe.get_all(
			"Viva Amenity Booking",
			filters={
				"amenity": doc.name,
				"booking_date": str(target_date),
				"status": ["in", ["Pending", "Confirmed"]],
			},
			fields=["name", "start_time", "end_time", "status", "tenant_name", "tenant"],
			order_by="start_time asc",
			ignore_permissions=True,
		)

		busy = []
		for b in existing:
			s = _parse_time_str(b.start_time).strftime("%H:%M:%S")
			e = _parse_time_str(b.end_time).strftime("%H:%M:%S")
			end_buf = expand_end_with_buffer(e, buffer_mins)
			label = (b.tenant_name or b.tenant or "Booked") if is_staff else "Booked"
			busy.append({
				"booking_id": b.name,
				"start_time": s,
				"end_time": e,
				"end_with_buffer": end_buf,
				"status": b.status,
				"label": label,
			})

		free_gaps = compute_free_gaps(open_t, close_t, busy)

		return {
			"status": "success",
			"amenity": doc.name,
			"amenity_name": doc.amenity_name,
			"booking_date": str(target_date),
			"open_time": open_t,
			"close_time": close_t,
			"booking_time_step_mins": step,
			"cleanup_buffer_mins": buffer_mins,
			"requires_approval": cint(getattr(doc, "requires_approval", 0) or 0),
			"cancel_before_hours": cint(getattr(doc, "cancel_before_hours", None) or 2),
			"capacity": cint(doc.capacity or 0),
			"busy": busy,
			"free_gaps": free_gaps,
			"is_staff": is_staff,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenity_day_availability")
		return {"status": "error", "message": str(e)}
```

- [ ] **Step 2: Expose new fields on amenity list/detail**

In `list.py` `_amenity_list_fields()`, append:
`"booking_time_step_mins", "cleanup_buffer_mins", "requires_approval", "cancel_before_hours"`  
(keep `slot_duration_mins` for compat).

- [ ] **Step 3: Export from `__init__.py`**

```python
from propms.api.v1.amenities.slots import get_available_slots, get_amenity_day_availability
# add to __all__
```

- [ ] **Step 4: Manual smoke**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com console <<'PY'
import frappe
frappe.set_user("Administrator")
from propms.api.v1.amenities.slots import get_amenity_day_availability
amenity = frappe.db.get_value("Viva Amenity", {"is_active": 1}, "name")
print(get_amenity_day_availability(amenity=amenity, booking_date=frappe.utils.nowdate()))
PY
```

Expected: `status: success` with `busy`, `free_gaps`, policy fields.

- [ ] **Step 5: Commit**

```bash
git add propms/api/v1/amenities/slots.py propms/api/v1/amenities/list.py propms/api/v1/amenities/__init__.py
git commit -m "feat(amenities): day availability API for exclusive range booking"
```

---

### Task 4: Exclusive create_booking

**Files:**
- Modify: `propms/api/v1/amenities/booking.py`

- [ ] **Step 1: Replace capacity-overlap block with exclusive validation**

In `create_booking`, after parsing `s_time` / `e_time`:

1. Require `e_time > s_time`.
2. Load `step`, `buffer_mins`, `requires_approval`, `open_time`, `close_time` from amenity.
3. Reject if start/end not `is_time_on_step`.
4. Reject if outside open–close (`s_time < open` or `e_time > close`).
5. Call `find_conflicting_booking(...)`; if found return error:
   `"This time overlaps an existing booking ({start}–{end})."`
6. Do **not** enforce guests vs capacity (informational only). Still accept `guests_count`.
7. Set status: `"Pending" if cint(amenity_doc.requires_approval) else "Confirmed"`.
8. Keep `notify_amenity_booked` after insert.

Skeleton for validation section (replace old overlapping guest loop):

```python
from propms.api.v1.amenities.overlap import (
	is_time_on_step,
	find_conflicting_booking,
)

if e_time <= s_time:
	return {"status": "error", "message": "End time must be after start time"}

step = max(1, cint(getattr(amenity_doc, "booking_time_step_mins", None) or amenity_doc.slot_duration_mins or 30))
buffer_mins = max(0, cint(getattr(amenity_doc, "cleanup_buffer_mins", None) or 0))
open_s = _parse_time_str(amenity_doc.open_time).strftime("%H:%M:%S")
close_s = _parse_time_str(amenity_doc.close_time).strftime("%H:%M:%S")

if not is_time_on_step(s_time, step) or not is_time_on_step(e_time, step):
	return {"status": "error", "message": f"Start and end times must align to {step}-minute steps"}

if s_time < open_s or e_time > close_s:
	return {"status": "error", "message": f"Booking must be within operating hours ({open_s}–{close_s})"}

conflict = find_conflicting_booking(
	amenity_doc.name, target_date, s_time, e_time, buffer_mins
)
if conflict:
	cs = _parse_time_str(conflict.start_time).strftime("%H:%M:%S")
	ce = _parse_time_str(conflict.end_time).strftime("%H:%M:%S")
	return {
		"status": "error",
		"message": f"This time overlaps an existing booking ({cs}–{ce}).",
		"conflict_booking_id": conflict.name,
		"conflict_start": cs,
		"conflict_end": ce,
	}

initial_status = "Pending" if cint(getattr(amenity_doc, "requires_approval", 0)) else "Confirmed"
# guests_count informational — no capacity check
```

Set `"status": initial_status` on insert doc.

Import `_parse_time_str` from slots if not already available in booking.py (today booking imports from slots only `_parse_time_str` — keep that).

- [ ] **Step 2: Smoke create overlap**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com console <<'PY'
import frappe
from propms.api.v1.amenities.booking import create_booking
# Use a tenant session if needed; Administrator may bypass lease helpers
frappe.set_user("baraka@vvsdtz.com")
amenity = frappe.db.get_value("Viva Amenity", {"amenity_name": ["like", "%Party%"]}, "name") or frappe.db.get_value("Viva Amenity", {"is_active":1}, "name")
r1 = create_booking(amenity=amenity, booking_date=frappe.utils.add_days(frappe.utils.nowdate(), 1), start_time="18:00:00", end_time="21:00:00", guests_count=10)
print("first", r1)
r2 = create_booking(amenity=amenity, booking_date=frappe.utils.add_days(frappe.utils.nowdate(), 1), start_time="19:00:00", end_time="20:00:00", guests_count=2)
print("overlap", r2)
assert r2.get("status") == "error"
print("OK")
PY
```

Expected: first success; second error with overlap message.

- [ ] **Step 3: Commit**

```bash
git add propms/api/v1/amenities/booking.py
git commit -m "feat(amenities): exclusive create_booking with approval and buffer"
```

---

### Task 5: Cancel-before-hours policy

**Files:**
- Modify: `propms/api/v1/amenities/user_bookings.py`

- [ ] **Step 1: Enforce cancel window for tenants**

After loading `doc` and auth checks, before setting Cancelled:

```python
from frappe.utils import get_datetime, now_datetime, cint

if not is_staff:
	amenity = frappe.get_doc("Viva Amenity", doc.amenity)
	hours = cint(getattr(amenity, "cancel_before_hours", None) or 2)
	start_dt = get_datetime(f"{doc.booking_date} {doc.start_time}")
	deadline = start_dt - frappe.utils.datetime.timedelta(hours=hours)
	# prefer: from datetime import timedelta
	if now_datetime() >= deadline:
		return {
			"status": "error",
			"message": f"Cancellation is only allowed until {hours} hour(s) before start.",
		}
```

Use `from datetime import timedelta` at top of file if not present:
```python
deadline = start_dt - timedelta(hours=hours)
```

Staff bypass this check. Keep `notify_amenity_booking_cancelled`.

Allow cancel for status in `Pending` and `Confirmed` only (already blocks Completed/Cancelled).

- [ ] **Step 2: Smoke**

Confirm staff can cancel anytime; tenant near start gets error (set `cancel_before_hours` high temporarily if needed).

- [ ] **Step 3: Commit**

```bash
git add propms/api/v1/amenities/user_bookings.py
git commit -m "feat(amenities): enforce cancel_before_hours for tenants"
```

---

### Task 6: Staff approve / reject

**Files:**
- Create: `propms/api/v1/amenities/staff_actions.py`
- Modify: `propms/api/v1/amenities/notify.py`
- Modify: `propms/api/v1/amenities/__init__.py`

- [ ] **Step 1: Implement staff_actions.py**

```python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload
from propms.api.v1.amenities.overlap import find_conflicting_booking
from propms.api.v1.amenities.slots import _parse_time_str


@frappe.whitelist(methods=["POST"])
def approve_amenity_booking(booking_id=None):
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can approve bookings"), frappe.PermissionError)

		payload = _parse_request_payload({"booking_id": booking_id})
		target = (payload.get("booking_id") or "").strip()
		if not target or not frappe.db.exists("Viva Amenity Booking", target):
			return {"status": "error", "message": f"Booking {target} not found"}

		doc = frappe.get_doc("Viva Amenity Booking", target)
		if doc.status != "Pending":
			return {"status": "error", "message": f"Only Pending bookings can be approved (current: {doc.status})"}

		amenity = frappe.get_doc("Viva Amenity", doc.amenity)
		buffer_mins = max(0, cint(getattr(amenity, "cleanup_buffer_mins", 0) or 0))
		s = _parse_time_str(doc.start_time).strftime("%H:%M:%S")
		e = _parse_time_str(doc.end_time).strftime("%H:%M:%S")
		conflict = find_conflicting_booking(
			doc.amenity, doc.booking_date, s, e, buffer_mins, exclude_name=doc.name
		)
		if conflict:
			return {
				"status": "error",
				"message": f"Cannot approve: overlaps {conflict.name}",
			}

		doc.status = "Confirmed"
		doc.approved_by = frappe.session.user
		doc.approved_on = now_datetime()
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		from propms.api.v1.amenities.notify import notify_amenity_booking_approved
		notify_amenity_booking_approved(doc)

		return {"status": "success", "message": "Booking approved", "booking_id": doc.name, "doc": doc.as_dict()}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "approve_amenity_booking")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def reject_amenity_booking(booking_id=None, rejection_reason=None):
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can reject bookings"), frappe.PermissionError)

		payload = _parse_request_payload({
			"booking_id": booking_id,
			"rejection_reason": rejection_reason,
		})
		target = (payload.get("booking_id") or "").strip()
		reason = (payload.get("rejection_reason") or "").strip()
		if not target or not frappe.db.exists("Viva Amenity Booking", target):
			return {"status": "error", "message": f"Booking {target} not found"}
		if not reason:
			return {"status": "error", "message": "rejection_reason is required"}

		doc = frappe.get_doc("Viva Amenity Booking", target)
		if doc.status != "Pending":
			return {"status": "error", "message": f"Only Pending bookings can be rejected (current: {doc.status})"}

		doc.status = "Rejected"
		doc.rejection_reason = reason
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		from propms.api.v1.amenities.notify import notify_amenity_booking_rejected
		notify_amenity_booking_rejected(doc)

		return {"status": "success", "message": "Booking rejected", "booking_id": doc.name}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "reject_amenity_booking")
		return {"status": "error", "message": str(e)}
```

- [ ] **Step 2: Add notify helpers in `notify.py`**

```python
def notify_amenity_booking_approved(booking_doc):
	"""Notify tenant that Pending booking was approved."""
	try:
		tenant = getattr(booking_doc, "tenant", None)
		if not tenant:
			return
		payload = _booking_payload(booking_doc, event_type="amenity_booking_approved")
		events = ("amenity_booking_approved",)
		for ev in events:
			frappe.publish_realtime(event=ev, message=payload, user=tenant, after_commit=True)
			for room in (f"user:{tenant}", f"user_{tenant}"):
				frappe.publish_realtime(event=ev, message=payload, room=room, after_commit=True)
		title = f"Amenity booking approved: {payload.get('amenity_name')}"
		body = f"Your booking on {payload.get('booking_date')} {payload.get('start_time')}-{payload.get('end_time')} was approved."
		frappe.enqueue(
			"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
			queue="short",
			user=tenant,
			title=title,
			body=body,
			payload=payload,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booking_approved")


def notify_amenity_booking_rejected(booking_doc):
	"""Notify tenant that Pending booking was rejected."""
	try:
		tenant = getattr(booking_doc, "tenant", None)
		if not tenant:
			return
		payload = _booking_payload(booking_doc, event_type="amenity_booking_rejected")
		events = ("amenity_booking_rejected",)
		for ev in events:
			frappe.publish_realtime(event=ev, message=payload, user=tenant, after_commit=True)
			for room in (f"user:{tenant}", f"user_{tenant}"):
				frappe.publish_realtime(event=ev, message=payload, room=room, after_commit=True)
		title = f"Amenity booking rejected: {payload.get('amenity_name')}"
		reason = getattr(booking_doc, "rejection_reason", "") or ""
		body = f"Your booking was rejected. {reason}".strip()
		frappe.enqueue(
			"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
			queue="short",
			user=tenant,
			title=title,
			body=body,
			payload=payload,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booking_rejected")
```

- [ ] **Step 3: Export approve/reject from `__init__.py`**

- [ ] **Step 4: Smoke approve/reject with `requires_approval=1` on a test amenity**

- [ ] **Step 5: Commit**

```bash
git add propms/api/v1/amenities/staff_actions.py propms/api/v1/amenities/notify.py propms/api/v1/amenities/__init__.py
git commit -m "feat(amenities): staff approve and reject pending bookings"
```

---

### Task 7: Lifecycle — auto-cancel stale Pending

**Files:**
- Modify: `propms/api/v1/amenities/lifecycle.py`
- Modify: `propms/hooks.py` (same cron entry is enough if function handles both)

- [ ] **Step 1: Extend lifecycle**

```python
def reconcile_stale_pending_amenity_bookings(limit=500):
	"""Pending bookings whose start has passed → Cancelled (free the window)."""
	now = now_datetime()
	rows = frappe.get_all(
		"Viva Amenity Booking",
		filters={"status": "Pending"},
		fields=["name", "booking_date", "start_time"],
		limit_page_length=limit,
		ignore_permissions=True,
	)
	updated = 0
	for row in rows:
		if not row.booking_date or not row.start_time:
			continue
		try:
			start_dt = get_datetime(f"{row.booking_date} {row.start_time}")
		except Exception:
			continue
		if start_dt >= now:
			continue
		frappe.db.set_value(
			"Viva Amenity Booking",
			row.name,
			{
				"status": "Cancelled",
				"cancellation_reason": "Auto-cancelled: pending approval past start time",
			},
			update_modified=True,
		)
		updated += 1
	if updated:
		frappe.db.commit()
	return {"updated": updated}


def complete_past_confirmed_bookings():
	"""Scheduler entrypoint (hourly)."""
	done = reconcile_completed_amenity_bookings()
	pending = reconcile_stale_pending_amenity_bookings()
	return {"completed": done, "pending_cancelled": pending}
```

- [ ] **Step 2: Confirm hooks still point at `complete_past_confirmed_bookings`**

`hooks.py` already has:
`"15 * * * *": ["propms.api.v1.amenities.lifecycle.complete_past_confirmed_bookings"]`  
No change required if Step 1 expands that function.

- [ ] **Step 3: Commit**

```bash
git add propms/api/v1/amenities/lifecycle.py
git commit -m "feat(amenities): auto-cancel pending bookings past start"
```

---

### Task 8: Mobile wrappers + amenities router

**Files:**
- Modify: `propms/api/mobile.py`
- Modify: `propms/api/amenities.py`

- [ ] **Step 1: Add wrappers on `mobile.py` near existing amenity wrappers**

```python
@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_day_availability(amenity=None, booking_date=None):
	from propms.api.v1.amenities import get_amenity_day_availability as v1
	return v1(amenity=amenity, booking_date=booking_date)


@frappe.whitelist(methods=["POST"])
def approve_amenity_booking(booking_id=None):
	from propms.api.v1.amenities import approve_amenity_booking as v1
	return v1(booking_id=booking_id)


@frappe.whitelist(methods=["POST"])
def reject_amenity_booking(booking_id=None, rejection_reason=None):
	from propms.api.v1.amenities import reject_amenity_booking as v1
	return v1(booking_id=booking_id, rejection_reason=rejection_reason)
```

- [ ] **Step 2: Mirror on `propms/api/amenities.py`**

Same three functions delegating to v1.

- [ ] **Step 3: Ensure `__init__.py` exports**

```python
from propms.api.v1.amenities.staff_actions import approve_amenity_booking, reject_amenity_booking
from propms.api.v1.amenities.slots import get_amenity_day_availability
```

- [ ] **Step 4: Commit**

```bash
git add propms/api/mobile.py propms/api/amenities.py propms/api/v1/amenities/__init__.py
git commit -m "feat(amenities): mobile wrappers for day availability and staff approve/reject"
```

---

### Task 9: Flutter prompt + Postman

**Files:**
- Create: `docs/flutter_amenity_range_booking_prompt.md`
- Modify: `docs/postman/PropMS_Full_API.postman_collection.json` (git add `-f`)

- [ ] **Step 1: Write Flutter handoff prompt** covering:
  - Hybrid UI (busy strip + Start/End)
  - Endpoints: `get_amenity_day_availability`, `create_amenity_booking`, `cancel_amenity_booking`, `approve_amenity_booking`, `reject_amenity_booking`, `get_my_amenity_bookings`
  - Statuses including Pending/Rejected
  - WS/FCM event names
  - Deprecate reliance on `get_available_slots` for Party Hall

- [ ] **Step 2: Add Postman requests** under Amenities folder for the three new methods + note on create overlap error shape.

- [ ] **Step 3: Commit**

```bash
git add -f docs/flutter_amenity_range_booking_prompt.md docs/postman/PropMS_Full_API.postman_collection.json
git commit -m "docs: Flutter prompt and Postman for amenity range booking"
```

---

### Task 10: End-to-end verification checklist

- [ ] **Step 1: Run unit tests**

```bash
cd /home/vvsd/dev2_version15_bench && bench --site dev15-viva2.vvsdtz.com run-tests --module propms.api.v1.amenities.test_overlap
```

Expected: PASS.

- [ ] **Step 2: Manual E2E on `dev15-viva2.vvsdtz.com`**

1. Set Party Hall: `requires_approval=1`, `cleanup_buffer_mins=30`, `booking_time_step_mins=30`, `cancel_before_hours=2`.
2. Tenant: day availability → see free gaps; book 18:00–21:00 → Pending; staff FCM.
3. Second tenant: 19:00–20:00 → overlap error; 21:00–22:00 → blocked by buffer until 21:30.
4. Staff approve → tenant FCM; status Confirmed.
5. Tenant cancel within policy works; inside 2h before start fails.
6. Leave a Pending with past start; run lifecycle → Cancelled.

- [ ] **Step 3: Final commit only if verification left small fixes** (message accordingly).

---

## Spec coverage self-check

| Spec requirement | Task |
|------------------|------|
| Exclusive overlap + buffer after end | 2, 4 |
| Day availability busy + free_gaps | 3 |
| Per-amenity step / buffer / approval / cancel hours | 1, 3–5 |
| Pending blocks; approve/reject | 4, 6 |
| Guests informational | 4 |
| Auto-cancel Pending past start | 7 |
| Mobile wrappers + Flutter handoff | 8, 9 |
| Notifications approve/reject | 6 |
| Deprecate slot engine for UI | 3, 9 |

No TBD placeholders. Function names consistent: `get_amenity_day_availability`, `approve_amenity_booking`, `reject_amenity_booking`, `find_conflicting_booking`, `expand_end_with_buffer`.
