# 💳 Master AI Directive: Implement Selcom Payment Gateway in Viva Towers Flutter App

> **Target Audience**: AI Coding Assistant / Developer working on the **Viva Towers Mobile App (Flutter)**.  
> **Objective**: Implement the complete, secure **Selcom Payment Gateway** flow for Rent, Utility Bills, and Invoice settlements across all payment rails (Mobile Money USSD Push, New & Saved Credit/Debit Cards, TanQR Code, and Hosted Checkout).

---

## 1. Executive Architecture & Action-Driven State Machine

1. **Security Rule #1**: The Flutter mobile app **NEVER** stores Selcom API keys, computes HMAC signatures, or captures raw 16-digit credit card numbers. All cryptographic authentication and PCI compliance are handled securely by the Frappe backend (`propms`).
2. **Backend Base URL**: `https://dev15-viva2.vvsdtz.com`
   - Facade namespace: `/api/method/propms.api.mobile.<method>`
   - Direct namespace: `/api/method/propms.api.v1.payments.<method>`
3. **Action-Driven UI State Machine**:
   The backend API returns an explicit `action` string in every response. Your Flutter code MUST route UI state using this single switch block:

```dart
void handlePaymentAction(BuildContext context, Map<String, dynamic> response) {
  final action = response['action'];
  final orderId = response['order_id'];
  final gatewayUrl = response['gateway_url'];

  switch (action) {
    case 'OPEN_WEBVIEW':
      // Open in-app WebView for Selcom Hosted Checkout (New Card / Hosted)
      openSelcomWebView(context, gatewayUrl: gatewayUrl, orderId: orderId);
      break;

    case 'OPEN_3DS_WEBVIEW':
      // Open in-app WebView ONLY for 3D-Secure bank OTP challenge
      openSelcomWebView(context, gatewayUrl: gatewayUrl, orderId: orderId);
      break;

    case 'PAYMENT_COMPLETED':
      // Direct 1-Tap payment succeeded (Saved card charge or immediate reconciliation)
      showPaymentSuccessDialog(context, orderId: orderId);
      break;

    case 'WAIT_FOR_USSD_PIN':
      // Display USSD PIN prompt waiting dialog and start polling / socket listener
      showWaitingForPinModal(context, orderId: orderId, phone: response['phone_number']);
      break;

    case 'DISPLAY_QR':
      // Display dynamic TanQR image and 8-digit payment token
      showQrCodeModal(context, qrData: response['qr_data'], token: response['payment_token'], orderId: orderId);
      break;

    default:
      verifyPaymentStatus(context, orderId: orderId);
  }
}
```

---

## 2. API Endpoint Specifications

### 🔹 Endpoint 1: `get_payment_methods`
Discovers active payment channels.
* **Route**: `GET` or `POST` `/api/method/propms.api.mobile.get_payment_methods`
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
        "enabled": true
      },
      {
        "id": "CARD",
        "title": "Credit / Debit Card",
        "subtitle": "Visa, Mastercard, UnionPay (3D-Secure)",
        "icon": "credit_card",
        "enabled": true
      },
      {
        "id": "QR_CODE",
        "title": "QR Code (TanQR / Masterpass)",
        "subtitle": "Scan & Pay with any Tanzanian Banking App",
        "icon": "qr_code_scanner",
        "enabled": true
      }
    ]
  }
}
```

---

### 🔹 Endpoint 2: `get_stored_cards`
Retrieves list of tokenized cards saved for the logged-in tenant.
* **Route**: `GET` or `POST` `/api/method/propms.api.mobile.get_stored_cards`
* **Response Body**:
```json
{
  "message": {
    "status": "success",
    "buyer_userid": "tenant@vvsdtz.com",
    "cards": [
      {
        "card_token": "TOK-VISA-991823",
        "masked_card": "4111-XXXX-XXXX-1111",
        "card_brand": "Visa",
        "expiry": "12/28"
      }
    ]
  }
}
```

---

### 🔹 Endpoint 3: `initiate_payment`
Starts payment for New Card, Mobile Money, or QR Code.
* **Route**: `POST /api/method/propms.api.mobile.initiate_payment`

#### Option A: Mobile Money (Instant USSD Push)
* **Request**:
```json
{
  "invoice_name": "ACC-SINV-2026-04028",
  "payment_method": "MOBILE_MONEY",
  "phone_number": "0779961780"
}
```
* **Response (`action: WAIT_FOR_USSD_PIN`)**:
```json
{
  "message": {
    "status": "success",
    "message": "USSD PIN prompt sent to 255779961780. Please enter your PIN on your phone.",
    "order_id": "ORD-ACCSINV20260-A75CAD",
    "transaction_id": "TXN-2026-194333",
    "phone_number": "255779961780",
    "action": "WAIT_FOR_USSD_PIN"
  }
}
```

#### Option B: New Credit / Debit Card
* **Request**:
```json
{
  "invoice_name": "ACC-SINV-2026-04028",
  "payment_method": "CARD"
}
```
* **Response (`action: OPEN_WEBVIEW`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Payment session initialized. Please complete payment on the secure gateway.",
    "order_id": "ORD-ACCSINV20260-3285C0",
    "gateway_url": "https://tza.selcom.online/paymentgw/checkout/...",
    "action": "OPEN_WEBVIEW"
  }
}
```

#### Option C: Dynamic TanQR Code
* **Request**:
```json
{
  "invoice_name": "ACC-SINV-2026-04028",
  "payment_method": "QR_CODE"
}
```
* **Response (`action: DISPLAY_QR`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Dynamic TanQR code generated.",
    "order_id": "ORD-ACCSINV20260-99A82B",
    "qr_data": "0002010102120415...",
    "payment_token": "63830950",
    "action": "DISPLAY_QR"
  }
}
```

---

### 🔹 Endpoint 4: `pay_with_stored_card` (1-Tap Fast Checkout)
Charges a saved/tokenized card directly via API **WITHOUT WEBVIEW** (unless 3DS challenge is required by issuing bank).
* **Route**: `POST /api/method/propms.api.mobile.pay_with_stored_card`
* **Request**:
```json
{
  "invoice_name": "ACC-SINV-2026-04028",
  "card_token": "TOK-VISA-991823"
}
```
* **Frictionless Response (`action: PAYMENT_COMPLETED`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Card payment processed successfully",
    "order_id": "ORD-ACCSINV20260-771122",
    "payment_entry": "RE-2026-00994",
    "action": "PAYMENT_COMPLETED"
  }
}
```
* **3DS Challenge Response (`action: OPEN_3DS_WEBVIEW`)**:
```json
{
  "message": {
    "status": "success",
    "message": "Please complete 3D-Secure authentication",
    "order_id": "ORD-ACCSINV20260-771122",
    "gateway_url": "https://tza.selcom.online/...",
    "action": "OPEN_3DS_WEBVIEW"
  }
}
```

---

### 🔹 Endpoint 5: `get_payment_status` (Post-WebView Verification Fallback)
Queries authoritative payment status from backend/Selcom.
* **Route**: `GET` or `POST` `/api/method/propms.api.mobile.get_payment_status`
* **Request**:
```json
{
  "order_id": "ORD-ACCSINV20260-A75CAD"
}
```
* **Response**:
```json
{
  "message": {
    "status": "success",
    "order_id": "ORD-ACCSINV20260-A75CAD",
    "transaction_status": "Success",
    "payment_entry": "RE-2026-00994",
    "selcom_reference": "7888760241246998704163"
  }
}
```

---

## 3. Post-WebView Verification Rule (CRITICAL)

When the WebView closes or redirects to `/payment-success` or `/payment-cancel`:
1. **NEVER** assume the payment succeeded just because the WebView URL changed!
2. Flutter MUST immediately call `get_payment_status(order_id)` to verify that `transaction_status == "Success"` and `payment_entry` has been posted.

```dart
Future<void> onWebViewClosed(BuildContext context, String orderId) async {
  showLoadingDialog(context, 'Verifying payment status...');
  final statusResponse = await paymentRepository.getPaymentStatus(orderId);
  
  if (statusResponse['transaction_status'] == 'Success') {
    showPaymentReceiptScreen(context, statusResponse);
  } else {
    showPaymentFailedDialog(context, statusResponse['message']);
  }
}
```

---

## 4. UI / UX Design Specifications

1. **Checkout Method Selection Screen**:
   - List enabled methods (`Mobile Money`, `Credit / Debit Card`, `TanQR`).
   - If `get_stored_cards` returns saved cards, display them at the top:
     ```
     Saved Cards
     ──────────────────────────────
     💳 Visa ending in 1111 (Exp: 12/28)
     ──────────────────────────────
     ```
   - Below saved cards, provide `[ + Add New Card ]`.

2. **Card Payment UX**:
   - **Saved Card Selected**: Tapping **Pay** calls `pay_with_stored_card`. No WebView opens (unless 3DS is required). Payment completes in 1–2 seconds.
   - **New Card Selected**: Tapping **Pay** calls `initiate_payment(CARD)`. Opens in-app WebView for Selcom Hosted Checkout.

3. **Real-time Dual Confirmation**:
   - **Primary**: Socket.io / WebSocket event `payment_completed` auto-dismisses waiting dialogs and opens the Receipt Screen.
   - **Fallback**: Background polling timer calling `get_payment_status(order_id)` every 3 seconds for up to 60 seconds.
