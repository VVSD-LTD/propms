# -*- coding: utf-8 -*-
"""Call history query service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


@frappe.whitelist(methods=["GET", "POST"])
def get_call_history(status="all", page=1, page_length=20):
	"""Return call logs for the logged-in user (both incoming and outgoing)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"status": status, "page": page, "page_length": page_length})
		current_user = frappe.session.user
		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		# Condition: user is either caller or receiver
		where_status = ""
		if target_status and target_status != "all":
			where_status = f"AND status = '{target_status.capitalize()}'"

		calls = frappe.db.sql(
			f"""
			SELECT name, caller, caller_name, caller_unit, caller_role,
				receiver, receiver_name, receiver_unit, receiver_role,
				call_type, room_name, status, started_at, connected_at, ended_at, duration_seconds
			FROM `tabViva Call Log`
			WHERE (caller = %(user)s OR receiver = %(user)s)
			  {where_status}
			ORDER BY started_at DESC
			LIMIT %(limit)s OFFSET %(offset)s
			""",
			{"user": current_user, "limit": page_len, "offset": offset},
			as_dict=True,
		)

		for c in calls:
			c["is_outgoing"] = c.get("caller") == current_user

		# Summary counts
		summary_rows = frappe.db.sql(
			"""
			SELECT status, COUNT(name) as count
			FROM `tabViva Call Log`
			WHERE (caller = %(user)s OR receiver = %(user)s)
			GROUP BY status
			""",
			{"user": current_user},
			as_dict=True,
		)
		counts_map = {r["status"]: r["count"] for r in summary_rows}
		summary = {
			"total": sum(counts_map.values()),
			"answered": counts_map.get("Answered", 0) + counts_map.get("Ended", 0),
			"missed": counts_map.get("Missed", 0),
			"declined": counts_map.get("Declined", 0),
		}

		return {
			"status": "success",
			"summary": summary,
			"page": page_num,
			"page_length": page_len,
			"calls": calls,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_call_history")
		return {"status": "error", "message": str(e)}
