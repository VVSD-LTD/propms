# Design: Amenity Booking Request + Auto-Approval + Series Bookings Table

**Date:** 2026-10-05  
**Status:** Approved  
**Site / product:** PropMS mobile + Desk (Viva Towers)  
**Related:** Existing amenity exclusive booking (`Amenity`, `Amenity Booking`, `Amenity Booking Series`)

---

## 1. Problem

Today a tenant create goes straight to **`Amenity Booking`**:

- `requires_approval = 1` → status `Pending` (acts as a soft “request”)
- `requires_approval = 0` → status `Confirmed`

There is **no Request DocType**. Staff see pending items mixed with real bookings. Clients want:

1. **Every** tenant ask to create a **Request** first.
2. Amenity setting **Auto-Approval** (only when the amenity requires booking):
   - ON → Request immediately becomes a Confirmed Booking
   - OFF → staff must approve the Request; **then** a Booking is created
3. On **Amenity Booking Series**, a **child table** listing all bookings in that series so staff can see “Tenant X booked for a month — here are the N bookings.”

---

## 2. Goals

- Clear separation: **Request** (intent / approval queue) vs **Booking** (calendar hold that is confirmed inventory).
- Desk: Amenity field **`auto_approval`** replaces **`requires_approval`**, shown only when **`requires_booking`** is checked.
- Open Requests **block** the slot (same as today’s Pending).
- Series form shows an auto-maintained, read-only child table of linked bookings.
- Minimize Flutter breakage: keep familiar mobile method names where possible; change semantics under the hood.

### Non-goals

- Redesigning amenity catalog / audience / exclusive overlap rules (reuse existing).
- Reworking payment for amenities.
- Changing water delivery / POS / directory (unrelated).

---

## 3. Decisions locked

| Topic | Decision |
|-------|----------|
| Approach | New DocType **Amenity Booking Request**; Booking created only on auto-approve or staff approve |
| Auto-Approval UI | Checkbox on Amenity; depends on `requires_booking` |
| Field migration | Replace `requires_approval` with `auto_approval` (`auto_approval = not requires_approval`) |
| Default | `auto_approval = 0` (safer — needs staff unless staff turns it on) |
| Slot hold | Open Request **blocks** the slot |
| Series child table | Auto-filled, read-only rows when bookings are created/linked to the series |

---

## 4. Amenity DocType changes

### Fields

| Field | Behavior |
|-------|----------|
| `requires_booking` | Unchanged — amenity is bookable or not |
| `auto_approval` | **New** Check; label **Auto-Approval**; `depends_on`: `requires_booking`; default `0` |
| `requires_approval` | **Removed** after data migration |

### Desk copy

- Auto-Approval: “When checked, tenant Requests are approved automatically and a Confirmed Booking is created. When unchecked, staff must approve the Request before a Booking is created.”

### Patch

1. Add `auto_approval` column.
2. `UPDATE Amenity SET auto_approval = IFNULL(1 - requires_approval, 0)` (or equivalent).
3. Remove `requires_approval` from DocType JSON / DB.
4. Update all Python that read `requires_approval` to use `auto_approval` (inverted logic).

---

## 5. New DocType: `Amenity Booking Request`

### Purpose

Single source for tenant booking **intent** and staff **approval queue**.

### Naming

`VABR-.YYYY.-.#####` (or `ABR-.YYYY.-.#####` — pick one consistent with `VAB-` / `VABS-`).

### Core fields

| Field | Type | Notes |
|-------|------|--------|
| `amenity` | Link → Amenity | reqd |
| `booking_date` | Date | reqd |
| `start_time` / `end_time` | Time | reqd |
| `status` | Select | see below |
| `tenant` | Link → User | |
| `tenant_name` | Data | |
| `property_unit` | Link → Property | |
| `lease` | Link → Lease | |
| `guests_count` | Int | |
| `notes` | Small Text | |
| `series` | Link → Amenity Booking Series | optional |
| `booking` | Link → Amenity Booking | set when Approved |
| `approved_by` / `approved_on` | | staff approve |
| `rejection_reason` | Small Text | reject |
| `cancelled_by` / cancellation fields | | as needed |

### Statuses

`Open` · `Approved` · `Rejected` · `Cancelled` · `Expired`

| Status | Meaning | Slot |
|--------|---------|------|
| Open | Waiting (or mid auto-approve) | **Holds** |
| Approved | Booking created; `booking` linked | Hold moves to Booking |
| Rejected | Staff rejected | Freed |
| Cancelled | Tenant/staff cancelled before approve | Freed |
| Expired | Past start still Open (reconciler) | Freed |

---

## 6. Flows

### 6.1 Tenant create (single)

1. Validate amenity (`requires_booking`, published/active, audience, overlap including **Open Requests** + Confirmed Bookings).
2. Insert **Amenity Booking Request** with `status=Open`.
3. If `amenity.auto_approval`:
   - Create **Amenity Booking** `Confirmed`
   - Set Request `Approved`, link `booking`, set approved metadata (system / auto)
   - Notify as “booked” (existing notify pattern)
4. Else:
   - Leave Request `Open`
   - Notify staff (existing pending notify pattern)

### 6.2 Staff approve / reject

- **Approve** Open Request: re-check overlap → create Confirmed Booking → Request `Approved` + link → notify tenant.
- **Reject** Open Request: require reason → `Rejected` → notify tenant → slot free.

### 6.3 Cancel

- Tenant/staff may cancel **Open** Request → `Cancelled`.
- Cancel of **Approved** Request’s Booking uses existing booking cancel rules; optionally mark Request cancelled or leave Approved with cancelled booking (prefer: cancel Booking; keep Request Approved with booking status reflecting cancel — or set Request to Cancelled if booking cancelled before start). **Recommendation:** canceling an Open Request cancels the request; canceling a Confirmed Booking uses existing booking cancel API; Request stays Approved with linked booking now Cancelled.

### 6.4 Stale Open Requests

Reuse lifecycle idea: Open Requests whose `booking_date`+`start_time` is past → `Expired` (or Cancelled with system reason). Run on read paths + scheduler like today.

### 6.5 Series (recurring)

Same Request-first model at series level:

- Creating a recurring series creates a **Series** document plus **one Request per occurrence** (or one Series-level request that expands — prefer **one Request per occurrence** for overlap clarity), OR keep series Pending and expand to Requests.

**Recommendation (aligned with Approach 1):**

- `create_recurring_amenity_booking` creates `Amenity Booking Series` + child occurrence **Requests** (Open).
- If `auto_approval`: each Open Request immediately becomes a Confirmed Booking; series status → Confirmed; child table fills.
- If not: series stays Pending/Open-equivalent; staff approve series → approve all Open Requests → create Bookings; or approve per Request.
- Series **child table of bookings** fills only when Bookings exist.

(Exact series approve UX can stay “approve whole series” as today, implemented as approve-all Open Requests.)

---

## 7. Overlap / availability

Busy set for exclusive amenities:

- `Amenity Booking` in statuses that currently block (Confirmed, Pending if any remain during migration, etc.) — **after cutover, Pending bookings should not be created**; use Open Requests instead.
- **Open** `Amenity Booking Request` rows for the same amenity/time.

Cleanup buffer and exclusive rules unchanged.

---

## 8. Amenity Booking Series — bookings child table

### Child DocType: `Amenity Booking Series Item` (name TBD)

| Field | Type |
|-------|------|
| `booking` | Link → Amenity Booking (reqd) |
| `booking_date` | Date (fetch/read-only) |
| `start_time` / `end_time` | Time (read-only) |
| `status` | Data/Select (read-only mirror) |

- Parent field on Series: `bookings` (Table).
- **Auto-maintained, not manually edited** (`read_only` on grid / ignore user edits in validate).
- On Booking insert with `series` set → append/update row.
- On Booking status change → update row `status`.
- On Booking cancel/delete → update status or remove row (prefer keep row with status Cancelled for audit).

Source of truth remains `Amenity Booking.series` Link; child table is Desk visibility.

---

## 9. API / mobile

Prefer keeping wrappers; change implementations:

| Mobile method | New behavior |
|---------------|--------------|
| `create_amenity_booking` | Creates **Request**; may auto-create Booking |
| `approve_amenity_booking` | Approves **Request** id (param may stay `booking_id` for compat or rename to `request_id` with alias) |
| `reject_amenity_booking` | Rejects **Request** |
| `get_my_amenity_bookings` | Return Open Requests as pending + Bookings as confirmed/history (document shape carefully for Flutter) |
| `cancel_amenity_booking` | Cancel Open Request **or** Confirmed Booking by id/type |

**Compat note:** Flutter today may treat create response as a booking id. Response should include:

```json
{
  "status": "success",
  "request_id": "VABR-...",
  "booking_id": "VAB-..." | null,
  "request_status": "Open" | "Approved",
  "auto_approved": true | false
}
```

Document this for a Flutter prompt after backend lands.

Series mobile wrappers (if missing) follow the same Request-first pattern.

---

## 10. Desk UX

- Workspace / list: **Amenity Booking Request** (filter Open) for staff queue.
- Amenity form: Auto-Approval under booking rules when Requires Booking.
- Series form: section **Bookings** child table (read-only).

---

## 11. Migration

1. Patch Amenity field rename/invert.
2. For existing `Amenity Booking` with `status=Pending`:
   - Create matching **Request** `Open` (copy fields), link optionally, **or** convert Pending → treat as Request and delete/cancel old Pending booking after Request created (avoid double slot hold).
   - **Recommended:** create Request Open from Pending booking fields; set old Pending booking to Cancelled with reason “migrated to request” **or** delete force if safe — simplest safe path: create Request, cancel Pending booking in same transaction after Request exists so only Request holds slot.
3. Backfill Series child table from existing `Amenity Booking` where `series` is set.

---

## 12. Testing

- Unit: auto_approval create → Request Approved + Booking Confirmed; non-auto → Open only; overlap blocks Open Request; approve creates Booking; reject frees slot; series child rows auto-append.
- API smoke on `dev15-viva2`: Gym (auto) vs Party Hall (manual) style amenities.
- Series: create month of occurrences; Desk shows all booking rows.

---

## 13. Implementation sketch (after this spec is reviewed)

1. Amenity field patch (`auto_approval`)
2. DocTypes: Amenity Booking Request + Series Item child
3. Overlap helpers include Open Requests
4. Rewrite create / approve / reject / cancel / lifecycle
5. Series create/approve + child table sync
6. Mobile response shape + short Flutter prompt
7. Migration patch for Pending bookings + series rows
8. Tests

---

## 14. Open items (non-blocking)

- Exact naming series prefix `VABR-` vs `ABR-`
- Whether series approve is only bulk or also per-request in v1
- Flutter prompt timing (same PR vs follow-up)
