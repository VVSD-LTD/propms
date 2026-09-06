import os
import shutil
import json
import frappe
from frappe.utils import get_site_path, generate_hash

def _get_chunks_temp_dir(session_id):
    """Return the absolute path for temporary chunk storage for a session."""
    base_dir = get_site_path("private", "files", "chunks", session_id)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


@frappe.whitelist()
def start_upload_session(
    filename=None,
    file_name=None,
    total_chunks=1,
    chunks_total=None,
    total_size=0,
    file_size=None,
    file_type=None,
    doctype=None,
    docname=None,
    fieldname=None,
    is_private=0,
):
    """Initialize a chunked upload session."""
    try:
        req = getattr(frappe, "form_dict", None) or {}
        filename = filename or file_name or req.get("filename") or req.get("file_name") or "uploaded_video.mp4"
        total_chunks = int(total_chunks or chunks_total or req.get("total_chunks") or req.get("chunks_total") or 1)
        total_size = int(total_size or file_size or req.get("total_size") or req.get("file_size") or 0)
        file_type = file_type or req.get("file_type") or req.get("mime_type") or ""
        doctype = doctype or req.get("doctype") or req.get("attached_to_doctype")
        docname = docname or req.get("docname") or req.get("attached_to_name")
        fieldname = fieldname or req.get("fieldname") or req.get("attached_to_field")
        is_private = int(is_private or req.get("is_private") or 0)

        session_id = generate_hash(length=24)
        chunks_dir = _get_chunks_temp_dir(session_id)

        meta = {
            "session_id": session_id,
            "filename": filename,
            "total_chunks": total_chunks,
            "total_size": total_size,
            "file_type": file_type,
            "doctype": doctype,
            "docname": docname,
            "fieldname": fieldname,
            "is_private": is_private,
            "user": frappe.session.user,
        }

        meta_path = os.path.join(chunks_dir, "metadata.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        return {
            "status": "success",
            "session_id": session_id,
            "upload_session_id": session_id,
            "filename": filename,
            "total_chunks": total_chunks,
            "message": "Upload session initialized",
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "start_upload_session")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def upload_chunk(session_id=None, upload_session_id=None, chunk_index=None, chunk_number=None):
    """Receive and save an individual binary chunk."""
    try:
        req = getattr(frappe, "form_dict", None) or {}
        session_id = session_id or upload_session_id or req.get("session_id") or req.get("upload_session_id")
        chunk_idx = chunk_index if chunk_index is not None else (chunk_number if chunk_number is not None else req.get("chunk_index", req.get("chunk_number")))

        if not session_id:
            return {"status": "error", "message": "session_id is required"}
        if chunk_idx is None:
            return {"status": "error", "message": "chunk_index is required"}

        chunk_idx = int(chunk_idx)
        chunks_dir = _get_chunks_temp_dir(session_id)

        # Retrieve chunk data from multipart files or raw body
        chunk_data = None
        if getattr(frappe.request, "files", None):
            for file_key in ["file", "chunk", "data", "file_chunk"]:
                if file_key in frappe.request.files:
                    chunk_data = frappe.request.files[file_key].read()
                    break
            if chunk_data is None and len(frappe.request.files) > 0:
                first_key = list(frappe.request.files.keys())[0]
                chunk_data = frappe.request.files[first_key].read()

        if chunk_data is None:
            chunk_data = frappe.request.get_data()

        if not chunk_data:
            return {"status": "error", "message": "No chunk data received"}

        chunk_path = os.path.join(chunks_dir, f"chunk_{chunk_idx:05d}")
        with open(chunk_path, "wb") as f:
            f.write(chunk_data)

        return {
            "status": "success",
            "session_id": session_id,
            "chunk_index": chunk_idx,
            "bytes_received": len(chunk_data),
            "message": f"Chunk {chunk_idx} saved",
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "upload_chunk")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def finalize_upload(
    session_id=None,
    upload_session_id=None,
    doctype=None,
    docname=None,
    fieldname=None,
    is_private=None,
):
    """Reassemble all chunks into the final File document in Frappe."""
    try:
        req = getattr(frappe, "form_dict", None) or {}
        session_id = session_id or upload_session_id or req.get("session_id") or req.get("upload_session_id")

        if not session_id:
            return {"status": "error", "message": "session_id is required"}

        chunks_dir = _get_chunks_temp_dir(session_id)
        meta_path = os.path.join(chunks_dir, "metadata.json")

        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)

        filename = meta.get("filename") or req.get("filename") or "uploaded_video.mp4"
        total_chunks = int(meta.get("total_chunks", 1))
        doctype = doctype or req.get("doctype") or meta.get("doctype")
        docname = docname or req.get("docname") or meta.get("docname")
        fieldname = fieldname or req.get("fieldname") or meta.get("fieldname")
        is_private = int(is_private if is_private is not None else (req.get("is_private") or meta.get("is_private", 0)))

        # Find all chunk files sorted
        chunk_files = sorted([f for f in os.listdir(chunks_dir) if f.startswith("chunk_")])
        if not chunk_files:
            return {"status": "error", "message": "No chunk files found for this session"}

        # Combine into complete binary buffer
        final_data = bytearray()
        for cname in chunk_files:
            cpath = os.path.join(chunks_dir, cname)
            with open(cpath, "rb") as cf:
                final_data.extend(cf.read())

        # Save to Frappe File
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": filename,
            "content": bytes(final_data),
            "is_private": is_private,
            "attached_to_doctype": doctype or None,
            "attached_to_name": docname or None,
            "attached_to_field": fieldname or None,
        })
        file_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # Clean up temporary chunk folder
        try:
            shutil.rmtree(chunks_dir)
        except Exception:
            pass

        return {
            "status": "success",
            "message": "File uploaded successfully",
            "file_url": file_doc.file_url,
            "name": file_doc.name,
            "file_name": file_doc.file_name,
            "file_size": len(final_data),
            "is_private": file_doc.is_private,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finalize_upload")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_upload_session_status(session_id=None, upload_session_id=None):
    """Check upload session progress and return received chunks."""
    try:
        req = getattr(frappe, "form_dict", None) or {}
        session_id = session_id or upload_session_id or req.get("session_id") or req.get("upload_session_id")
        if not session_id:
            return {"status": "error", "message": "session_id is required"}

        chunks_dir = _get_chunks_temp_dir(session_id)
        meta_path = os.path.join(chunks_dir, "metadata.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)

        chunk_files = sorted([f for f in os.listdir(chunks_dir) if f.startswith("chunk_")])
        present_chunks = []
        for c in chunk_files:
            try:
                present_chunks.append(int(c.split("_")[1]))
            except Exception:
                pass

        total_chunks = int(meta.get("total_chunks", 1))
        return {
            "status": "success",
            "session_id": session_id,
            "total_chunks": total_chunks,
            "received_chunks": len(present_chunks),
            "present_chunks": present_chunks,
            "filename": meta.get("filename"),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_upload_session_status")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def abort_upload_session(session_id=None, upload_session_id=None):
    """Abort upload session and clean up temp chunks."""
    try:
        req = getattr(frappe, "form_dict", None) or {}
        session_id = session_id or upload_session_id or req.get("session_id") or req.get("upload_session_id")
        if not session_id:
            return {"status": "error", "message": "session_id is required"}

        chunks_dir = _get_chunks_temp_dir(session_id)
        if os.path.exists(chunks_dir):
            shutil.rmtree(chunks_dir, ignore_errors=True)

        return {
            "status": "success",
            "message": "Upload session aborted",
            "session_id": session_id,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "abort_upload_session")
        return {"status": "error", "message": str(e)}

