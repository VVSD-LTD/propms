# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import unittest
from datetime import datetime, time

import frappe


class TestDeliveryWindow(unittest.TestCase):
	def test_valid_window_passes(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		validate_delivery_window(
			start="10:00:00",
			end="12:00:00",
			settings=settings,
			now=now,
			delivery_date="2026-09-29",
		)

	def test_too_short_window_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="10:00:00",
				end="11:00:00",
				settings=settings,
				now=now,
				delivery_date="2026-09-29",
			)

	def test_outside_hours_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 9, 0, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="17:00:00",
				end="19:00:00",
				settings=settings,
				now=now,
				delivery_date="2026-09-29",
			)

	def test_past_last_feasible_start_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		# 17:00 with min 120 → last start is 16:00; too late
		now = datetime(2026, 9, 29, 17, 0, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="16:00:00",
				end="18:00:00",
				settings=settings,
				now=now,
				delivery_date="2026-09-29",
			)

	def test_start_before_now_ceil_fails(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		now = datetime(2026, 9, 29, 10, 15, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(18, 0),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		# earliest start should be 10:30; 10:00 invalid
		with self.assertRaises(frappe.ValidationError):
			validate_delivery_window(
				start="10:00:00",
				end="12:00:00",
				settings=settings,
				now=now,
				delivery_date="2026-09-29",
			)

	def test_ceil_past_midnight_closed(self):
		from propms.api.v1.pos_store.delivery_window import validate_delivery_window

		# 23:45 with step 30 → ceil spills to next day 00:00
		now = datetime(2026, 9, 29, 23, 45, 0)
		settings = {
			"open_time": time(8, 0),
			"close_time": time(23, 59),
			"min_window_mins": 120,
			"picker_step_mins": 30,
		}
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_delivery_window(
				start="08:00:00",
				end="10:00:00",
				settings=settings,
				now=now,
				delivery_date="2026-09-29",
			)
		self.assertIn("closed", str(ctx.exception).lower())
