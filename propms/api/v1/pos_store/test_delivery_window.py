# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import unittest
from datetime import datetime, time

import frappe


class TestDeliveryWindow(unittest.TestCase):
	def _settings(self):
		return {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"slot_duration_mins": 60,
		}

	def test_valid_one_hour_slot_passes(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		out = validate_delivery_window(
			start="10:00:00",
			end="11:00:00",
			settings=self._settings(),
			now=now,
			delivery_date="2026-09-29",
		)
		self.assertEqual(out["delivery_time_start"], "10:00:00")
		self.assertEqual(out["delivery_time_end"], "11:00:00")

	def test_start_only_infers_one_hour_end(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		out = validate_delivery_window(
			start="14:00:00",
			end=None,
			settings=self._settings(),
			now=now,
			delivery_date="2026-09-29",
		)
		self.assertEqual(out["delivery_time_end"], "15:00:00")

	def test_two_hour_window_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="10:00:00",
				end="12:00:00",
				settings=self._settings(),
				now=now,
				delivery_date="2026-09-29",
			)

	def test_outside_hours_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="17:30:00",
				end="18:30:00",
				settings=self._settings(),
				now=now,
				delivery_date="2026-09-29",
			)

	def test_past_slot_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		# 10:15 → 10:00–11:00 already started
		now = datetime(2026, 9, 29, 10, 15, 0)
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="10:00:00",
				end="11:00:00",
				settings=self._settings(),
				now=now,
				delivery_date="2026-09-29",
			)

	def test_slots_hide_past(self):
		from propms.api.v1.pos_store.delivery_window import (
			build_delivery_slots,
			serialize_delivery_window_for_api,
		)

		now = datetime(2026, 9, 29, 10, 15, 0)
		slots = build_delivery_slots(
			settings=self._settings(), now=now, delivery_date="2026-09-29"
		)
		by_start = {s["start"]: s["available"] for s in slots}
		self.assertFalse(by_start["08:00:00"])
		self.assertFalse(by_start["10:00:00"])
		self.assertTrue(by_start["11:00:00"])
		self.assertTrue(by_start["17:00:00"])

		payload = serialize_delivery_window_for_api(
			settings=self._settings(), now=now, delivery_date="2026-09-29"
		)
		self.assertEqual(payload["slot_duration_mins"], 60)
		self.assertTrue(all(s["available"] for s in payload["slots"]))
		self.assertNotIn("10:00:00", [s["start"] for s in payload["slots"]])
		self.assertIn("11:00:00", [s["start"] for s in payload["slots"]])

	def test_same_day_only(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="10:00:00",
				end="11:00:00",
				settings=self._settings(),
				now=now,
				delivery_date="2026-09-30",
			)
