frappe.ui.form.on("Viva Selcom Settings", {
    refresh: function(frm) {
        // Ensure webhook URL matches current origin
        const webhookUrl = window.location.origin + "/api/method/propms.api.v1.payments.selcom_ipn_webhook";
        if (!frm.doc.ipn_callback_url || frm.doc.ipn_callback_url !== webhookUrl) {
            frm.set_value("ipn_callback_url", webhookUrl);
        }
    }
});
