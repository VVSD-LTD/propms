# -*- coding: utf-8 -*-
"""Viva Voice & Video Calling Top-Level Router."""

from __future__ import unicode_literals
import frappe
from propms.api.v1.calls import (
	get_livekit_config as _get_livekit_config,
	initiate_call as _initiate_call,
	answer_call as _answer_call,
	end_call as _end_call,
	get_call_history as _get_call_history,
)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_config():
	"""Return public LiveKit server connection configuration."""
	config = _get_livekit_config()
	return {
		"status": "success",
		"enabled": config.get("enabled", True),
		"server_url": config.get("url", ""),
	}


@frappe.whitelist(methods=["POST"])
def initiate_call(receiver=None, call_type="Voice"):
	return _initiate_call(receiver=receiver, call_type=call_type)


@frappe.whitelist(methods=["POST"])
def answer_call(call_id=None):
	return _answer_call(call_id=call_id)


@frappe.whitelist(methods=["POST"])
def end_call(call_id=None, reason="ended"):
	return _end_call(call_id=call_id, reason=reason)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_history(status="all", page=1, page_length=20):
	return _get_call_history(status=status, page=page, page_length=page_length)
