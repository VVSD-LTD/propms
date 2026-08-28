import frappe
import json
from threading import Lock

_init_lock = Lock()
_initialized = False


def _ensure_initialized():
    global _initialized
    if _initialized:
        return None
    with _init_lock:
        if _initialized:
            return None
        try:
            import firebase_admin
            from firebase_admin import credentials

            # Load service account from Mobile App Settings (fcm_server_key JSON field)
            try:
                settings = frappe.get_single("Mobile App Settings")
                raw_value = getattr(settings, "fcm_server_key", None)
            except Exception:
                raw_value = None

            if not raw_value:
                raise Exception(
                    "Firebase service account not configured "
                    "(set Mobile App Settings.fcm_server_key JSON)"
                )

            # raw_value may be a JSON string or already a dict
            if isinstance(raw_value, str):
                try:
                    sa_obj = json.loads(raw_value)
                except Exception as e:
                    frappe.log_error(f"Failed to parse FCM server key JSON: {str(e)}", "FCM Config Error")
                    raise Exception(f"Invalid JSON in fcm_server_key: {str(e)}")
            else:
                sa_obj = raw_value

            cred = credentials.Certificate(sa_obj)

            firebase_admin.initialize_app(cred)
            _initialized = True
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"FCM init error: {e}")
            raise


def send_to_tokens(tokens, data=None, title=None, body=None):
    """Send push notifications to a list of device tokens.

    tokens: list[str]
    data: dict -> values will be stringified
    title/body: optional notification
    
    CRITICAL iOS REQUIREMENT: iOS terminated apps REQUIRE notification payload.
    Data-only messages will NOT be delivered when app is terminated.
    """
    if not tokens:
        return {"success": 0, "failure": 0}

    _ensure_initialized()

    # Import here to avoid import cost before init
    from firebase_admin import messaging

    success_count = 0
    failure_count = 0
    
    # Log incoming data parameter
    frappe.logger().info(f"📤 FCM send_to_tokens: Received data parameter: {data}")
    frappe.logger().info(f"📤 FCM send_to_tokens: Data type: {type(data)}")
    frappe.logger().info(f"📤 FCM send_to_tokens: Data is None: {data is None}")
    frappe.logger().info(f"📤 FCM send_to_tokens: Data is empty dict: {data == {}}")
    
    # Prepare stringified data payload (for both Android and iOS)
    # CRITICAL: All values must be strings for FCM data payload
    fcm_data = {}
    if data:
        fcm_data = {k: str(v) if v is not None else "" for k, v in data.items()}
    
    frappe.logger().info(f"📤 FCM send_to_tokens: Prepared fcm_data: {fcm_data}")
    frappe.logger().info(f"📤 FCM send_to_tokens: fcm_data keys: {list(fcm_data.keys())}")
    frappe.logger().info(f"📤 FCM send_to_tokens: fcm_data values: {list(fcm_data.values())}")
    
    # CRITICAL: iOS terminated apps REQUIRE notification payload (not data-only)
    # Ensure we always have title/body for notification, even if empty
    # This prevents data-only messages that iOS will reject when terminated
    notification_title = title or "Notification"
    notification_body = body or "You have a new message"
    
    # Send individual messages instead of multicast to avoid 404 error
    for token in tokens:
        try:
            # CRITICAL: Always create notification payload (required for iOS terminated apps)
            # iOS will NOT deliver data-only messages when app is terminated
            notification = messaging.Notification(
                title=notification_title,
                body=notification_body
            )

            # Configure APNS for iOS - CRITICAL: iOS requires alert in APNS payload to display notifications
            # Build APNS alert payload for iOS
            # CRITICAL: iOS terminated apps REQUIRE both notification AND alert in APNS payload
            apns_alert = messaging.ApsAlert(
                title=notification_title,
                body=notification_body
            )
            
            # CRITICAL FIX for iOS terminated apps:
            # 1. Must have notification payload (not data-only) ✅
            # 2. Must have alert in APNS payload ✅
            # 3. Must have content_available=1 (integer) ✅
            # 4. Must have apns-priority: "10" ✅
            # 5. Must have apns-push-type: "alert" ✅
            apns_config = messaging.APNSConfig(
                headers={
                    "apns-priority": "10",  # High priority for immediate delivery (required for background)
                    "apns-push-type": "alert",  # Explicit push type (required)
                },
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=apns_alert,  # ✅ CRITICAL: iOS needs this to display notification
                        sound="default",
                        content_available=1,  # CRITICAL: Integer 1 (not True) - enables terminated app wake
                        badge=None,  # Optional: can be set to notification count
                    ),
                    # Note: Firebase Admin SDK automatically includes 'data' fields in APNS payload
                    # as custom fields at the root level of the payload
                ),
            )
            
            # Validate data payload is not empty
            if not fcm_data:
                frappe.logger().warning(f"⚠️ FCM WARNING: fcm_data is empty! This will cause Flutter app to receive empty data payload.")
                frappe.logger().warning(f"⚠️ FCM WARNING: Original data parameter was: {data}")
            
            # Build message with BOTH notification and data payloads
            # CRITICAL: iOS terminated apps REQUIRE notification payload (not data-only)
            # CRITICAL: Android terminated apps REQUIRE data payload for background handling
            message = messaging.Message(
                notification=notification,  # ✅ ALWAYS present (required for iOS terminated apps)
                data=fcm_data,  # FCM data - Firebase automatically maps this to APNS custom fields
                token=token,
                android=messaging.AndroidConfig(
                    priority="high",
                    # CRITICAL: Ensure data payload is included for Android terminated apps
                    # AndroidConfig doesn't need explicit data field - it's included automatically
                ),
                apns=apns_config,
            )
            
            # Log the actual message structure before sending
            frappe.logger().info(f"📤 FCM Message Structure:")
            frappe.logger().info(f"   - Notification: title='{notification_title}', body='{notification_body}'")
            frappe.logger().info(f"   - Data payload: {fcm_data}")
            frappe.logger().info(f"   - Data payload count: {len(fcm_data)}")
            frappe.logger().info(f"   - Data payload items: {list(fcm_data.items())}")
            frappe.logger().info(f"   - Token: {token[:20]}...")
            
            # Verify message.data is set correctly
            if hasattr(message, 'data'):
                frappe.logger().info(f"   - Message.data attribute: {message.data}")
            else:
                frappe.logger().warning(f"⚠️ FCM WARNING: Message object does not have 'data' attribute!")

            # Log payload structure for debugging (truncate token for security)
            payload_log = {
                "notification": {
                    "title": notification_title,
                    "body": notification_body
                },
                "data": fcm_data,
                "token": token[:20] + "...",
                "android": {"priority": "high"},
                "apns": {
                    "headers": {"apns-priority": "10", "apns-push-type": "alert"},
                    "payload": {
                        "aps": {
                            "alert": {"title": notification_title, "body": notification_body},
                            "sound": "default",
                            "content-available": 1,
                        },
                        # Firebase Admin SDK will add fcm_data fields here as custom fields
                        "custom_data_fields": list(fcm_data.keys()) if fcm_data else []
                    }
                }
            }
            frappe.logger().info(f"📤 FCM Payload Structure (iOS terminated-safe): {json.dumps(payload_log, indent=2)}")

            response = messaging.send(message)
            success_count += 1
            frappe.logger().info(f"✅ FCM sent successfully: {response}")
            
        except Exception as e:
            failure_count += 1
            frappe.logger().error(f"❌ FCM send failed for token {token[:20]}...: {str(e)}")
    
    return {"success": success_count, "failure": failure_count}


