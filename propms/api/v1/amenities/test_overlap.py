# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import unittest


class TestAmenityOverlap(unittest.TestCase):
	def test_ranges_overlap(self):
		from propms.api.v1.amenities.overlap import ranges_overlap

		self.assertTrue(ranges_overlap("18:00:00", "21:00:00", "19:00:00", "20:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", "21:00:00", "21:00:00", "22:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", "21:00:00", "16:00:00", "18:00:00"))

	def test_buffer_blocks_next_start(self):
		from propms.api.v1.amenities.overlap import expand_end_with_buffer, ranges_overlap

		end_buf = expand_end_with_buffer("21:00:00", 30)
		self.assertEqual(end_buf, "21:30:00")
		self.assertTrue(ranges_overlap("18:00:00", end_buf, "21:00:00", "22:00:00"))
		self.assertFalse(ranges_overlap("18:00:00", end_buf, "21:30:00", "22:00:00"))

	def test_free_gaps(self):
		from propms.api.v1.amenities.overlap import compute_free_gaps

		gaps = compute_free_gaps(
			open_time="06:00:00",
			close_time="22:00:00",
			busy=[
				{"start_time": "10:00:00", "end_with_buffer": "12:30:00"},
				{"start_time": "18:00:00", "end_with_buffer": "21:30:00"},
			],
		)
		self.assertEqual(gaps[0]["start_time"], "06:00:00")
		self.assertEqual(gaps[0]["end_time"], "10:00:00")
		self.assertEqual(gaps[1]["start_time"], "12:30:00")
		self.assertEqual(gaps[1]["end_time"], "18:00:00")
		self.assertEqual(gaps[2]["start_time"], "21:30:00")
		self.assertEqual(gaps[2]["end_time"], "22:00:00")

	def test_time_on_step(self):
		from propms.api.v1.amenities.overlap import is_time_on_step

		self.assertTrue(is_time_on_step("18:00:00", 30))
		self.assertTrue(is_time_on_step("18:30:00", 30))
		self.assertFalse(is_time_on_step("18:15:00", 30))
		self.assertTrue(is_time_on_step("18:15:00", 15))
