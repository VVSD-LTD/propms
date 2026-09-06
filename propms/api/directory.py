# -*- coding: utf-8 -*-
"""Viva Building Directory Top-Level Router."""

from __future__ import unicode_literals
import frappe
from propms.api.v1.directory import get_directory_contacts as _get_directory_contacts


@frappe.whitelist(methods=["GET", "POST"])
def get_directory_contacts(department=None):
	return _get_directory_contacts(department=department)
