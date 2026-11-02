import json
import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

folder = "../patches/custom_fields/custom_fields_json"


def load_json(file):
    CURR_DIR = os.path.abspath(os.path.dirname(__file__))
    json_file_path = os.path.join(CURR_DIR, folder, file)
    # TODO do not load the file if already applied
    with open(json_file_path, "r") as file:
        data = json.load(file)
    return data


def create_fields_from_json(custom_fields_obj):
    disallowed_fields = [
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "is_system_generated",
        "__last_sync_on",
    ]
    doctype_custom_fields_dict = {}
    # Cache meta lookups per doctype for speed and consistency
    meta_cache = {}

    for custom_field in custom_fields_obj:
        doctype = custom_field["dt"]
        fieldname = custom_field.get("fieldname")

        # If the fieldname already exists as a standard DocField (or previously created),
        # do NOT try to create a Custom Field with the same fieldname.
        # This avoids migrate failures like: "A field with the name X already exists in Y".
        if fieldname:
            if doctype not in meta_cache:
                meta_cache[doctype] = frappe.get_meta(doctype)
            if meta_cache[doctype].has_field(fieldname):
                _sync_existing_custom_field(doctype, fieldname, custom_field)
                continue
        all_fields = frappe.get_meta("Custom Field").get_valid_columns()
        field_list = set(all_fields).difference(disallowed_fields)
        custom_field_dict = {}
        for field_name in field_list:
            custom_field_dict[field_name] = custom_field.get(field_name)

        # Ensure the list for the doctype is initialized
        if doctype not in doctype_custom_fields_dict:
            doctype_custom_fields_dict[doctype] = []

        doctype_custom_fields_dict[doctype].append(custom_field_dict)

    # Use update=True so reruns don't fail when fields already exist
    create_custom_fields(doctype_custom_fields_dict, update=True)


def _sync_existing_custom_field(doctype, fieldname, spec):
    """Apply fieldtype changes from JSON onto an existing Custom Field.

    Standard DocFields are left alone. This is how Penalty Invoice moves from
    Link to Data without a new column: both types share the same varchar column,
    and dropping the link stops cancel/delete checks on the stored name.
    """
    custom_field_name = frappe.db.get_value(
        "Custom Field", {"dt": doctype, "fieldname": fieldname}, "name"
    )
    if not custom_field_name:
        return

    desired_fieldtype = spec.get("fieldtype")
    if not desired_fieldtype:
        return

    current = frappe.db.get_value(
        "Custom Field",
        custom_field_name,
        ["fieldtype", "options", "description"],
        as_dict=True,
    )
    desired_options = spec.get("options") or None
    desired_description = spec.get("description")
    if (
        current.fieldtype == desired_fieldtype
        and (current.options or None) == desired_options
        and (desired_description is None or current.description == desired_description)
    ):
        return

    # Customize Form blocks Link -> Data. Both types use varchar(140), so the
    # stored invoice name stays put and only the link metadata is removed.
    updates = {
        "fieldtype": desired_fieldtype,
        "options": desired_options,
    }
    if desired_description is not None:
        updates["description"] = desired_description
    frappe.db.set_value("Custom Field", custom_field_name, updates, update_modified=False)
    frappe.clear_cache(doctype=doctype)


def execute():
    # read names of only json files in this folder and put it into files list
    files = list(
        filter(
            lambda x: x.endswith(".json"),
            os.listdir(
                os.path.join(os.path.abspath(os.path.dirname(__file__)), folder)
            ),
        )
    )
    for file in files:
        data = load_json(file)
        create_fields_from_json(data)


@frappe.whitelist()
def export_custom_fields(docnames):
    docnames = frappe.parse_json(docnames)
    custom_fields = []

    for docname in docnames:
        doc = frappe.get_doc("Custom Field", docname)
        custom_fields.append(
            doc.as_dict(
                convert_dates_to_str=True, no_default_fields=True, no_nulls=True
            )
        )

    return str(custom_fields)