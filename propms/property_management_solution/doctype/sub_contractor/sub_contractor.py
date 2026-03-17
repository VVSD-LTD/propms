# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

# import frappe
from frappe.utils.nestedset import NestedSet


class SubContractor(NestedSet):
	def validate(self):
		self.sub_contractor = self.name
		self.set_employee_name()

	def set_employee_name(self):
		self.sub_contractor_name = " ".join(
			filter(lambda x: x, [self.first_name, self.middle_name, self.last_name])
		)

	def after_rename(self, old, new, merge):
		self.db_set("sub_contractor", new)