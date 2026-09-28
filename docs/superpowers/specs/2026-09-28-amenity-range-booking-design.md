# Amenity Range Booking (Exclusive Windows) — Design

**Date:** 2026-09-28  
**Status:** Approved for planning  
**Approach:** Free-form start→end booking with exclusive overlap + per-amenity buffer/approval (replace fixed slot-duration engine)

## Goal

Tenants book Viva Amenities by choosing **from this time to that time** (not fixed slot chips). Bookings on the same amenity **must not overlap**. Staff can configure cleanup buffer, approval, cancel window, and time-picker step per amenity. Mobile app updates to a hybrid day-strip + Start/End pickers UX.

## Non-goals (V1)

- Overnight / multi-day ranges
- Min/max booking duration limits
- Recurring bookings
- Paid amenity / checkout
- Editing a booking (cancel + rebook only)
- Shared-capacity concurrent bookings (multiple overlapping guests until capacity)

## Decisions

| Topic | Choice |
|-------|--------|
| Overlap model | Exclusive — one Pending/Confirmed booking owns the window |
| Mobile UI | Hybrid: busy day strip + Start/End pickers (tap free gap → prefill) |
| Cleanup buffer | Per amenity (`cleanup_buffer_mins`), applied **after** booking end |
| Duration min/max | None for V1 |
| Approval | Per amenity (`requires_approval`) → status `Pending` vs `Confirmed` |
| Pending holds time | Yes — Pending blocks like Confirmed |
| Cancel | Tenant until `cancel_before_hours` before start; no edit |
| Time step | Per amenity (`booking_time_step_mins`) |
| Guests | Keep `guests_count` — informational only (no capacity enforcement) |
| Pending past start | Auto-cancel Pending when start time has passed |
| Architecture | Range booking + `get_amenity_day_availability` API |

## Current state (baseline)

- **Viva Amenity** uses `slot_duration_mins` to generate fixed slots via `get_available_slots`.
- **Viva Amenity Booking** already has `booking_date`, `start_time`, `end_time`.
- Create path already checks overlap but treats capacity as shared guest spots.
- Staff FCM/WebSocket notify exists for book/cancel (`propms.api.v1.amenities.notify`).

## Data model

### Viva Amenity

| Field | Action |
|-------|--------|
| `slot_duration_mins` | Deprecate as booking engine; migrate default into `booking_time_step_mins` where useful |
| `booking_time_step_mins` | **New** Int, default `30` — Start/End must snap to this step |
| `cleanup_buffer_mins` | **New** Int, default `0` — blocks time after each booking end |
| `requires_approval` | **New** Check — if 1, new bookings start as `Pending` |
| `cancel_before_hours` | **New** Int/Float, default `2` — tenant cancel allowed until this many hours before start |
| `open_time`, `close_time`, `max_advance_days` | Keep |
| `capacity` | Keep for display / informational guests guidance only |
| Publish / media / guidelines | Keep |

### Viva Amenity Booking

| Field | Action |
|-------|--------|
| `booking_date`, `start_time`, `end_time` | Keep — free-form same-day range |
| `status` | `Pending`, `Confirmed`, `Completed`, `Cancelled`, `No Show`, `Rejected` |
| `guests_count` | Keep — informational only |
| `rejection_reason` | **New** optional |
| `approved_by`, `approved_on` | **New** optional audit fields |
| Tenant / unit / lease / notes | Keep |

### Overlap rule (authoritative on server)

Two bookings conflict when:

1. Same `amenity`
2. Same `booking_date`
3. Status in (`Pending`, `Confirmed`)
4. Time ranges overlap after expanding each booking’s **end** by amenity `cleanup_buffer_mins`

Formal interval overlap: `not (end_a_buffered <= start_b or end_b_buffered <= start_a)`.

V1: same calendar day only; `end_time > start_time`; both within `open_time`–`close_time`.

## APIs

### `get_amenity_day_availability(amenity, booking_date)`

Primary availability endpoint for mobile.

Returns:

- Amenity rules: `open_time`, `close_time`, `booking_time_step_mins`, `cleanup_buffer_mins`, `requires_approval`, `cancel_before_hours`, `capacity` (display)
- `busy[]`: `{ booking_id, start_time, end_time, end_with_buffer, status, label }`  
  - Tenants: anonymized label (e.g. “Booked”)  
  - Staff: may include tenant name
- `free_gaps[]`: `{ start_time, end_time }` inside open hours after subtracting busy+buffer

### `get_available_slots`

Keep temporarily for compatibility; document as deprecated in favor of day availability (or thin adapter over free gaps). Flutter should migrate.

### `create_amenity_booking`

Inputs: `amenity`, `booking_date`, `start_time`, `end_time`, optional `guests_count`, `notes`, `lease`, `property_unit`.

Validates: auth, active+published (tenant), advance window, open hours, end > start, snap to step, exclusive overlap + buffer, same-day.

Status: `Pending` if `requires_approval` else `Confirmed`.

Notify staff (existing amenity notify path; include Pending).

### `cancel_amenity_booking`

- Tenant: allowed only if status in (`Pending`, `Confirmed`) and `now < start - cancel_before_hours`
- Staff: always allowed (with reason optional)

### Staff: `approve_amenity_booking` / `reject_amenity_booking`

Staff roles only (same amenity staff gate).  
Approve: Pending → Confirmed (re-check overlap).  
Reject: Pending → Rejected + `rejection_reason`; frees time.  
Notify tenant via WebSocket + FCM.

### Lists

`get_my_amenity_bookings` includes Pending/Rejected; staff still see all tenants.

### Lifecycle jobs

- Confirmed past end → Completed (existing)
- **Pending whose start has passed → auto-Cancelled** (new), so time is not stuck blocked

## Mobile UX

### Tenant book

1. Amenity detail → Book  
2. Pick date  
3. Load day availability → busy strip + free gaps  
4. Start/End pickers (step from amenity); tap free gap optional prefill  
5. Guests optional (info) + notes  
6. Submit → Confirmed or Pending approval messaging  
7. Overlap error cites conflicting window  

### Tenant my bookings

Show Pending / Confirmed / Completed / Cancelled / Rejected.  
Cancel control only when policy allows.

### Staff

Pending queue + approve/reject/cancel.  
Listen: `amenity_booked`, `amenity_booking_cancelled`, `amenity_booking_approved`, `amenity_booking_rejected`.

## Errors (stable messages)

- Outside open hours  
- End ≤ start  
- Time not aligned to step  
- Overlaps existing booking (include conflict start–end)  
- Past / beyond max advance  
- Cancel too late  
- Amenity inactive / unpublished  
- Not permitted (staff actions)  

Race: first commit wins; second gets overlap error. Server always re-validates.

## Notifications

- Tenant creates (Pending or Confirmed) → staff WS + FCM  
- Staff approve / reject / cancel → tenant WS + FCM  
- Tenant cancel → staff WS + FCM  

Reuse `propms.api.v1.amenities.notify` with new event types for approve/reject.

## Migration notes

1. Add new Amenity / Booking fields; migrate DocTypes.  
2. Set defaults: `booking_time_step_mins=30` (or copy from old `slot_duration_mins` if sensible), `cleanup_buffer_mins=0`, `requires_approval=0`, `cancel_before_hours=2`.  
3. Existing Confirmed bookings remain valid; Party Hall can enable approval + buffer after go-live.  
4. Ship Flutter handoff prompt after backend endpoints land.

## Success criteria

- Tenant cannot book overlapping exclusive window (including buffer) on Party Hall.  
- Day availability returns correct busy + free gaps for hybrid UI.  
- Approval amenities create Pending that blocks time until approve/reject/auto-cancel.  
- Tenant cancel respects `cancel_before_hours`.  
- Staff phone receives FCM/WS on new booking; tenant on approve/reject.
