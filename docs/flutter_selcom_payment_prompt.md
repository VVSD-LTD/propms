# 💳 Master AI Directive: Implement Selcom Payment Gateway in Viva Towers Flutter App

> **Target Audience**: Antigravity / Coding Assistant Agent working on the **Viva Towers Mobile App (Flutter)**.  
> **Objective**: Implement the complete, secure **Selcom Payment Gateway** flow for Rent, Utility Bills, and Invoice settlements across all 4 payment rails (Mobile Money USSD Push, 3D-Secure Cards, TanQR, and Hosted Checkout).

---

## 1. Executive Architectural Overview

1. **Security Rule #1**: The Flutter mobile app **NEVER** stores Selcom API keys or computes HMAC signatures. All cryptographic authentication is handled securely on the Frappe backend (`propms`).
2. **Backend URLs**:
   - Production / Staging: `https://dev15-viva2.vvsdtz.com`
   - Facade namespace: `/api/method/propms.api.mobile.<method>`
   - Direct namespace: `/api/method/propms.api.v1.payments.<method>`
3. **Dual Confirmation Loop**:
   - Primary: **WebSocket real-time event** (`payment_completed`) immediately flips UI to Success.
   - Fallback: **Background polling** (`get_payment_status`) every 3–4 seconds for 60 seconds in case socket disconnects.
   - Terminated / Background: **FCM Push Notification** (`invoice_paid`) brings the user back to the receipt screen.

---

## 2. Whitelisted API Endpoints (Request & Response Specifications)

### 🔹 Endpoint 1: `get_payment_methods`
Discovers active payment channels and supported telco networks.

* **Route**: `GET` or `POST` `/api/method/propms.api.mobile.get_payment_methods`
* **Request Headers**: `Authorization: token <key>:<secret>` or Session Cookie
* **Request Body**: *None*
* **Response Body**:
```json
{
  "message": {
    "status": "success",
    "gateway_enabled": true,
    "currency": "TZS",
    "methods": [
      {
        "id": "MOBILE_MONEY",
        "title": "Mobile Money",
        "subtitle": "Instant USSD Push (M-Pesa, Tigo, Airtel, HaloPesa)",
        "icon": "phone_android",
        "enabled": true,
        "providers": [
          {"name": "Vodacom M-Pesa", "code": "MPESA", "prefix": ["074", "075", "076"]},
          {"name": "Mixx by Yas (Tigo)", "code": "TIGO", "prefix": ["071", "065", "067"]},
          {"name": "Airtel Money", "code": "AIRTEL", "prefix": ["068", "069", "078"]},
          {"name": "HaloPesa", "code": "HALOPESA", "prefix": ["062"]}
        ]
      },
      {
        "id": "CARD",
        "title": "Credit / Debit Card",
        "subtitle": "Visa, Mastercard, UnionPay (3D-Secure)",
        "icon": "credit_card",
        "enabled": true,
        "providers": [
          {"name": "Visa", "code": "VISA"},
          {"name": "Mastercard", "code": "MASTERCARD"}
        ]
      },
      {
        "id": "QR_CODE",
        "title": "QR Code (TanQR / Masterpass)",
        "subtitle": "Scan & Pay with any Tanzanian Banking App",
        "icon": "qr_code_scanner",
        "enabled": true,
        "providers": [
          {"name": "TanQR (National Standard)", "code": "TANQR"},
          {"name": "Masterpass QR", "code": "MASTERPASS"}
        ]
      },
      {
        "id": "HOSTED",
        "title": "All Payment Options",
        "subtitle": "Selcom Secure Web Checkout",
        "icon": "language",
        "enabled": true
      }
    ]
  }
}
```

---

### 🔹 Endpoint 2: `initiate_payment`
Starts a payment session and triggers the chosen payment rail.

* **Route**: `POST /api/method/propms.api.mobile.initiate_payment`
* **Request Body Options**:

#### Option A: Mobile Money (Instant USSD Push)
```json
{
  "invoice_name": "ACC-SINV-2026-03739",
  "amount": 169323.0, // Optional: defaults to full invoice balance
  "payment_method": "MOBILE_MONEY",
  "phone_number": "0714000111" // Accepts 07..., 2557..., or +2557...
}
```
* **Success Response (`action: WAIT_FOR_USSD_PIN`)**:
```json
{
  "message": {
    "status": "success",
    "message": "USSD PIN prompt sent to 255714000111. Please enter your PIN on your phone to complete payment.",
    "order_id": "ORD-ACC-SINV-20-A1B2",
    "transaction_id": "TXN-2026-00012",
    "invoice_name": "ACC-SINV-2026-03739",
    "amount": 169323.0,
    "currency": "TZS",
    "payment_method": "MOBILE_MONEY",
    "phone_number": "255714000111",
    "action": "WAIT_FOR_USSD_PIN"
  }
}
```

#### Option B: Card Payment (Visa / Mastercard)
```json
{
  "invoice_name": "ACC-SINV-2026-03739",
  "amount": 169323.0,
  "payment_method": "CARD"
}
```
* **Success Response (`action: OPEN_WEBVIEW`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Payment session initialized. Please complete payment on the secure gateway.",
    "order_id": "ORD-ACC-SINV-20-C3D4",
    "transaction_id": "TXN-2026-00013",
    "invoice_name": "ACC-SINV-2026-03739",
    "amount": 169323.0,
    "currency": "TZS",
    "payment_method": "CARD",
    "gateway_url": "https://checkout.selcommobile.com/pay/...",
    "action": "OPEN_WEBVIEW"
  }
}
```

#### Option C: Dynamic QR Code (TanQR)
```json
{
  "invoice_name": "ACC-SINV-2026-03739",
  "amount": 169323.0,
  "payment_method": "QR_CODE"
}
```
* **Success Response (`action: DISPLAY_QR`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Dynamic QR code generated. Scan with your banking app or M-Pesa.",
    "order_id": "ORD-ACC-SINV-20-E5F6",
    "transaction_id": "TXN-2026-00014",
    "invoice_name": "ACC-SINV-2026-03739",
    "amount": 169323.0,
    "currency": "TZS",
    "payment_method": "QR_CODE",
    "qr_data": "00020101021226500014... (EMVCo / TanQR Payload)",
    "action": "DISPLAY_QR"
  }
}
```

---

### 🔹 Endpoint 3: `get_payment_status`
Queries the live status of an active payment order (used for polling during USSD waiting).

* **Route**: `GET` or `POST` `/api/method/propms.api.mobile.get_payment_status`
* **Request Body / Query Param**: `{"order_id": "ORD-ACC-SINV-20-A1B2"}`
* **Response Body**:
```json
{
  "message": {
    "status": "success",
    "order_id": "ORD-ACC-SINV-20-A1B2",
    "transaction_status": "Success", // "Pending" | "Success" | "Failed" | "Cancelled"
    "invoice_name": "ACC-SINV-2026-03739",
    "amount": 169323.0,
    "currency": "TZS",
    "payment_entry": "RE-2026-00982",
    "selcom_reference": "SEL20260907001928"
  }
}
```

---

### 🔹 Endpoint 4: `cancel_payment`
Cancels an ongoing pending order if the tenant closes the waiting sheet.

* **Route**: `POST /api/method/propms.api.mobile.cancel_payment`
* **Request Body**: `{"order_id": "ORD-ACC-SINV-20-A1B2"}`
* **Response Body**:
```json
{
  "message": {
    "status": "success",
    "message": "Payment order cancelled successfully",
    "order_id": "ORD-ACC-SINV-20-A1B2"
  }
}
```

---

## 3. Real-Time WebSocket Signaling & Push Notifications

### A. WebSocket Event: `payment_completed`
The backend emits this event immediately upon reconciliation.

* **Rooms Broadcasted To**:
  - `doc:Sales Invoice/<invoice_name>` (e.g. `doc:Sales Invoice/ACC-SINV-2026-03739`)
  - `user:<tenant_email>` (e.g. `user:tenant@viva.tz`)
* **Payload**:
```json
{
  "order_id": "ORD-ACC-SINV-20-A1B2",
  "invoice_name": "ACC-SINV-2026-03739",
  "amount": 169323.0,
  "currency": "TZS",
  "status": "PAID",
  "payment_entry": "RE-2026-00982",
  "reference_no": "SEL20260907001928",
  "timestamp": "2026-09-07 10:15:00"
}
```

---

## 4. Flutter UI/UX Best Practices & Implementation Guide

### 📱 Screen 1: Invoice Detail & Pay Bottom Sheet
* Show **Invoice #**, **Billing Period**, and **Outstanding Balance**.
* Provide an amount toggle: **"Pay Full Balance (TZS 169,323)"** vs **"Pay Custom Amount"**.
* Render Payment Rail Radio Cards:
  1. 🟢 **Mobile Money** (Vodacom M-Pesa, Yas Tigo Pesa, Airtel Money, HaloPesa)
  2. 💳 **Card** (Visa, Mastercard with 3D-Secure badge)
  3. 🔲 **QR Code** (TanQR / Bank App scan)
  4. 🌐 **Other / Hosted Portal**

---

### 📱 Screen 2: Rail-Specific Interactive Flows

#### 1. If `MOBILE_MONEY`:
1. Auto-fill phone from user's tenant profile; show provider badge based on prefix (`071` &rarr; Tigo, `075` &rarr; Vodacom, `078` &rarr; Airtel, `062` &rarr; HaloPesa).
2. Call `initiate_payment`.
3. Pop open **"Waiting for PIN" Modal**:
   - Circular countdown progress bar (60 seconds).
   - Text: *"Please check your phone. Enter your Mobile Money PIN when prompted by Vodacom/Tigo/Airtel."*
   - Subscribe to WebSocket `payment_completed`.
   - Run a `Timer.periodic(Duration(seconds: 3))` calling `get_payment_status(order_id)`.
   - If user taps "Cancel", call `cancel_payment(order_id)` and dismiss modal.

#### 2. If `CARD` or `HOSTED`:
1. Call `initiate_payment`.
2. Open in-app `WebViewWidget` with `gateway_url`.
3. Set `NavigationDelegate` on the WebView:
   - When URL contains `payment_success` or WebSocket `payment_completed` fires:
     - Pop WebView.
     - Navigate to `PaymentCelebrationScreen`.

#### 3. If `QR_CODE`:
1. Call `initiate_payment`.
2. Render QR image using `QrImageView.withQr(data: qr_data)`.
3. Show 15-minute countdown timer.
4. Provide action buttons:
   - **"Save QR to Gallery"** / **"Share"**.
   - **"Copy Payment Reference"** (copies `order_id` to clipboard).
5. Listen for WebSocket `payment_completed` to automatically dismiss QR when paid.

---

### 📱 Screen 3: Payment Celebration & Receipt Screen
* Animated green checkmark / celebration.
* Card displaying:
  - **Amount Paid**: `TZS 169,323`
  - **Invoice #**: `ACC-SINV-2026-03739`
  - **Receipt Voucher**: `RE-2026-00982`
  - **Date & Time**: `07 Sep 2026, 10:15 AM`
* Action Buttons:
  - **"Download Official Receipt (PDF)"**
  - **"Back to Home / Invoices"** (triggers state refresh on invoice list provider).

---

## 5. Verification Checklist for Flutter AI
- [ ] Phone number normalizer converts `07XXXXXXXX` to `2557XXXXXXXX` before API call.
- [ ] Mobile Money waiting sheet polls every 3s and cancels timer upon WebSocket event.
- [ ] 3DS Card WebView interceptor closes cleanly upon successful payment.
- [ ] TanQR renders high-contrast QR matrix scannable by banking apps.
- [ ] Upon success, invoice list state invalidates and flips invoice badge from `Unpaid` to `Paid`.
- [ ] `flutter analyze` passes with 0 errors.
