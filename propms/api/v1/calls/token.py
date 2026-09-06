# -*- coding: utf-8 -*-
"""LiveKit JWT Access Token generation service (Dual-Mode: Cloud & Self-Hosted)."""

from __future__ import unicode_literals
import time
import frappe
from frappe import _


def get_livekit_config():
	"""Fetch LiveKit connection settings from Mobile App Settings."""
	try:
		settings = frappe.get_single("Mobile App Settings")
		enabled = getattr(settings, "livekit_enabled", 1)
		url = getattr(settings, "livekit_url", None)
		api_key = getattr(settings, "livekit_api_key", None)
		api_secret = getattr(settings, "livekit_api_secret", None)

		# Use password field decryption if needed
		if api_secret and hasattr(settings, "get_password"):
			try:
				decrypted = settings.get_password("livekit_api_secret")
				if decrypted:
					api_secret = decrypted
			except Exception:
				pass

		return {
			"enabled": bool(enabled),
			"url": (url or "").strip(),
			"api_key": (api_key or "").strip(),
			"api_secret": (api_secret or "").strip(),
		}
	except Exception as e:
		frappe.logger().error(f"Error reading LiveKit config: {e}")
		return {"enabled": False, "url": "", "api_key": "", "api_secret": ""}


def generate_livekit_token(
	room_name,
	identity,
	name=None,
	can_publish=True,
	can_subscribe=True,
	ttl_seconds=21600,
):
	"""Generate a signed LiveKit Access Token JWT.
	
	Compatible with both LiveKit Cloud and Self-Hosted instances.
	"""
	config = get_livekit_config()
	api_key = config.get("api_key")
	api_secret = config.get("api_secret")
	server_url = config.get("url")

	if not api_key or not api_secret:
		frappe.throw(_("LiveKit API Key and Secret are not configured in Mobile App Settings"))

	identity = str(identity)
	display_name = str(name or identity)
	now = int(time.time())

	# 1. Try official livekit-api if available
	try:
		from livekit.api import AccessToken, VideoGrants

		grant = VideoGrants(
			room_join=True,
			room=room_name,
			can_publish=can_publish,
			can_subscribe=can_subscribe,
		)
		token = (
			AccessToken(api_key, api_secret)
			.with_identity(identity)
			.with_name(display_name)
			.with_grants(grant)
			.with_ttl(ttl_seconds)
			.to_jwt()
		)
		return {"token": token, "server_url": server_url}
	except ImportError:
		pass

	# 2. Pure JWT generation fallback (Zero external dependencies)
	import jwt

	payload = {
		"iss": api_key,
		"sub": identity,
		"name": display_name,
		"nbf": now - 5,
		"exp": now + ttl_seconds,
		"video": {
			"room": room_name,
			"roomJoin": True,
			"canPublish": can_publish,
			"canSubscribe": can_subscribe,
		},
	}

	token = jwt.encode(payload, api_secret, algorithm="HS256")
	if isinstance(token, bytes):
		token = token.decode("utf-8")

	return {"token": token, "server_url": server_url}
