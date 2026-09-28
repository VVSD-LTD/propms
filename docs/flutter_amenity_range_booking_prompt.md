# Master AI Directive: Amenity Range Booking — Exclusive Windows UX & API

> **Target Audience**: Cursor agent on the **Viva Towers Flutter** (tenant + staff) app.  
> **Objective**: Replace fixed slot-chip booking with **free-form Start→End** ranges. Same amenity cannot overlap (Pending/Confirmed exclusive + cleanup buffer).  
> **Backend**: Implemented on PropMS. Do **not** invent endpoints. Prefer mobile wrappers.

**Base URL:** `https://dev15-viva2.vvsdtz.com`  
Prefer `propms.api.mobile.*`. Response envelope: `{ "message": { ... } }`.

**Related:** Design `docs/superpowers/specs/2026-09-28-amenity-range-booking-design.md`

---

## 1. What changed (important)

| Before (wrong for Party Hall) | After (correct) |
|---|---|
| Fixed chips from `get_available_slots` / `slot_duration_mins` | **Hybrid UI**: busy day strip + **Start/End** pickers |
| Shared-capacity guest math | **Exclusive** windows — one Pending/Confirmed owns the range |
| No buffer / approval fields | Per-amenity `cleanup_buffer_mins`, `requires_approval`, `booking_time_step_mins`, `cancel_before_hours` |

**Deprecate** reliance on `get_available_slots` for Party Hall (and exclusive amenities). Keep calling it only if an old screen still needs a thin adapter — new book flow must use **`get_amenity_day_availability`**.

---

## 2. Screens / UX (hybrid)

### Tenant — Book

1. Amenity detail → Book.  
2. Pick `booking_date` (respect `max_advance_days` / past-date errors from API).  
3. Call **`get_amenity_day_availability`** → render:
   - **Busy strip** from `busy[]` (show `end_with_buffer` as blocked cleanup if buffer > 0)
   - **Free gaps** from `free_gaps[]` — tap a gap to **prefill** Start/End  
4. **Start** / **End** pickers snap to `booking_time_step_mins` (default 30).  
5. Optional `guests_count` + `notes` — guests are **informational only** (no capacity enforcement).  
6. Submit via **`create_amenity_booking`**.  
7. If amenity `requires_approval == 1` → show **Pending approval** messaging; else **Confirmed**.  
8. On overlap error, show conflict window using `conflict_start` / `conflict_end` (and optional `conflict_booking_id`).

### Tenant — My bookings

- List via **`get_my_amenity_bookings`**.  
- Statuses: **Pending**, **Confirmed**, **Completed**, **Cancelled**, **Rejected**, **No Show**.  
- Cancel via **`cancel_amenity_booking`** only when policy allows (tenant: before `cancel_before_hours` of start; staff always).

### Staff

- Pending queue: approve / reject.  
- Busy strip may show tenant **label** (`is_staff` / named `label` on busy items).  
- Actions: **`approve_amenity_booking`**, **`reject_amenity_booking`** (`rejection_reason` required), cancel.

---

## 3. Endpoints (prefer mobile)

| Action | Method |
|---|---|
| Day availability (primary) | `GET\|POST propms.api.mobile.get_amenity_day_availability` |
| Create booking | `POST propms.api.mobile.create_amenity_booking` |
| Cancel | `POST propms.api.mobile.cancel_amenity_booking` |
| Approve (staff) | `POST propms.api.mobile.approve_amenity_booking` |
| Reject (staff) | `POST propms.api.mobile.reject_amenity_booking` |
| My / staff lists | `GET\|POST propms.api.mobile.get_my_amenity_bookings` |

Also usable: `propms.api.amenities.*` top wrappers for the same handlers.

### Deprecated for exclusive booking UI

`get_available_slots` — do **not** drive Party Hall Start/End UX from this.

---

## 4. Day availability response shape

`POST` / `GET` with `amenity`, `booking_date` (YYYY-MM-DD).

```json
{
  "message": {
    "status": "success",
    "amenity": "AMN-0001",
    "amenity_name": "Party Hall",
    "booking_date": "2026-09-28",
    "open_time": "06:00:00",
    "close_time": "22:00:00",
    "booking_time_step_mins": 30,
    "cleanup_buffer_mins": 30,
    "requires_approval": 1,
    "cancel_before_hours": 2,
    "capacity": 50,
    "busy": [
      {
        "booking_id": "AMB-0001",
        "start_time": "18:00:00",
        "end_time": "21:00:00",
        "end_with_buffer": "21:30:00",
        "status": "Pending",
        "label": "Booked"
      }
    ],
    "free_gaps": [
      { "start_time": "06:00:00", "end_time": "18:00:00" },
      { "start_time": "21:30:00", "end_time": "22:00:00" }
    ],
    "is_staff": false
  }
}
```

**Policy fields** drive pickers and copy: step, buffer, approval, cancel window, open/close, capacity (display only).

---

## 5. Create booking + overlap error

Params: `amenity`, `booking_date`, `start_time`, `end_time`, optional `guests_count`, `notes`, `lease`, `property_unit`.

Success → `booking_id`, `doc`, status **Pending** or **Confirmed**.

Overlap (stable shape — surface in UI):

```json
{
  "message": {
    "status": "error",
    "message": "This time overlaps an existing booking (18:00:00–21:00:00).",
    "conflict_booking_id": "AMB-0001",
    "conflict_start": "18:00:00",
    "conflict_end": "21:00:00"
  }
}
```

Server re-validates exclusive overlap including **cleanup buffer after end**. Race: first commit wins; second gets this error.

Other errors (message strings): outside open hours, end ≤ start, times not on step, past / beyond advance, inactive / unpublished amenity.

---

## 6. Staff approve / reject

| Call | Body | Result |
|---|---|---|
| `approve_amenity_booking` | `{ "booking_id": "…" }` | Pending → Confirmed (re-checks overlap) |
| `reject_amenity_booking` | `{ "booking_id": "…", "rejection_reason": "…" }` | Pending → Rejected; frees time |

Staff-only; non-staff → permission error.

---

## 7. Statuses

| Status | Meaning for UI |
|---|---|
| `Pending` | Awaiting staff approval; **blocks** time like Confirmed |
| `Confirmed` | Approved / auto-confirmed |
| `Completed` | Past end (lifecycle) |
| `Cancelled` | Tenant/staff cancel or Pending past start auto-cancel |
| `Rejected` | Staff rejected |
| `No Show` | Staff / lifecycle |

---

## 8. WebSocket / FCM events

Listen and refresh day strip / lists:

| Event | Typical recipient |
|---|---|
| `amenity_booked` | Staff (also aliases `amenity_booking_created`, `new_amenity_booking`) |
| `amenity_booking_cancelled` | Other party (tenant↔staff) |
| `amenity_booking_approved` | Tenant |
| `amenity_booking_rejected` | Tenant (`rejection_reason` in payload) |

Payload includes `type` / `event` / `notification_type`, `booking_id`, amenity, date, times, status, tenant fields, `route` (`/amenity_bookings`). Same pattern as other staff mobile notifications.

---

## 9. Guests

`guests_count` remains on create/detail for display and notes. **Do not** enforce capacity or shared concurrent occupancy in V1. `capacity` on day availability is informational guidance only.

---

## 10. Do / Don’t

**Do**

- Drive book UI from `get_amenity_day_availability`  
- Snap Start/End to `booking_time_step_mins`  
- Treat Pending as blocking busy time  
- Show overlap conflict times from API fields  
- Wire staff approve/reject + WS/FCM refresh  

**Don’t**

- Build Party Hall exclusive booking from `get_available_slots` chips  
- Invent endpoints or client-side-only overlap (server is authoritative)  
- Enforce guest capacity  
- Allow overnight / multi-day / edit-in-place (cancel + rebook only)  

---

## 11. Suggested flow

```
Amenity detail → pick date
  → get_amenity_day_availability
  → busy strip + free_gaps; Start/End (tap gap to prefill)
  → create_amenity_booking
  → Pending or Confirmed messaging
  → on overlap: show conflict_start–conflict_end; reload day

My bookings → get_my_amenity_bookings
  → cancel_amenity_booking when allowed

Staff → Pending queue
  → approve_amenity_booking / reject_amenity_booking
  → listen amenity_booked / cancelled / approved / rejected
```

---

## 12. Acceptance

- [ ] Party Hall book screen uses day availability, not slot chips  
- [ ] Busy strip + Start/End; tap free gap prefills range  
- [ ] Overlap returns and displays `conflict_*` fields  
- [ ] Approval amenity creates Pending; staff approve/reject works  
- [ ] Guests informational only  
- [ ] WS/FCM events refresh lists / strip  
- [ ] Tenant cancel respects `cancel_before_hours`  
