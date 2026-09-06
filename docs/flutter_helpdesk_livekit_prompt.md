# 🚀 Master AI Directive: Implement LiveKit Audio & Video Calling in VSD Helpdesk Flutter App

> **Target Audience**: Antigravity / Coding Assistant Agent working on the **VSD Helpdesk Flutter Mobile App**.  
> **Objective**: Implement **Audio & Video Calling** (powered by LiveKit) into the **Ticket Chat Room Header** and 1-on-1 direct calls using the new modular backend API.

---

## 1. Executive Overview

We have implemented a **fully modular, real-time LiveKit Audio & Video Calling system** on the Frappe Helpdesk backend (`apps/vsd_helpdesk`).
Now, we need the Flutter mobile app to:
1. Add **Voice Call (📞)** and **Video Call (📹)** action buttons to the **Ticket Chat Room Header / AppBar**.
2. Tap the button &rarr; call the assigned support engineer (if caller is customer) or call the customer (if caller is support engineer).
3. Connect via LiveKit WebRTC, render audio/video tracks, and handle ringing, answering, muting, camera flip, and ending.
4. Listen for incoming calls via **WebSockets** (`incoming_call` / `ticket_call_incoming`) and **FCM Push Notifications**.

---

## 2. Backend Architecture & API Specifications

All endpoints are whitelisted under both `vsd_helpdesk.api.mobile.*` and `vsd_helpdesk.api.calls.*`:

### A. Whitelisted Endpoints

| Endpoint | Method | Params | Description |
| :--- | :--- | :--- | :--- |
| **`initiate_ticket_call`** | POST | `ticket_id`, `call_type` (`"Voice"` or `"Video"`) | **Primary Ticket Room Call Action**. Auto-detects participants (customer calls assigned staff; staff calls customer). Creates call log, generates LiveKit room & JWT token, emits websocket signaling, and enqueues FCM push. |
| **`initiate_call`** | POST | `receiver`, `call_type`, `ticket_id` (optional) | 1-on-1 direct call to any user/email. |
| **`answer_call`** | POST | `call_id` | Accept incoming call, marks Answered, generates receiver LiveKit token, emits `call_answered`. |
| **`end_call`** | POST | `call_id`, `reason` (`"ended"`, `"declined"`, `"missed"`) | Terminates call, computes duration, emits `call_ended`. |
| **`get_call_config`** | GET/POST | _none_ | Returns `{"enabled": true, "server_url": "wss://..."}`. |
| **`get_ticket_call_history`**| GET/POST | `ticket_id` | Returns call logs linked to this ticket. |

---

### B. Real-time WebSocket Signaling Events

Your WebSocket client should listen for these events:

1. **`incoming_call` / `ticket_call_incoming`**:
   ```json
   {
     "event": "incoming_call",
     "call_id": "HCL-2026-00001",
     "room_name": "ticket_call_TIC-001_HCL-2026-00001",
     "call_type": "Voice", // or "Video"
     "ticket_id": "TIC-001",
     "caller": "john@client.com",
     "caller_name": "John Doe",
     "caller_role": "Customer User",
     "server_url": "wss://livekit.vvsdtz.com",
     "started_at": "2026-09-06 23:50:00"
   }
   ```
2. **`call_answered` / `ticket_call_answered`**:
   ```json
   {
     "event": "call_answered",
     "call_id": "HCL-2026-00001",
     "answered_by": "support@vvsdtz.com",
     "connected_at": "2026-09-06 23:50:05",
     "status": "Answered"
   }
   ```
3. **`call_ended` / `ticket_call_ended`**:
   ```json
   {
     "event": "call_ended",
     "call_id": "HCL-2026-00001",
     "status": "Ended", // or "Declined", "Missed"
     "ended_by": "john@client.com",
     "duration_seconds": 125,
     "reason": "ended"
   }
   ```

---

### C. FCM Push Notification Payload (Background / Terminated Wakeup)

```json
{
  "notification": {
    "title": "📞 Incoming Voice Call",
    "body": "John Doe is calling you regarding Ticket #TIC-001..."
  },
  "data": {
    "type": "incoming_call",
    "call_id": "HCL-2026-00001",
    "room_name": "ticket_call_TIC-001_HCL-2026-00001",
    "call_type": "Voice",
    "ticket_id": "TIC-001",
    "caller_name": "John Doe",
    "server_url": "wss://livekit.vvsdtz.com",
    "click_action": "FLUTTER_NOTIFICATION_CLICK"
  }
}
```

---

## 3. Flutter Implementation Instructions

### Step 1: Dependencies Check
Ensure `livekit_client` (e.g. `^2.1.0` or latest compatible) is included in `pubspec.yaml`, along with permission handlers for microphone and camera:
* `livekit_client`
* `permission_handler`

### Step 2: Ticket Room Header Action Buttons
In your Ticket Chat Screen (`ticket_chat_screen.dart` / `ticket_detail_screen.dart`):
1. In the `AppBar` actions list, add:
   * **Voice Call Button**: `IconButton(icon: Icon(Icons.phone), onPressed: () => _startCall(ticketId, "Voice"))`
   * **Video Call Button**: `IconButton(icon: Icon(Icons.videocam), onPressed: () => _startCall(ticketId, "Video"))`
2. When tapped:
   * Call `/api/method/vsd_helpdesk.api.mobile.initiate_ticket_call` with `{"ticket_id": ticketId, "call_type": callType}`.
   * On success: navigate to `ActiveCallScreen(callId: res['call_id'], roomName: res['room_name'], token: res['token'], serverUrl: res['server_url'], callType: callType, isCaller: true)`.

### Step 3: Incoming Call Overlay & Answering
1. When WebSocket receives `incoming_call` or app launches from FCM call push:
   * Display `IncomingCallDialog` / `IncomingCallScreen` (Caller name, Avatar, Ticket ID, "Accept" (green) & "Decline" (red)).
2. If "Accept":
   * Call `/api/method/vsd_helpdesk.api.mobile.answer_call` with `{"call_id": callId}`.
   * Obtain `token` and `server_url` from response.
   * Navigate to `ActiveCallScreen` with `isCaller: false`.
3. If "Decline":
   * Call `/api/method/vsd_helpdesk.api.mobile.end_call` with `{"call_id": callId, "reason": "declined"}`.

### Step 4: Active Call Screen & LiveKit Connection
Create a modular call screen (e.g. `lib/features/calls/screens/active_call_screen.dart`):
1. **Connect to Room**:
   ```dart
   final room = Room();
   final listener = room.createListener();
   await room.connect(serverUrl, token);
   await room.localParticipant?.setMicrophoneEnabled(true);
   if (callType == "Video") {
     await room.localParticipant?.setCameraEnabled(true);
   }
   ```
2. **UI Elements**:
   * Remote participant video renderer (`VideoTrackRenderer` for remote video track).
   * Local preview video renderer in picture-in-picture floating overlay.
   * Call duration timer (00:00).
   * Controls bar: Mute/Unmute Mic, Switch Speaker/Earpiece, Toggle Camera (if Video), Flip Camera, End Call (red hang-up button).
3. **End Call Action**:
   * Call `/api/method/vsd_helpdesk.api.mobile.end_call` with `{"call_id": callId, "reason": "ended"}`.
   * `room.disconnect()`, `room.dispose()`.
   * Pop back to Ticket screen.

---

## 4. Verification Checklist
- [ ] Tapping the Phone icon on a ticket room header dials the assigned staff/customer and generates LiveKit token.
- [ ] The recipient receives `incoming_call` real-time event and full-screen / popup ringing UI.
- [ ] Answering connects both parties with crystal-clear audio (and video if enabled).
- [ ] Ending the call from either party hangs up the LiveKit room, stops audio tracks, and closes the screen.
- [ ] Call log is recorded with exact duration under `Helpdesk Call Log`.
