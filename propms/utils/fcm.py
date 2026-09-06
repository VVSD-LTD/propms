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

            # Load service account JSON from Mobile App Settings (or Helpdesk Settings as fallback)
            raw_value = None
            try:
                if frappe.db.exists("DocType", "Mobile App Settings"):
                    settings = frappe.get_single("Mobile App Settings")
                    raw_value = getattr(settings, "fcm_server_key", None)
            except Exception:
                raw_value = None

            if not raw_value:
                try:
                    if frappe.db.exists("DocType", "Helpdesk Settings"):
                        hd_settings = frappe.get_single("Helpdesk Settings")
                        raw_value = getattr(hd_settings, "fcm_server_key", None)
                except Exception:
                    raw_value = None

            if not raw_value:
                raise Exception("Firebase service account not configured (set Mobile App Settings.fcm_server_key JSON)")

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

    from firebase_admin import messaging

    success_count = 0
    failure_count = 0
    
    # Prepare stringified data payload (for both Android and iOS)
    fcm_data = {k: str(v) for k, v in (data or {}).items()}
    
    # CRITICAL: iOS terminated apps REQUIRE notification payload (not data-only)
    notification_title = title or "Notification"
    notification_body = body or "You have a new message"
    
    # Send individual messages instead of multicast to avoid 404 batch error
    for token in tokens:
        try:
            notification = messaging.Notification(
                title=notification_title,
                body=notification_body
            )

            # Configure APNS for iOS
            apns_alert = messaging.ApsAlert(
                title=notification_title,
                body=notification_body
            )
            
            apns_config = messaging.APNSConfig(
                headers={
                    "apns-priority": "10",  # High priority for immediate delivery
                    "apns-push-type": "alert",  # Explicit push type
                },
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=apns_alert,
                        sound="default",
                        content_available=1,  # Enables background/terminated app wake
                        badge=None,
                    ),
                ),
            )
            
            message = messaging.Message(
                notification=notification,
                data=fcm_data,
                token=token,
                android=messaging.AndroidConfig(priority="high"),
                apns=apns_config,
            )

            response = messaging.send(message)
            success_count += 1
            frappe.logger().info(f"✅ FCM sent successfully to token {token[:16]}...: {response}")
            
        except Exception as e:
            failure_count += 1
            frappe.logger().error(f"❌ FCM send failed for token {token[:16]}...: {str(e)}")
    
    return {"success": success_count, "failure": failure_count}
