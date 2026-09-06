# 🚀 Master AI Directive: Align Viva Towers Mobile App (Flutter) with Production-Tested VSD Helpdesk

> **Target Audience**: Antigravity / Coding Assistant Agent working on the Flutter Mobile App Workspace.  
> **Objective**: Upgrade and align the **Job Card / Ticket** feature module in the **Viva Towers Flutter App** using the battle-tested, production-stable code from the **VSD Helpdesk Flutter App** without breaking any Viva-specific features.

---

## 1. Executive Context & The Problem

1. **The Origin**:
   * The **VSD Helpdesk App** is our flagship client support platform, currently **live in production on both Google Play Store and Apple App Store**.
   * It underwent extensive real-world testing and dozens of live bug fixes (handling network timeouts, iOS background push wakeups, WebSocket reconnects, @mentions, message editing, quoted replies, and chunked photo uploads).
2. **The Fork Drift**:
   * When Viva Towers initially requested a building management app, we cloned the early Helpdesk codebase.
   * Viva Towers temporarily paused the project, during which we continued perfecting the Helpdesk app into its current rock-solid state.
3. **The Goal Now**:
   * Viva Towers has resumed the project.
   * **Do NOT debug or reinvent the Job Card module in Viva from scratch.**
   * Transplant the battle-tested Ticket/JobCard logic, UI, WebSocket listeners, FCM background handlers, and state management directly from **`vsd_helpdesk` Flutter** to **`viva` Flutter**.

---

## 2. What Was Just Aligned & Fixed on the Frappe Backend

The Frappe backend (`apps/propms`) has already been completely upgraded and synced with `vsd_helpdesk`. It now outputs identical JSON contracts and WebSocket events:

### A. Realtime WebSocket Events
The backend emits dual event names for 100% backward and forward compatibility:
| Event Name | Trigger | Payload Structure |
| :--- | :--- | :--- |
| **`ticket_message`** & **`new_message`** | New message sent in chat | `{"ticket_id": "ISS-...", "communication": {...}, "sender_type": "...", "is_internal": false}` |
| **`ticket_typing`** & **`typing`** | User is typing indicator | `{"ticket_id": "ISS-...", "user": "...", "user_full_name": "...", "is_typing": true}` |
| **`ticket_mention`** | User @mentioned in message | `{"ticket_id": "ISS-...", "message_idx": 5, "mentioned_emails": ["..."]}` |
| **`ticket_message_edited`** & **`ticket_communication_edited`** | Message edited (15-min window) | `{"ticket_id": "ISS-...", "communication": {"idx": 5, "message_content": "...", "is_edited": 1}}` |
| **`ticket_status_changed`**, **`ticket_updated`**, **`ticket_update`** | Status changed (e.g. Open -> In Progress -> Resolved) | `{"ticket_id": "ISS-...", "status": "In Progress", "previous_status": "Open", "changed_by": "..."}` |

### B. Firebase FCM Push Notification Schema
All push notifications are sent via Firebase Admin SDK v1 with APNS alert headers for iOS background/terminated wakeups:
```json
{
  "notification": {
    "title": "New message",
    "body": "John Doe: Please inspect the AC unit"
  },
  "data": {
    "type": "ticket_message", // or "mention", "ticket_message_edited", "ticket_status_changed", "ticket_assigned"
    "ticket_id": "ISS-2026-00001",
    "message_idx": "14",
    "user": "tenant@viva.tz",
    "click_action": "FLUTTER_NOTIFICATION_CLICK"
  }
}
```

### C. Available Whitelisted API Routes
All endpoints are available under both `propms.api.mobile.*` and `propms.api.v1.job_card.job_card.*`:
* **Chat & Messaging**:
  * `send_ticket_communication(ticket_id, message_content, attachment, reply_to_idx, mentioned_emails, channel)`
  * `edit_ticket_communication(ticket_id, communication_idx, new_message_content, mentioned_emails)`
  * `mark_communications_as_read(ticket_id, communication_indices)`
  * `send_typing_indicator(ticket_id, is_typing)`
  * `get_ticket_communications(ticket_id, limit, offset, channel)`
  * `get_mentionable_support_staff(ticket_id)`
  * `get_user_tickets(status, priority, limit, offset, search)`
* **Status Updates (Officers & Managers)**:
  * `change_ticket_status(ticket_id, new_status, comment)` - Supports `Open`, `In Progress`, `On Hold`, `Resolved`, `Closed`, `Cancelled`.
  * `put_ticket_on_hold(ticket_id, reason)`
  * `resume_ticket_from_hold(ticket_id, comment)`
  * `resolve_ticket(ticket_id, resolution_notes)`
  * `close_ticket_with_feedback(ticket_id, feedback, rating)`
* **Chunked File & Photo Uploads**:
  * `start_upload_session(file_name, total_chunks, total_size, file_type)`
  * `upload_chunk(session_id, chunk_index, chunk_data)`
  * `finalize_upload(session_id)`
  * `abort_upload_session(session_id)`

---

## 3. Step-by-Step Instructions for the Flutter AI

### Step 1: Side-by-Side Comparison
Compare the feature folders between both Flutter codebases:
1. `vsd_helpdesk` (Source of Truth): `lib/features/tickets/` (or `job_cards/`)
2. `viva` (Target): `lib/features/job_cards/` (or `tickets/`)

### Step 2: Port Core Utilities & Chat Engine
Transplant the following components from `vsd_helpdesk` to `viva`:
1. **Chat Screen & Message Bubbles**:
   * Quoted reply banner above text input (`reply_to_idx`, `quoted_sender`, `quoted_content`) + swipe-to-reply gesture.
   * Message status indicators (Single tick = Sent, Double tick = Delivered, Blue double tick = Read, Pencil icon = Edited).
   * @Mention dropdown picker showing mentionable officers/technicians with avatars when `@` is typed.
   * Message edit modal with 15-minute validity window.
2. **WebSocket Realtime Service**:
   * Socket event listeners for `ticket_message` / `new_message`, `ticket_typing` / `typing`, `ticket_mention`, and `ticket_communication_edited`.
   * Typing debouncer (stop typing after 3 seconds of inactivity).
3. **Chunked File & Photo Upload Manager**:
   * Replace single base64 image uploads with the chunked upload controller (`start_upload_session` &rarr; 2MB chunks &rarr; `finalize_upload`) to prevent network timeouts when taking high-res photos.
4. **Push Notification Navigation Interceptor**:
   * When user taps an FCM notification with `type: "ticket_message"` or `type: "mention"`, route directly to the Job Card detail screen and scroll to `message_idx`.

---

## 4. ⚠️ CRITICAL CONSTRAINTS (What NOT to Touch)

Viva Towers is a comprehensive property management solution. **You MUST preserve and NOT break any Viva-specific modules**:
* 💳 **Selcom Payment Gateway**: Invoices, payment bottom sheet, USSD push status polling, M-Pesa / Tigo Pesa integration.
* 🏊 **Amenities Booking**: Slots picker, calendar, booking reservations.
* 📞 **LiveKit Video / Audio Calling**: Calling screen, token exchange, WebRTC connection.
* 🚨 **Emergency Incidents**: Emergency SOS reporting, building security dispatch.
* 📇 **Building Directory**: Resident contacts, management office directory.
* 🎫 **Visitor Gate Passes**: QR code generation and visitor check-in passes.

---

## 5. Verification Checklist

Before finishing, ensure:
- [ ] Job Card chat receives messages in real-time over WebSockets without manual pull-to-refresh.
- [ ] Typing indicator displays when the other party types.
- [ ] @Mentions display with colored chips and dispatch notifications.
- [ ] Quoted replies render the parent message preview correctly.
- [ ] Message edits update the bubble in-place and show "(edited)".
- [ ] Taking a camera photo uploads via chunked sessions with progress bar.
- [ ] `flutter analyze` passes with 0 critical errors.
