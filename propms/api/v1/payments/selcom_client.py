import base64
import datetime
import hashlib
import hmac
import json
import requests
import frappe
from frappe import _


def get_selcom_settings():
    """Retrieve Viva Selcom Settings singleton document."""
    if not frappe.db.exists("DocType", "Viva Selcom Settings"):
        frappe.throw(_("Viva Selcom Settings DocType is not installed."))

    settings = frappe.get_single("Viva Selcom Settings")
    return settings


class SelcomClient:
    """Official Selcom API Gateway HTTP & HMAC-SHA256 Client."""

    def __init__(self, base_url=None, api_key=None, api_secret=None, vendor_id=None):
        settings = get_selcom_settings()
        self.base_url = (base_url or settings.get("base_url") or "https://apigw.selcommobile.com").rstrip("/")
        self.api_key = api_key or settings.get("api_key")
        self.api_secret = api_secret or settings.get_password("api_secret") or settings.get("api_secret")
        self.vendor_id = vendor_id or settings.get("vendor_id")
        self.enabled = bool(settings.get("enabled", 1))

    def compute_header(self, dict_data):
        """Compute the 5 mandatory cryptographic authentication headers for Selcom."""
        if not self.api_key or not self.api_secret:
            frappe.throw(_("Selcom API Key and API Secret must be configured in Viva Selcom Settings."))

        # 1. Base64 encode API Key
        api_key_bytes = str(self.api_key).encode("ascii")
        base64_api_key = base64.b64encode(api_key_bytes).decode("ascii")
        auth_token = f"SELCOM {base64_api_key}"

        # 2. ISO 8601 Timestamp with timezone (Tanzania UTC+3)
        now = datetime.datetime.now().astimezone()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S%z")

        # 3. Build string of signed fields
        signed_fields_list = []
        data_str = f"timestamp={timestamp}"
        for key in dict_data:
            data_str += f"&{key}={str(dict_data[key])}"
            signed_fields_list.append(str(key))
        signed_fields = ",".join(signed_fields_list)

        # 4. Compute HMAC-SHA256 signature
        signature = hmac.new(
            key=str(self.api_secret).encode("utf-8"),
            msg=data_str.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        digest = base64.b64encode(signature).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": auth_token,
            "Digest-Method": "HS256",
            "Digest": digest,
            "Timestamp": timestamp,
            "Signed-Fields": signed_fields,
        }
        return headers

    def post(self, path, dict_data, timeout=30):
        """Send authenticated POST request to Selcom API Gateway."""
        if not self.enabled:
            return {"status": "error", "message": "Selcom payments are currently disabled in settings."}

        url = f"{self.base_url}{path}"
        headers = self.compute_header(dict_data)

        try:
            response = requests.post(url, json=dict_data, headers=headers, timeout=timeout)
            try:
                res_json = response.json()
            except Exception:
                res_json = {"raw_text": response.text, "http_status": response.status_code}
            return res_json
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Selcom POST Error: {str(e)}\nURL: {url}\nPayload: {json.dumps(dict_data)}", "SelcomClient.post")
            return {"status": "error", "message": f"Network error connecting to Selcom: {str(e)}"}

    def get(self, path, dict_data=None, timeout=30):
        """Send authenticated GET request to Selcom API Gateway."""
        if not self.enabled:
            return {"status": "error", "message": "Selcom payments are currently disabled in settings."}

        dict_data = dict_data or {}
        url = f"{self.base_url}{path}"
        headers = self.compute_header(dict_data)

        try:
            response = requests.get(url, params=dict_data, headers=headers, timeout=timeout)
            try:
                res_json = response.json()
            except Exception:
                res_json = {"raw_text": response.text, "http_status": response.status_code}
            return res_json
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Selcom GET Error: {str(e)}\nURL: {url}\nParams: {json.dumps(dict_data)}", "SelcomClient.get")
            return {"status": "error", "message": f"Network error connecting to Selcom: {str(e)}"}

    def delete(self, path, dict_data=None, timeout=30):
        """Send authenticated DELETE request to Selcom API Gateway."""
        if not self.enabled:
            return {"status": "error", "message": "Selcom payments are currently disabled in settings."}

        dict_data = dict_data or {}
        url = f"{self.base_url}{path}"
        headers = self.compute_header(dict_data)

        try:
            response = requests.delete(url, params=dict_data, headers=headers, timeout=timeout)
            try:
                res_json = response.json()
            except Exception:
                res_json = {"raw_text": response.text, "http_status": response.status_code}
            return res_json
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Selcom DELETE Error: {str(e)}\nURL: {url}\nParams: {json.dumps(dict_data)}", "SelcomClient.delete")
            return {"status": "error", "message": f"Network error connecting to Selcom: {str(e)}"}
