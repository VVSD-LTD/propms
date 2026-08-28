"""
FCM Push Notification functions for ticket messages and app notifications
"""
import frappe


@frappe.whitelist()
def enqueue_ticket_message_push(user, ticket_id, title, body, message_idx=None, notification_type=None):
    """Send FCM push notification for ticket messages or mentions.
    
    This function is called as a background job to send push notifications
    when new messages are added to tickets.
    
    Args:
        user (str): User email to send notification to
        ticket_id (str): Ticket ID/name
        title (str): Notification title
        body (str): Notification body/message preview
        message_idx (int/str, optional): Communication idx for opening the specific message (e.g. for mention)
        notification_type (str, optional): "ticket_message" (default) or "mention"
    """
    try:
        frappe.logger().info(f"📲 FCM PUSH: Starting for user={user}, ticket={ticket_id}")
        
        # Get user device tokens
        # Get the most recent device token only to avoid sending duplicate notifications
        tokens = frappe.get_all(
            "User Device",
            filters={"user": user},
            order_by="creation desc",
            pluck="token",
            limit_page_length=1
        )
        
        if not tokens:
            frappe.logger().warning(f"📲 FCM PUSH: No device tokens found for user {user}")
            return {
                "status": "no_tokens",
                "user": user,
                "ticket_id": ticket_id
            }
        
        frappe.logger().info(f"📲 FCM PUSH: Found {len(tokens)} device token(s) for user {user}")
        
        # Prepare FCM data payload
        data = {
            "type": notification_type or "ticket_message",
            "ticket_id": ticket_id,
            "user": user,
        }
        if message_idx is not None:
            data["message_idx"] = str(message_idx)
        
        # Send FCM notification using the utility function
        from propms.api.v1.utils.fcm import send_to_tokens
        
        result = send_to_tokens(
            tokens=tokens,
            data=data,
            title=title,
            body=body
        )
        
        success_count = result.get("success", 0)
        failure_count = result.get("failure", 0)
        
        frappe.logger().info(
            f"📲 FCM PUSH: Completed for user {user}, ticket {ticket_id} - "
            f"Success: {success_count}, Failure: {failure_count}"
        )
        
        return {
            "status": "success",
            "user": user,
            "ticket_id": ticket_id,
            "result": result,
            "token_count": len(tokens),
            "success_count": success_count,
            "failure_count": failure_count
        }
        
    except Exception as e:
        frappe.logger().error(
            f"❌ FCM PUSH ERROR: Error sending FCM to user {user} for ticket {ticket_id}: {str(e)}"
        )
        frappe.log_error(
            frappe.get_traceback(),
            f"FCM Push Error - User: {user}, Ticket: {ticket_id}"
        )
        return {
            "status": "error",
            "user": user,
            "ticket_id": ticket_id,
            "message": str(e)
        }


@frappe.whitelist()
def enqueue_app_notification_push(user, notification_id, title, body):
    """Send FCM push notification for app-level notifications.
    
    This function is called as a background job to send push notifications
    for app-level notifications (not ticket messages).
    
    Args:
        user (str): User email to send notification to
        notification_id (str): Notification ID/name
        title (str): Notification title
        body (str): Notification body/message
    """
    try:
        frappe.logger().info(f"📲 FCM APP NOTIFICATION: Starting for user={user}, notification={notification_id}")
        
        # Get user device tokens
        tokens = frappe.get_all(
            "User Device",
            filters={"user": user},
            pluck="token"
        )
        
        if not tokens:
            frappe.logger().warning(f"📲 FCM APP NOTIFICATION: No device tokens found for user {user}")
            return {
                "status": "no_tokens",
                "user": user,
                "notification_id": notification_id
            }
        
        frappe.logger().info(f"📲 FCM APP NOTIFICATION: Found {len(tokens)} device token(s) for user {user}")
        
        # Prepare FCM data payload for app notification
        data = {
            "type": "app_notification",
            "notification_id": notification_id,
            "user": user,
            "is_app_notification": "1"
        }
        
        # Send FCM notification using the utility function
        from propms.api.v1.utils.fcm import send_to_tokens
        
        result = send_to_tokens(
            tokens=tokens,
            data=data,
            title=title,
            body=body
        )
        
        success_count = result.get("success", 0)
        failure_count = result.get("failure", 0)
        
        frappe.logger().info(
            f"📲 FCM APP NOTIFICATION: Completed for user {user}, notification {notification_id} - "
            f"Success: {success_count}, Failure: {failure_count}"
        )
        
        return {
            "status": "success",
            "user": user,
            "notification_id": notification_id,
            "result": result,
            "token_count": len(tokens),
            "success_count": success_count,
            "failure_count": failure_count
        }
        
    except Exception as e:
        frappe.logger().error(
            f"❌ FCM APP NOTIFICATION ERROR: Error sending FCM to user {user} for notification {notification_id}: {str(e)}"
        )
        frappe.log_error(
            frappe.get_traceback(),
            f"FCM App Notification Error - User: {user}, Notification: {notification_id}"
        )
        return {
            "status": "error",
            "user": user,
            "notification_id": notification_id,
            "message": str(e)
        }

