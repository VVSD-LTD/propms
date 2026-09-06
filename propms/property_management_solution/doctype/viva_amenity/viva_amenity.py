# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class VivaAmenity(Document):
	def validate(self):
		if not self.slot_duration_mins or self.slot_duration_mins <= 0:
			self.slot_duration_mins = 60
		if not self.capacity or self.capacity <= 0:
			self.capacity = 10

		# Fallback cover_image to first gallery image if empty
		if not self.cover_image and self.gallery_images:
			for img in self.gallery_images:
				if getattr(img, "image", None):
					self.cover_image = img.image
					break

