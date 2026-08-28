// Rich "Chat with Tenant" UI for Issue doctype
// This file adds:
// - A "Chat with Tenant" button on Issue
// - A full-featured chat modal (mentions, emoji picker, media preview, drag & drop, reply/edit, search)

function ensure_chat_with_tenant_button(frm) {
    if (!frm || frm.doctype !== "Issue") return;
    const label = __("Chat with Tenant");

    // Frappe clears custom buttons on refresh; use frm.custom_buttons to avoid duplicates.
    if (frm.custom_buttons && frm.custom_buttons[label]) return;

    try {
        frm.add_custom_button(label, () => {
            if (!frm.doc.person_in_charge) {
                frappe.msgprint({
                    title: __("Person in charge required"),
                    message: __(
                        "Please assign Person in Charge on this issue before using Chat with Tenant."
                    ),
                    indicator: "orange",
                });
                return;
            }
            window.__propms_issue_chat_channel = 'tenant_support';
            load_tenant_chat_emoji_assets_once();
            propms_init_tenant_chat_ui();
            openChat(frm);
        });
    } catch (e) {
        // If form isn't ready yet, ignore; refresh/onload_post_render will retry.
    }
}

frappe.ui.form.on('Issue', {
    // onload_post_render fires reliably when the form UI is ready
    onload_post_render(frm) {
        ensure_chat_with_tenant_button(frm);
    },
    refresh(frm) {
        ensure_chat_with_tenant_button(frm);
    },
});

// If this script loads after the Issue form has already rendered (common on first open),
// ensure the button is still added without needing a manual reload.
try {
    frappe.after_ajax(() => {
        if (window.cur_frm && window.cur_frm.doctype === "Issue") {
            ensure_chat_with_tenant_button(window.cur_frm);
        }
    });
} catch (e) {
    // ignore
}

// Load emoji-mart assets once (equivalent to the <script type="module"> in your standalone HTML)
function load_tenant_chat_emoji_assets_once() {
    if (window.__tenant_chat_emoji_loaded) return;

    const s = document.createElement('script');
    s.type = 'module';
    s.textContent = `
      import data from 'https://cdn.jsdelivr.net/npm/@emoji-mart/data@1.2.1/+esm';
      import { Picker } from 'https://cdn.jsdelivr.net/npm/emoji-mart@5.6.0/+esm';
      window._EmojiMartPicker = Picker;
      window._EmojiMartData = data;
      window.dispatchEvent(new Event('emoji-mart-ready'));
    `;
    document.head.appendChild(s);
    window.__tenant_chat_emoji_loaded = true;
}

function propms_init_tenant_chat_ui() {
    if (window.__propms_tenant_chat_initialized) return;

    const wrapper = document.createElement('div');
    wrapper.id = 'propms-tenant-chat-root';
    wrapper.innerHTML = `
<style>
  :root {
    --fp-blue:       #171717;
    --fp-blue-dark:  #000000;
    --fp-blue-light: #F3F3F3;
    --fp-bg:         #F3F3F3;
    --fp-surface:    #FFFFFF;
    --fp-border:     #E4E4E4;
    --fp-text:       #171717;
    --fp-muted:      #757575;
    --fp-green:      #2DB84B;
    --fp-sidebar:    #F3F3F3;
    --fp-hover:      #EBEBEB;
  }

  #propms-tenant-chat-root * {
    font-family: 'Plus Jakarta Sans', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    box-sizing: border-box;
  }

  .modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(23,23,23,0.6); backdrop-filter: blur(6px);
    z-index: 2000; align-items: center; justify-content: center;
  }
  .modal-overlay.open { display: flex; animation: overlayIn 0.2s ease; }
  @keyframes overlayIn { from { opacity: 0; } to { opacity: 1; } }

  .chat-modal {
    width: min(700px, 96vw); height: min(720px, 94vh);
    border-radius: 16px; overflow: hidden;
    border: 1px solid var(--fp-border);
    box-shadow: 0 24px 80px rgba(0,0,0,0.18);
    display: flex; flex-direction: column;
    background: var(--fp-surface);
    animation: modalIn 0.3s cubic-bezier(0.34, 1.4, 0.64, 1);
    position: relative;
  }
  .chat-modal.fullscreen {
    width: min(1200px, calc(100vw - 40px));
    height: calc(100vh - 40px);
    max-width: calc(100vw - 40px);
    max-height: calc(100vh - 40px);
    border-radius: 14px;
    border: 1px solid var(--fp-border);
    box-shadow: 0 24px 80px rgba(0,0,0,0.18);
    animation: none;
  }
  @keyframes modalIn {
    from { opacity: 0; transform: scale(0.93) translateY(14px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }

  .chat-header {
    background: var(--fp-surface); padding: 13px 16px;
    display: flex; align-items: center; gap: 11px;
    border-bottom: 1px solid var(--fp-border); flex-shrink: 0;
  }
  .avatar-wrap { position: relative; flex-shrink: 0; }
  .avatar-inner {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg, var(--fp-blue), var(--fp-blue-dark));
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 700; font-size: 13px;
  }
  .status-dot {
    position: absolute; bottom: 1px; right: 1px;
    width: 10px; height: 10px; background: var(--fp-green);
    border-radius: 50%; border: 2px solid var(--fp-surface);
  }
  .header-name { color: var(--fp-text); font-weight: 600; font-size: 14.5px; }
  .header-sub  { color: var(--fp-green); font-size: 11.5px; margin-top: 1px; display: flex; align-items: center; gap: 4px; }
  .header-badge {
    background: var(--fp-blue-light); color: var(--fp-blue);
    font-size: 10.5px; font-weight: 600; padding: 2px 9px;
    border-radius: 20px; border: 1px solid rgba(23,23,23,0.15);
  }
  .icon-btn {
    width: 34px; height: 34px; border-radius: 8px; border: none;
    background: transparent; color: var(--fp-muted);
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: all 0.15s; flex-shrink: 0;
  }
  .icon-btn:hover { background: var(--fp-hover); color: var(--fp-blue); }

  .search-bar { padding: 8px 14px; border-bottom: 1px solid var(--fp-border); background: var(--fp-sidebar); display: none; }
  .search-bar.open { display: flex; align-items: center; gap: 8px; }
  .search-bar input {
    flex: 1; border: 1px solid var(--fp-border); border-radius: 8px;
    padding: 6px 12px; font-size: 13px; outline: none;
    background: var(--fp-surface); color: var(--fp-text); transition: border-color 0.15s;
  }
  .search-bar input:focus { border-color: var(--fp-blue); }

  .messages-area {
    flex: 1; overflow-y: auto; padding: 18px 16px;
    display: flex; flex-direction: column; gap: 4px;
    background: var(--fp-sidebar); scroll-behavior: smooth;
    position: relative; transition: background 0.2s;
  }
  .messages-area::-webkit-scrollbar { width: 4px; }
  .messages-area::-webkit-scrollbar-thumb { background: var(--fp-border); border-radius: 4px; }

  .messages-area.drag-over { background: #EBEBEB; }
  .drop-zone-overlay {
    display: none; position: absolute; inset: 0;
    border: 2.5px dashed var(--fp-blue);
    border-radius: 12px; background: rgba(23,23,23,0.04);
    z-index: 50; align-items: center; justify-content: center;
    flex-direction: column; gap: 10px; pointer-events: none;
  }
  .drop-zone-overlay.visible { display: flex; }
  .drop-zone-label { font-size: 14px; font-weight: 600; color: var(--fp-blue); }
  .drop-zone-sub { font-size: 12px; color: var(--fp-muted); }

  .date-divider {
    display: flex; align-items: center; gap: 10px;
    color: var(--fp-muted); font-size: 11px; font-weight: 600;
    letter-spacing: 0.07em; text-transform: uppercase; margin: 8px 0;
  }
  .date-divider::before, .date-divider::after {
    content: ''; flex: 1; height: 1px; background: var(--fp-border);
  }

  .msg-row {
    display: flex; align-items: flex-end; gap: 8px;
    animation: fadeUp 0.25s ease forwards;
    opacity: 0; position: relative; padding: 1px 0;
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .msg-row.outgoing { flex-direction: row-reverse; }
  .msg-row.consecutive { margin-top: -6px; }
  .msg-row.consecutive .mini-avatar { visibility: hidden; }

  .mini-avatar {
    width: 28px; height: 28px; border-radius: 50%;
    background: linear-gradient(135deg, var(--fp-blue), var(--fp-blue-dark));
    display: flex; align-items: center; justify-content: center;
    font-size: 10px; color: #fff; font-weight: 700; flex-shrink: 0;
  }

  .bubble-wrap { display: flex; flex-direction: column; max-width: 64%; position: relative; }
  .msg-row.outgoing .bubble-wrap { align-items: flex-end; }

  .bubble {
    padding: 9px 13px 7px; border-radius: 14px;
    font-size: 13.5px; line-height: 1.58; position: relative; word-break: break-word;
  }
  .bubble.incoming {
    background: var(--fp-surface); color: var(--fp-text);
    border-bottom-left-radius: 3px;
    border: 1px solid var(--fp-border); box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }
  .bubble.outgoing {
    background: var(--fp-blue); color: #fff;
    border-bottom-right-radius: 3px; box-shadow: 0 2px 10px rgba(23,23,23,0.2);
  }

  .mention {
    color: var(--fp-blue); font-weight: 600;
    background: var(--fp-blue-light); border-radius: 4px; padding: 0 3px;
  }
  .bubble.outgoing .mention { color: #fff; background: rgba(255,255,255,0.2); }

  .edited-label { font-size: 10px; color: var(--fp-muted); font-style: italic; margin-left: 4px; }
  .bubble.outgoing .edited-label { color: rgba(255,255,255,0.55); }

  .bubble-footer { display: flex; align-items: center; gap: 3px; justify-content: flex-end; margin-top: 3px; }
  .bubble-time { font-size: 10.5px; color: var(--fp-muted); }
  .bubble.outgoing .bubble-time { color: rgba(255,255,255,0.65); }
  .tick { font-size: 11px; color: rgba(255,255,255,0.55); }
  .tick.read { color: #90CAF9; }

  .reply-preview {
    background: rgba(0,0,0,0.05); border-left: 3px solid var(--fp-blue);
    border-radius: 6px; padding: 5px 9px; margin-bottom: 7px; font-size: 12px;
  }
  .bubble.outgoing .reply-preview { background: rgba(255,255,255,0.15); border-left-color: rgba(255,255,255,0.6); }
  .reply-preview .reply-author { font-weight: 700; font-size: 11px; color: var(--fp-blue); margin-bottom: 1px; }
  .bubble.outgoing .reply-preview .reply-author { color: #fff; }
  .reply-preview .reply-text { opacity: 0.7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 260px; }

  .bubble.media { padding: 4px; background: transparent !important; border: none !important; box-shadow: none !important; }
  .bubble.media img { border-radius: 12px; max-width: 220px; display: block; border: 1px solid var(--fp-border); }
  .bubble.emoji-only { background: transparent !important; border: none !important; box-shadow: none !important; padding: 2px 0 !important; font-size: 30px !important; line-height: 1.2 !important; }

  .msg-actions {
    position: absolute; top: -22px; right: 4px;
    z-index: 20; display: none;
  }
  .msg-row.outgoing .msg-actions { left: 4px; right: auto; top: -22px; }
  .msg-row:hover .msg-actions { display: block; }
  .msg-actions-trigger {
    width: 24px; height: 24px; border-radius: 999px; border: none;
    background: rgba(0,0,0,0.04); color: var(--fp-muted);
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: all 0.12s; font-size: 14px;
  }
  .msg-actions-trigger:hover { background: var(--fp-hover); color: var(--fp-blue); }
  .msg-actions-menu {
    position: absolute; top: 26px; right: 0;
    min-width: 120px; background: var(--fp-surface);
    border-radius: 10px; padding: 4px 0;
    border: 1px solid var(--fp-border);
    box-shadow: 0 8px 24px rgba(15,23,42,0.18);
    display: none;
  }
  .msg-actions-menu.below {
    top: auto;
    bottom: 26px;
  }
  .msg-actions-menu.open { display: block; }
  .action-item {
    width: 100%; padding: 6px 12px; border: none;
    background: transparent; text-align: left;
    font-size: 12px; color: var(--fp-muted);
    cursor: pointer; display: flex; align-items: center; gap: 6px;
  }
  .action-item:hover { background: var(--fp-hover); color: var(--fp-blue); }

  .typing-row { display: flex; align-items: center; gap: 8px; }
  .typing-name { font-size: 11px; color: var(--fp-muted); margin-bottom: 2px; font-weight: 600; }
  .typing-dots {
    display: flex; gap: 4px; background: var(--fp-surface); padding: 10px 14px;
    border-radius: 14px; border-bottom-left-radius: 3px;
    border: 1px solid var(--fp-border); box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }
  .typing-dots span {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--fp-blue); animation: tdot 1.2s infinite;
  }
  .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
  .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes tdot {
    0%,60%,100% { transform: translateY(0); opacity: 0.35; }
    30% { transform: translateY(-5px); opacity: 1; }
  }

  .reply-strip {
    display: none; align-items: center; gap: 10px; padding: 8px 14px;
    background: var(--fp-blue-light);
    border-top: 1px solid rgba(23,23,23,0.12); border-left: 3px solid var(--fp-blue);
  }
  .reply-strip.open { display: flex; }
  .reply-strip-content { flex: 1; }
  .reply-strip-author { font-weight: 700; color: var(--fp-blue); font-size: 11.5px; }
  .reply-strip-text { color: var(--fp-muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 400px; }
  .strip-close { background: none; border: none; cursor: pointer; color: var(--fp-muted); font-size: 17px; line-height: 1; padding: 2px 4px; border-radius: 4px; }
  .strip-close:hover { background: rgba(23,23,23,0.1); color: var(--fp-blue); }

  .edit-strip {
    display: none; align-items: center; gap: 10px; padding: 8px 14px;
    background: #FFFBEB; border-top: 1px solid rgba(240,165,0,0.2); border-left: 3px solid #F0A500;
  }
  .edit-strip.open { display: flex; }
  .edit-strip-label { flex: 1; font-size: 12px; font-weight: 600; color: #8a6a00; }

  .mention-popup {
    position: absolute; left: 0; right: 0; bottom: 100%;
    background: var(--fp-surface);
    border: 1px solid var(--fp-border);
    border-radius: 16px 16px 0 0;
    overflow: hidden;
    box-shadow: 0 -12px 40px rgba(0,0,0,0.14);
    z-index: 500; display: none;
    max-height: 280px;
  }
  .mention-popup.open { display: flex; flex-direction: column; }
  .mention-popup-header {
    padding: 10px 14px 8px;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid var(--fp-border);
    background: var(--fp-surface); flex-shrink: 0;
  }
  .mention-popup-title {
    font-size: 11px; font-weight: 700; color: var(--fp-muted);
    letter-spacing: 0.08em; text-transform: uppercase;
    display: flex; align-items: center; gap: 6px;
  }
  .mention-popup-title svg { color: var(--fp-blue); }
  .mention-count {
    font-size: 10.5px; font-weight: 600;
    background: var(--fp-blue-light); color: var(--fp-blue);
    padding: 1px 7px; border-radius: 20px;
  }
  #mentionList { overflow-y: auto; flex: 1; }
  #mentionList::-webkit-scrollbar { width: 3px; }
  #mentionList::-webkit-scrollbar-thumb { background: var(--fp-border); border-radius: 3px; }

  .mention-item {
    display: flex; align-items: center; gap: 11px;
    padding: 10px 14px; cursor: pointer;
    transition: background 0.12s; position: relative;
  }
  .mention-item:hover, .mention-item.active { background: var(--fp-hover); }
  .mention-item:hover .mention-arrow { opacity: 1; }
  .m-avatar {
    width: 34px; height: 34px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; color: #fff; font-weight: 700; flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(0,0,0,0.12);
  }
  .m-info { flex: 1; min-width: 0; }
  .m-name { font-size: 13px; font-weight: 600; color: var(--fp-text); line-height: 1.2; }
  .m-role {
    font-size: 11px; color: var(--fp-muted); margin-top: 1px;
    display: flex; align-items: center; gap: 4px;
  }
  .m-role-dot {
    width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0;
  }
  .mention-arrow {
    opacity: 0; transition: opacity 0.15s;
    color: var(--fp-blue); font-size: 12px;
  }
  .mention-item.no-results {
    color: var(--fp-muted); font-size: 13px; cursor: default;
    justify-content: center; padding: 20px;
  }
  .mention-item.no-results:hover { background: transparent; }
  .mention-kbd {
    display: inline-flex; align-items: center; gap: 3px;
    font-size: 10px; color: var(--fp-muted);
  }
  .kbd {
    background: var(--fp-sidebar); border: 1px solid var(--fp-border);
    border-radius: 4px; padding: 1px 5px; font-size: 10px;
    font-weight: 600; color: var(--fp-text);
  }

  .emoji-picker-wrap {
    position: absolute; bottom: calc(100% + 8px); left: 12px;
    z-index: 600; display: none;
    border-radius: 16px; overflow: hidden;
    box-shadow: 0 12px 48px rgba(0,0,0,0.18);
    border: 1px solid var(--fp-border);
  }
  .emoji-picker-wrap.open { display: block; }

  .input-area {
    background: var(--fp-surface); border-top: 1px solid var(--fp-border);
    flex-shrink: 0; position: relative;
  }
  .input-row { padding: 10px 12px; display: flex; align-items: center; gap: 7px; }
  .input-wrapper {
    flex: 1; display: flex; align-items: center; gap: 6px;
    background: var(--fp-sidebar); border: 1.5px solid var(--fp-border);
    border-radius: 12px; padding: 7px 8px 7px 13px;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .input-wrapper:focus-within {
    border-color: var(--fp-blue); box-shadow: 0 0 0 3px rgba(23,23,23,0.08);
  }
  .chat-input { 
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--fp-text); font-size: 13.5px;
    resize: none; max-height: 120px;
    font-family: 'Plus Jakarta Sans', sans-serif; line-height: 1.5;
  }
  .chat-input::placeholder { color: var(--fp-muted); }
  .input-icons { display: flex; align-items: center; gap: 1px; flex-shrink: 0; }
  .send-btn {
    width: 38px; height: 38px; border-radius: 10px;
    background: var(--fp-blue); border: none; color: #fff;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: all 0.15s; flex-shrink: 0;
    box-shadow: 0 2px 10px rgba(23,23,23,0.25);
  }
  .send-btn:hover { background: var(--fp-blue-dark); transform: scale(1.05); }
  .send-btn:active { transform: scale(0.95); }
  #file-input { display: none; }

  .preview-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(23,23,23,0.75); backdrop-filter: blur(8px);
    z-index: 2100; align-items: center; justify-content: center;
  }
  .preview-overlay.open { display: flex; animation: overlayIn 0.2s ease; }
  .preview-modal {
    background: var(--fp-surface);
    border-radius: 20px; overflow: hidden;
    border: 1px solid var(--fp-border);
    box-shadow: 0 32px 80px rgba(0,0,0,0.3);
    width: min(480px, 92vw);
    animation: modalIn 0.28s cubic-bezier(0.34, 1.4, 0.64, 1);
    display: flex; flex-direction: column;
  }
  .preview-header {
    padding: 14px 18px; display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid var(--fp-border); background: var(--fp-sidebar);
  }
  .preview-title { font-size: 13.5px; font-weight: 700; color: var(--fp-text); display: flex; align-items: center; gap: 8px; }
  .preview-title svg { color: var(--fp-blue); }
  .preview-body { padding: 20px; display: flex; flex-direction: column; align-items: center; gap: 16px; }
  .preview-img-wrap {
    width: 100%; border-radius: 12px; overflow: hidden;
    border: 1px solid var(--fp-border); background: var(--fp-sidebar);
    max-height: 300px; display: flex; align-items: center; justify-content: center;
  }
  .preview-img-wrap img { width: 100%; height: 100%; object-fit: contain; max-height: 300px; display: block; }
  .preview-video-wrap {
    width: 100%; border-radius: 12px; overflow: hidden;
    border: 1px solid var(--fp-border); background: #000;
  }
  .preview-video-wrap video { width: 100%; max-height: 280px; display: block; }
  .preview-file-meta {
    width: 100%; display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; background: var(--fp-sidebar);
    border-radius: 10px; border: 1px solid var(--fp-border);
  }
  .preview-file-icon {
    width: 40px; height: 40px; border-radius: 10px;
    background: var(--fp-blue-light); display: flex; align-items: center; justify-content: center;
    font-size: 20px; flex-shrink: 0;
  }
  .preview-file-name { font-size: 13px; font-weight: 600; color: var(--fp-text); word-break: break-all; }
  .preview-file-size { font-size: 11.5px; color: var(--fp-muted); margin-top: 2px; }
  .preview-caption-wrap { width: 100%; }
  .preview-caption-label { font-size: 11px; font-weight: 600; color: var(--fp-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.06em; }
  .preview-caption {
    width: 100%; border: 1.5px solid var(--fp-border); border-radius: 10px;
    padding: 9px 13px; font-size: 13.5px; outline: none; resize: none;
    font-family: 'Plus Jakarta Sans', sans-serif; color: var(--fp-text);
    background: var(--fp-surface); transition: border-color 0.15s, box-shadow 0.15s;
  }
  .preview-caption:focus { border-color: var(--fp-blue); box-shadow: 0 0 0 3px rgba(23,23,23,0.08); }
  .preview-caption::placeholder { color: var(--fp-muted); }
  .preview-actions {
    padding: 14px 18px; display: flex; align-items: center; gap: 10px;
    border-top: 1px solid var(--fp-border); background: var(--fp-sidebar);
  }
  .preview-cancel {
    flex: 1; padding: 9px; border: 1.5px solid var(--fp-border); border-radius: 10px;
    font-size: 13px; font-weight: 600; color: var(--fp-text);
    background: var(--fp-surface); cursor: pointer; transition: all 0.15s;
  }
  .preview-cancel:hover { background: var(--fp-hover); border-color: #c5ccd3; }
  .preview-send {
    flex: 2; padding: 9px; border: none; border-radius: 10px;
    font-size: 13px; font-weight: 600; color: #fff;
    background: var(--fp-blue); cursor: pointer; transition: all 0.15s;
    display: flex; align-items: center; justify-content: center; gap: 6px;
    box-shadow: 0 2px 10px rgba(23,23,23,0.25);
  }
  .preview-send:hover { background: var(--fp-blue-dark); }

  /* Compact view mode for received media */
  .preview-overlay.view-mode .preview-modal {
    width: min(900px, 95vw);
    height: min(600px, 90vh);
    border-radius: 16px;
    max-width: 95vw;
    max-height: 90vh;
  }
  .preview-overlay.view-mode .preview-header .preview-title {
    font-size: 13px;
  }
  .preview-overlay.view-mode .preview-body {
    padding: 0;
    height: calc(100% - 48px);
    display: flex;
    align-items: center;
    justify-content: center;
    background: #000;
  }
  .preview-overlay.view-mode .preview-img-wrap,
  .preview-overlay.view-mode .preview-video-wrap {
    max-height: 100%;
    border-radius: 0;
    border: none;
    background: #000;
  }
  .preview-overlay.view-mode .preview-img-wrap img,
  .preview-overlay.view-mode .preview-video-wrap video {
    max-width: 100%;
    max-height: 100%;
  }
  .preview-overlay.view-mode .preview-actions,
  .preview-overlay.view-mode .preview-caption-wrap,
  .preview-overlay.view-mode .preview-file-meta {
    display: none;
  }

  #propms-tenant-chat-root * {
    scrollbar-width: thin; scrollbar-color: var(--fp-border) transparent;
  }
</style>

<div class="preview-overlay" id="previewOverlay">
  <div class="preview-modal">
    <div class="preview-header">
      <div class="preview-title"></div>
      <button class="icon-btn" onclick="closePreview()" style="width:28px;height:28px">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="preview-body" id="previewBody"></div>
    <div class="preview-actions">
      <button class="preview-cancel" onclick="closePreview()">Cancel</button>
      <button class="preview-send" onclick="confirmSendMedia()">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        Send
      </button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="chatModal">
  <div class="chat-modal" id="chatModalInner">

    <div class="chat-header">
      <div class="avatar-wrap">
        <div class="avatar-inner" id="tenantAvatar">IS</div>
        <div class="status-dot"></div>
      </div>
      <div style="flex:1;min-width:0">
        <div class="header-name" id="tenantName" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Issue</div>
        <div class="header-sub" id="tenantStatus" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>
      </div>
      <button class="icon-btn" id="toggleFullscreenBtn" onclick="toggleChatFullscreen()" title="Toggle Full Screen">
        <svg id="fullscreenIconEnter" xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
        <svg id="fullscreenIconExit" xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="display:none"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
      </button>
      <button class="icon-btn" onclick="closeChat()" title="Close">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <div class="messages-area" id="messagesArea">
      <div class="drop-zone-overlay" id="dropZoneOverlay">
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" fill="none" stroke="#171717" stroke-width="1.5" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <div class="drop-zone-label">Drop to attach media</div>
        <div class="drop-zone-sub">Images and videos supported</div>
      </div>

      <div class="msg-row typing-row" id="typingIndicator">
        <div class="mini-avatar" id="typingAvatar">?</div>
        <div>
          <div class="typing-name" id="typingName"></div>
          <div class="typing-dots"><span></span><span></span><span></span></div>
        </div>
      </div>
    </div>

    <div class="reply-strip" id="replyStrip">
      <div style="color:var(--fp-blue);font-size:18px;flex-shrink:0">↩</div>
      <div class="reply-strip-content">
        <div class="reply-strip-author" id="replyAuthor"></div>
        <div class="reply-strip-text" id="replyText"></div>
      </div>
      <button class="strip-close" onclick="cancelReply()">✕</button>
    </div>

    <div class="edit-strip" id="editStrip">
      <div style="color:#F0A500;font-size:16px;flex-shrink:0">✏️</div>
      <div class="edit-strip-label">Editing message</div>
      <button class="strip-close" style="color:#b08000" onclick="cancelEdit()">✕</button>
    </div>

    <div class="input-area" id="inputArea">

      <div class="mention-popup" id="mentionPopup">
        <div class="mention-popup-header">
          <div class="mention-popup-title">
            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.92 7.94"/></svg>
            Mention someone
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="mention-count" id="mentionCount">5</span>
            <span class="mention-kbd"><span class="kbd">↑↓</span> navigate&nbsp;&nbsp;<span class="kbd">↵</span> select&nbsp;&nbsp;<span class="kbd">Esc</span> close</span>
          </div>
        </div>
        <div id="mentionList"></div>
      </div>

      <div class="emoji-picker-wrap" id="emojiPickerWrap"></div>

      <div class="input-row">
        <div class="input-wrapper">
          <textarea class="chat-input" id="chatInput"
            placeholder="Type a message... (@ to mention)"
            rows="1"
            onkeydown="handleKeydown(event)"
            oninput="handleInput(this)"></textarea>
          <div class="input-icons">
            <button class="icon-btn" style="width:32px;height:32px" id="emojiBtn" onclick="toggleEmojiPicker(event)" title="Emoji">
              <svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
            </button>
            <button class="icon-btn" style="width:32px;height:32px" onclick="triggerFileInput()" title="Attach">
              <svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
            </button>
            <input type="file" id="file-input" accept="image/*,video/*" onchange="handleFileInput(event)"/>
          </div>
        </div>
        <button class="send-btn" onclick="sendOrEdit()">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>

  </div>
</div>
`;

    document.body.appendChild(wrapper);

    // JS logic ported from your original demo, slightly adapted for Frappe

    window.PEOPLE = [
      { name: 'Aria Chen',   initials: 'AC', role: 'Support Agent',   roleColor: '#171717', color: 'linear-gradient(135deg,#171717,#000000)' },
      { name: 'Bob Smith',   initials: 'BS', role: 'Developer',        roleColor: '#2DB84B', color: 'linear-gradient(135deg,#2DB84B,#1a8c35)' },
      { name: 'Carol White', initials: 'CW', role: 'Designer',         roleColor: '#F8A100', color: 'linear-gradient(135deg,#F8A100,#c07a00)' },
      { name: 'Dave Kim',    initials: 'DK', role: 'Product Manager',  roleColor: '#E24C4C', color: 'linear-gradient(135deg,#E24C4C,#b53030)' },
      { name: 'Eve Torres',  initials: 'ET', role: 'QA Engineer',      roleColor: '#8b5cf6', color: 'linear-gradient(135deg,#8b5cf6,#6d28d9)' },
    ];

    window.msgIdCounter = 0;
    window.replyTo = null;
    window.editingId = null;
    window.isMentioning = false;
    window.mentionAtPos = -1;
    window.mentionSelectedIdx = 0;
    window.pendingFileData = null;
    window.pendingFileName = null;
    window.pendingFileSize = null;
    window.pendingFileType = null;
    window.pendingFileObject = null;
    window.dragCounter = 0;
    window.currentIssueId = null;
    window.typingTimer = null;
    window.typingLastSent = 0;
    window.isTypingActive = false;
    window._typingAutoHide = null;

    window.msgStore = {};

    function clearMessagesArea() {
      $('#messagesArea .msg-row').not('.typing-row').remove();
      window.msgStore = {};
      window.msgIdCounter = 0;
    }

    window.openMediaUrlPreview = function (url) {
      if (!url) return;
      var lower = String(url).toLowerCase();
      var isImage = lower.match(/\.(png|jpe?g|gif|webp)$/);
      var isVideo = lower.match(/\.(mp4|webm|ogg|mov|mkv)$/);

      var bodyHtml = '';
      if (isImage) {
        bodyHtml =
          '<div class="preview-img-wrap">' +
            '<img src="' + esc(url) + '" alt="preview"/>' +
          '</div>';
      } else if (isVideo) {
        bodyHtml =
          '<div class="preview-video-wrap">' +
            '<video src="' + esc(url) + '" controls></video>' +
          '</div>';
      } else {
        var name = url.split('/').pop() || url;
        bodyHtml =
          '<div class="preview-file-meta">' +
            '<div class="preview-file-icon">📎</div>' +
            '<div>' +
              '<div class="preview-file-name">' + esc(name) + '</div>' +
              '<div class="preview-file-size">' + esc(url) + '</div>' +
            '</div>' +
          '</div>';
      }

      $('#previewBody').html(bodyHtml);
      $('#previewOverlay').addClass('open view-mode');
      pendingFileData = null;
      pendingFileName = null;
      pendingFileSize = null;
      pendingFileType = null;
      pendingFileObject = null;
    };

    function renderBackendMessage(comm) {
      if (!comm) return;

      var backendIdx = comm.idx || null;
      if (backendIdx) {
        var existing = $('.msg-row[data-backend-idx="' + backendIdx + '"]');
        if (existing.length) {
          return; // already rendered this backend message
        }
      }

      var isOutgoing = (comm.sender === frappe.session.user);
      var id = ++msgIdCounter;

      var author =
        comm.sender_full_name ||
        comm.sender ||
        (isOutgoing ? __('You') : __('User'));

      var text = comm.message_content || '';
      msgStore[id] = {
        author: author,
        text: text,
        backendIdx: comm.idx || null
      };

      var timeStr;
      if (frappe.datetime && (comm.time_stamp || comm.creation)) {
        var ts = comm.time_stamp || comm.creation;
        try {
          timeStr = frappe.datetime
            .convert_to_user_tz(ts)
            .toString()
            .slice(11, 16);
        } catch (e) {
          timeStr = nowTime();
        }
      } else {
        timeStr = nowTime();
      }

      var bubbleClass = isOutgoing ? 'outgoing' : 'incoming';
      var extraAvatar =
        isOutgoing
          ? ''
          : '<div class="mini-avatar">' +
            esc((author || 'U').substring(0, 2).toUpperCase()) +
            '</div>';

      var mediaUrl = comm.image_attachment || comm.image || comm.attachment || null;
      var bubblesHtml = '';

      if (mediaUrl) {
        var lower = String(mediaUrl).toLowerCase();
        if (lower.match(/\.(png|jpe?g|gif|webp)$/)) {
          bubblesHtml +=
            '<div class="bubble ' + bubbleClass + ' media">' +
              '<img src="' + esc(mediaUrl) + '" alt="Image"' +
                ' style="cursor:pointer"' +
                ' data-url="' + esc(mediaUrl) + '"' +
                ' onclick="openMediaUrlPreview(this.getAttribute(\'data-url\'))"' +
              '/>' +
            '</div>';
        } else if (lower.match(/\.(mp4|webm|ogg|mov|mkv)$/)) {
          bubblesHtml +=
            '<div class="bubble ' + bubbleClass + ' media">' +
              '<video src="' + esc(mediaUrl) + '" controls' +
                ' style="max-width:220px;border-radius:12px;display:block;border:1px solid var(--fp-border);cursor:pointer"' +
                ' data-url="' + esc(mediaUrl) + '"' +
                ' onclick="openMediaUrlPreview(this.getAttribute(\'data-url\'))"' +
              '></video>' +
            '</div>';
        } else if (lower.match(/\.(mp3|wav|m4a|aac|flac)$/)) {
          bubblesHtml +=
            '<div class="bubble ' + bubbleClass + ' media">' +
              '<audio src="' + esc(mediaUrl) + '" controls style="max-width:220px;display:block;"></audio>' +
            '</div>';
        } else {
          bubblesHtml +=
            '<div class="bubble ' + bubbleClass + '">' +
              '<a href="' + esc(mediaUrl) + '" target="_blank" rel="noopener noreferrer">' +
                esc(mediaUrl.split('/').pop() || mediaUrl) +
              '</a>' +
            '</div>';
        }
      }

      if (text) {
        var replyHtml = '';
        if (comm.reply_to_idx && (comm.quoted_sender || comm.quoted_content)) {
          var qSender = (comm.quoted_sender || '').replace(/@.*/, '');
          var qText   = (comm.quoted_content || '').slice(0, 120);
          replyHtml =
            '<div class="reply-preview">' +
              '<div class="reply-author">' + esc(qSender) + '</div>' +
              '<div class="reply-text">'   + esc(qText)   + '</div>' +
            '</div>';
        }
        var emojiCls = (isEmojiOnly(text) && !replyHtml) ? ' emoji-only' : '';
        bubblesHtml +=
          '<div class="bubble ' + bubbleClass + emojiCls + '">' +
            replyHtml +
            formatText(text) +
            (comm.is_edited ? ' <span class="edited-label">(edited)</span>' : '') +
          '</div>';
      }

      if (!bubblesHtml) {
        var emojiCls2 = isEmojiOnly(text || '') ? ' emoji-only' : '';
        bubblesHtml =
          '<div class="bubble ' + bubbleClass + emojiCls2 + '">' +
            formatText(text || '') +
          '</div>';
      }

      var rowHtml =
        '<div class="msg-row ' + bubbleClass + '" data-id="' + id + '"' +
          (backendIdx ? ' data-backend-idx="' + backendIdx + '"' : '') +
          ' style="animation-delay:0s">' +
          extraAvatar +
          '<div class="bubble-wrap">' +
            bubblesHtml +
            (isOutgoing
              ? '<div class="bubble-footer"><span class="bubble-time">' + timeStr + '</span><span class="tick">✓✓</span></div>'
              : '<div class="bubble-time">' + timeStr + '</div>') +
            actionsHTML(id, isOutgoing) +
          '</div>' +
        '</div>';

      $(rowHtml).insertBefore('#typingIndicator');
      scrollToBottom();
    }

    function loadMessagesFromServer(clearArea) {
      if (!window.currentIssueId) return;

      if (clearArea !== false) clearMessagesArea();

      var channel = window.__propms_issue_chat_channel || 'tenant_support';
      frappe.call({
        method: 'propms.api.v1.job_card.job_card.get_ticket_communications',
        args: {
          ticket_id: window.currentIssueId,
          channel: channel,
          limit: 50,
          offset: 0
        },
        freeze: false,
        callback: function (r) {
          if (!r.message || r.message.status !== 'success') return;
          var comms = r.message.communications || [];
          comms.forEach(function (c) {
            renderBackendMessage(c);
          });
        }
      });
    }

    function pollNewMessages() {
      if (!window.currentIssueId) return;
      loadMessagesFromServer(false);
    }

    function setupRealtimeForIssue() {
      if (!frappe.realtime || !window.currentIssueId) return;

      var ticketId = window.currentIssueId;

      if (frappe.realtime.off) {
        frappe.realtime.off('ticket_message');
        frappe.realtime.off('ticket_message_edited');
        frappe.realtime.off('ticket_typing');
      }

      frappe.realtime.on('ticket_message', function (data) {
        if (!data || data.ticket_id !== ticketId || !data.communication) return;
        renderBackendMessage(data.communication);
      });

      frappe.realtime.on('ticket_message_edited', function (data) {
        if (!data || data.ticket_id !== ticketId || !data.communication) return;
        var comm = data.communication;
        var $row = $('.msg-row[data-backend-idx="' + comm.idx + '"]');
        if (!$row.length) return;
        var localId = parseInt($row.attr('data-id'));
        if (msgStore[localId]) msgStore[localId].text = comm.message_content;
        var $bubble = $row.find('.bubble:not(.media)');
        var rHTML = $bubble.find('.reply-preview').prop('outerHTML') || '';
        $bubble.html(rHTML + formatText(comm.message_content) + ' <span class="edited-label">(edited)</span>');
      });

      frappe.realtime.on('ticket_typing', function (data) {
        if (!data || data.ticket_id !== ticketId) return;
        if (data.user === frappe.session.user) return;
        clearTimeout(window._typingAutoHide);
        if (data.is_typing) {
          var name = data.user_full_name || data.user || 'Someone';
          $('#typingName').text(name + ' is typing…');
          $('#typingAvatar').text((name).substring(0, 2).toUpperCase());
          showTyping();
          window._typingAutoHide = setTimeout(hideTyping, 5000);
        } else {
          hideTyping();
        }
      });

      frappe.call({
        method: 'propms.api.v1.job_card.job_card.subscribe_to_ticket_room',
        args: { ticket_id: ticketId },
        freeze: false
      });
    }

    function initEmojiPicker() {
      var Picker = window._EmojiMartPicker;
      var data   = window._EmojiMartData;
      if (!Picker || !data) return;
      var picker = new Picker({
        data: data,
        onEmojiSelect: function(emoji) {
          var input = document.getElementById('chatInput');
          var s = input.selectionStart, e = input.selectionEnd;
          input.value = input.value.slice(0, s) + emoji.native + input.value.slice(e);
          var pos = s + emoji.native.length;
          input.setSelectionRange(pos, pos);
          input.focus();
          closeEmojiPicker();
        },
        theme: 'light',
        previewPosition: 'none',
        skinTonePosition: 'none',
        set: 'native',
      });
      document.getElementById('emojiPickerWrap').appendChild(picker);
    }

    window.addEventListener('emoji-mart-ready', function() { initEmojiPicker(); }, { once: true });
    if (window._EmojiMartPicker) initEmojiPicker();

    window.openChat = function (frm) {
      const modal = document.getElementById('chatModal');
      modal.classList.add('open');
      setChatFullscreen(false);

      const nameEl = document.getElementById('tenantName');
      const avatarEl = document.getElementById('tenantAvatar');
      const statusEl = document.getElementById('tenantStatus');
      if (frm && frm.doc) {
        window.currentIssueId = frm.doc.name;
        var subject = frm.doc.subject || frm.doc.description || frm.doc.name || 'Issue';
        var plainSubject = subject.replace(/<[^>]*>/g, '').trim() || 'Issue';
        nameEl.textContent = plainSubject;
        avatarEl.textContent = plainSubject.substring(0, 2).toUpperCase();
        if (statusEl) statusEl.textContent = frm.doc.name || '';
      }

      loadMessagesFromServer();
      setupRealtimeForIssue();

      clearInterval(window._chatPollTimer);
      window._chatPollTimer = setInterval(pollNewMessages, 15000);

      scrollToBottom();
      setTimeout(function() { document.getElementById('chatInput').focus(); }, 300);
    };

    window.closeChat = function () {
      document.getElementById('chatModal').classList.remove('open');
      setChatFullscreen(false);
      clearInterval(window._chatPollTimer);
      window._chatPollTimer = null;
      closeEmojiPicker();
      closeMentionPopup();
    };

    window.setChatFullscreen = function (enabled) {
      var modalInner = document.getElementById('chatModalInner');
      var enterIcon = document.getElementById('fullscreenIconEnter');
      var exitIcon = document.getElementById('fullscreenIconExit');
      if (!modalInner) return;
      modalInner.classList.toggle('fullscreen', !!enabled);
      if (enterIcon) enterIcon.style.display = enabled ? 'none' : '';
      if (exitIcon) exitIcon.style.display = enabled ? '' : 'none';
    };

    window.toggleChatFullscreen = function () {
      var modalInner = document.getElementById('chatModalInner');
      if (!modalInner) return;
      setChatFullscreen(!modalInner.classList.contains('fullscreen'));
    };

    window.closeEmojiPicker = function () { $('#emojiPickerWrap').removeClass('open'); };

    window.toggleEmojiPicker = function (e) {
      e.stopPropagation();
      $('#emojiPickerWrap').toggleClass('open');
      if ($('#emojiPickerWrap').hasClass('open')) closeMentionPopup();
    };

    window.scrollToBottom = function () {
      setTimeout(function() {
        var a = document.getElementById('messagesArea');
        a.scrollTop = a.scrollHeight;
      }, 60);
    };

    window.handleInput = function (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
      var val = el.value, cursor = el.selectionStart;
      var textToCursor = val.slice(0, cursor);
      var atMatch = textToCursor.match(/@(\w*)$/);
      if (atMatch) {
        isMentioning = true;
        mentionAtPos = textToCursor.lastIndexOf('@');
        mentionSelectedIdx = 0;
        renderMentionPopup(atMatch[1].toLowerCase());
        $('#mentionPopup').addClass('open');
        closeEmojiPicker();
      } else {
        isMentioning = false;
        mentionAtPos = -1;
        closeMentionPopup();
      }
      // Typing indicator
      if (el.value.trim()) {
        sendTypingStart();
        clearTimeout(typingTimer);
        typingTimer = setTimeout(sendTypingStop, 4000);
      } else {
        sendTypingStop();
      }
    };

    window.renderMentionPopup = function (q) {
      var filtered = PEOPLE.filter(function(p) { return p.name.toLowerCase().includes(q); });
      $('#mentionCount').text(filtered.length);
      if (!filtered.length) {
        $('#mentionList').html('<div class="mention-item no-results">No people found matching "<strong>' + q + '</strong>"</div>');
        return;
      }
      var html = filtered.map(function(p, i) {
        return '<div class="mention-item' + (i === mentionSelectedIdx ? ' active' : '') + '" data-name="' + p.name + '" data-idx="' + i + '">' +
          '<div class="m-avatar" style="background:' + p.color + '">' + p.initials + '</div>' +
          '<div class="m-info"><div class="m-name">' + p.name + '</div>' +
          '<div class="m-role"><span class="m-role-dot" style="background:' + p.roleColor + '"></span>' + p.role + '</div></div>' +
          '<span class="mention-arrow">→</span></div>';
      }).join('');
      $('#mentionList').html(html);
      $('#mentionList .mention-item').on('click', function() { insertMention($(this).data('name')); });
    };

    window.closeMentionPopup = function () {
      $('#mentionPopup').removeClass('open');
      isMentioning = false;
    };

    window.insertMention = function (name) {
      var input = document.getElementById('chatInput');
      var before = input.value.slice(0, mentionAtPos);
      var after  = input.value.slice(input.selectionStart);
      input.value = before + '@' + name + ' ' + after;
      var pos = (before + '@' + name + ' ').length;
      input.setSelectionRange(pos, pos);
      input.focus();
      closeMentionPopup();
      isMentioning = false; mentionAtPos = -1;
    };

    window.handleKeydown = function (e) {
      if (isMentioning) {
        var $items = $('#mentionList .mention-item:not(.no-results)');
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          mentionSelectedIdx = Math.min(mentionSelectedIdx + 1, $items.length - 1);
          $items.removeClass('active').eq(mentionSelectedIdx).addClass('active');
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          mentionSelectedIdx = Math.max(mentionSelectedIdx - 1, 0);
          $items.removeClass('active').eq(mentionSelectedIdx).addClass('active');
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          var name = $items.eq(mentionSelectedIdx).data('name');
          if (name) { insertMention(name); return; }
        }
      }
      if (e.key === 'Escape') {
        closeMentionPopup(); closeEmojiPicker();
        cancelReply(); cancelEdit();
      }
      if (e.key === 'Enter' && !e.shiftKey && !isMentioning) {
        e.preventDefault(); sendOrEdit();
      }
    };

    window.formatText = function (text) {
      return esc(text).replace(/@([\w ]+?)(?=[\s,!?.]|$)/g, function(m, name) {
        return '<span class="mention">@' + name + '</span>';
      });
    };
    window.esc = function (t) {
      return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    };
    window.isEmojiOnly = function (text) {
      var t = text.trim();
      if (!t) return false;
      var stripped = t
        .replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, '') // surrogate pairs (most modern emoji)
        .replace(/[\u2000-\u3300]/g, '')                 // misc symbols & arrows
        .replace(/[\u00A9\u00AE]/g, '')                  // © ®
        .replace(/[\uFE00-\uFE0F]/g, '')                 // variation selectors
        .replace(/\u200D/g, '')                          // ZWJ
        .replace(/\u20E3/g, '')                          // combining enclosing keycap
        .replace(/\s/g, '');
      return stripped.length === 0;
    };
    window.actionsHTML = function (id, isOut) {
      var menuId = 'msg-actions-menu-' + id;
      var items = '<button class="action-item" type="button" onclick="startReply(' + id + '); closeMsgActionsMenu(\'' + menuId + '\')">↩ ' + __('Reply') + '</button>';
      if (isOut) {
        items += '<button class="action-item" type="button" onclick="startEdit(' + id + '); closeMsgActionsMenu(\'' + menuId + '\')">✏️ ' + __('Edit') + '</button>';
      }
      return (
        '<div class="msg-actions">' +
          '<button class="msg-actions-trigger" type="button" onclick="toggleMsgActionsMenu(\'' + menuId + '\', this)">⋯</button>' +
          '<div class="msg-actions-menu" id="' + menuId + '">' +
            items +
          '</div>' +
        '</div>'
      );
    };

    window.toggleMsgActionsMenu = function (menuId, btn) {
      var $menu = $('#' + menuId);
      if (!$menu.length) return;
      $('.msg-actions-menu.open').not($menu).removeClass('open');
      $menu.toggleClass('open');

      if ($menu.hasClass('open')) {
        // Decide whether to show above or below based on available space
        $menu.removeClass('below');
        var rect = $menu[0].getBoundingClientRect();
        var spaceAbove = rect.top;
        var spaceBelow = window.innerHeight - rect.bottom;
        if (spaceAbove < 80 && spaceBelow > spaceAbove) {
          // Not much room above; keep default (below trigger)
          $menu.removeClass('below');
        } else if (spaceAbove > spaceBelow) {
          // More room above than below – show menu above the trigger
          $menu.addClass('below');
        }
      }
    };

    window.closeMsgActionsMenu = function (menuId) {
      $('#' + menuId).removeClass('open');
    };

    window.sendTypingStart = function () {
      if (!window.currentIssueId || isTypingActive) return;
      isTypingActive = true;
      typingLastSent = Date.now();
      frappe.call({
        method: 'propms.api.v1.job_card.job_card.send_typing_indicator',
        args: { ticket_id: window.currentIssueId, is_typing: true },
        freeze: false
      });
    };

    window.sendTypingStop = function () {
      clearTimeout(typingTimer);
      typingTimer = null;
      if (!window.currentIssueId || !isTypingActive) return;
      isTypingActive = false;
      frappe.call({
        method: 'propms.api.v1.job_card.job_card.send_typing_indicator',
        args: { ticket_id: window.currentIssueId, is_typing: false },
        freeze: false
      });
    };

    window.sendOrEdit = function () {
      var input = document.getElementById('chatInput');
      var text = input.value.trim();
      if (!text) return;
      sendTypingStop();
      if (editingId) applyEdit(editingId, text);
      else sendMessage(text);
      input.value = ''; input.style.height = 'auto';
      closeMentionPopup();
    };

    window.sendMessage = function (text) {
      if (!text || !window.currentIssueId) return;

      var replyIdx = (replyTo && replyTo.backendIdx) ? replyTo.backendIdx : null;
      var savedReplyTo = replyTo ? { author: replyTo.author, text: replyTo.text, backendIdx: replyTo.backendIdx } : null;

      var channel = window.__propms_issue_chat_channel || 'tenant_support';
      frappe.call({
        method: 'propms.api.v1.job_card.job_card.send_ticket_communication',
        args: {
          ticket_id: window.currentIssueId,
          message_content: text,
          reply_to_idx: replyIdx,
          channel: channel
        },
        freeze: false,
        callback: function (r) {
          if (!r.message || r.message.status !== 'success') {
            frappe.msgprint(__('Failed to send message'));
            return;
          }
          cancelReply();
          var comm = r.message.communication;
          if (comm) {
            if (savedReplyTo && savedReplyTo.backendIdx) {
              comm.reply_to_idx = savedReplyTo.backendIdx;
              comm.quoted_sender = savedReplyTo.author;
              comm.quoted_content = savedReplyTo.text;
            }
            renderBackendMessage(comm);
          }
        }
      });
    };

    window.startReply = function (id) {
      var msg = msgStore[id]; if (!msg) return;
      replyTo = { id: id, author: msg.author, text: msg.text, backendIdx: msg.backendIdx };
      $('#replyAuthor').text(msg.author);
      $('#replyText').text(msg.text.slice(0, 90));
      $('#replyStrip').addClass('open');
      cancelEdit();
      document.getElementById('chatInput').focus();
    };
    window.cancelReply = function () { replyTo = null; $('#replyStrip').removeClass('open'); };

    window.startEdit = function (id) {
      var msg = msgStore[id]; if (!msg) return;
      editingId = id;
      var input = document.getElementById('chatInput');
      input.value = msg.text;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
      $('#editStrip').addClass('open');
      cancelReply();
      input.focus();
    };
    window.applyEdit = function (id, newText) {
      var msg = msgStore[id];
      if (!msg || !msg.backendIdx || !window.currentIssueId) return;
      frappe.call({
        method: 'propms.api.v1.job_card.job_card.edit_ticket_communication',
        args: {
          ticket_id: window.currentIssueId,
          communication_idx: msg.backendIdx,
          new_message_content: newText
        },
        freeze: false,
        callback: function (r) {
          if (!r.message || r.message.status !== 'success') {
            frappe.msgprint(__('Failed to edit message'));
            return;
          }
          msgStore[id].text = newText;
          var $row = $('.msg-row[data-id="' + id + '"]');
          var $bubble = $row.find('.bubble:not(.media)');
          var rHTML = $bubble.find('.reply-preview').prop('outerHTML') || '';
          $bubble.html(rHTML + formatText(newText) + ' <span class="edited-label">(edited)</span>');
          editingId = null;
          $('#editStrip').removeClass('open');
        }
      });
    };
    window.cancelEdit = function () {
      editingId = null; $('#editStrip').removeClass('open');
      $('#chatInput').val('').css('height', 'auto');
    };

    window.showTyping = function (name) {
      if (name) {
        $('#typingName').text(name + ' is typing…');
        $('#typingAvatar').text(name.substring(0, 2).toUpperCase());
      }
      $('#typingIndicator').show();
      scrollToBottom();
    };
    window.hideTyping = function () { $('#typingIndicator').hide(); };

    window.triggerFileInput = function () { document.getElementById('file-input').click(); };
    window.handleFileInput = function (event) {
      var file = event.target.files[0];
      if (!file) return;
      openMediaPreview(file);
      event.target.value = '';
    };

    window.openMediaPreview = function (file) {
      pendingFileObject = file;
      pendingFileName = file.name;
      pendingFileSize = formatBytes(file.size);
      pendingFileType = file.type;
      var reader = new FileReader();
      reader.onload = function(e) {
        pendingFileData = e.target.result;
        buildPreviewBody(file, e.target.result);
        $('#previewOverlay').removeClass('view-mode').addClass('open');
      };
      reader.readAsDataURL(file);
    };

    window.buildPreviewBody = function (file, dataUrl) {
      var mediaHTML = '';
      if (file.type.startsWith('image/')) {
        mediaHTML = '<div class="preview-img-wrap"><img src="' + dataUrl + '" alt="preview"/></div>';
      } else if (file.type.startsWith('video/')) {
        mediaHTML = '<div class="preview-video-wrap"><video src="' + dataUrl + '" controls></video></div>';
      }
      var ext = file.name.split('.').pop().toUpperCase();
      $('#previewBody').html(
        mediaHTML +
        '<div class="preview-file-meta">' +
          '<div class="preview-file-icon">' + getFileEmoji(file.type) + '</div>' +
          '<div><div class="preview-file-name">' + esc(file.name) + '</div>' +
          '<div class="preview-file-size">' + formatBytes(file.size) + ' · ' + ext + '</div></div>' +
        '</div>' +
        '<div class="preview-caption-wrap">' +
          '<div class="preview-caption-label">Add a caption</div>' +
          '<textarea class="preview-caption" id="previewCaption" rows="2" placeholder="Add a caption (optional)…"></textarea>' +
        '</div>'
      );
    };

    window.closePreview = function () {
      $('#previewOverlay').removeClass('open view-mode');
      pendingFileData = null; pendingFileName = null; pendingFileSize = null; pendingFileObject = null;
    };

    window.confirmSendMedia = function () {
      if (!pendingFileObject || !window.currentIssueId) return;
      var caption = ($('#previewCaption').val() || '').trim();

      var file = pendingFileObject;
      var formData = new FormData();
      formData.append('file', file);
      formData.append('is_private', '0');
      formData.append('folder', 'Home');

      fetch('/api/method/upload_file', {
        method: 'POST',
        body: formData,
        headers: {
          'X-Frappe-CSRF-Token': frappe.csrf_token
        }
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!data || !data.message || !data.message.file_url) {
            frappe.msgprint(__('Failed to upload file'));
            return;
          }

          var fileData = data.message;
          var fileUrl = fileData.file_url;
          var name = fileData.file_name || file.name;
          var lower = (fileUrl || '').toLowerCase();
          var isImage = lower.match(/\.(png|jpe?g|gif|webp)$/);

          var messageText = caption || (isImage ? '📷 ' + name : '📎 ' + name);

          var channel = window.__propms_issue_chat_channel || 'tenant_support';
          frappe.call({
            method: 'propms.api.v1.job_card.job_card.send_ticket_communication',
            args: {
              ticket_id: window.currentIssueId,
              message_content: messageText,
              attachment: fileUrl,
              image_attachment: isImage ? fileUrl : null,
              channel: channel
            },
            freeze: false,
            callback: function (r) {
              if (!r.message || r.message.status !== 'success') {
                frappe.msgprint(__('Failed to send media message'));
                return;
              }
              closePreview();
            }
          });
        })
        .catch(function (err) {
          frappe.msgprint(__('Error uploading file'));
          // keep preview open so user can retry/close
        });
    };

    window.getFileEmoji = function (type) {
      if (type.startsWith('image/')) return '🖼️';
      if (type.startsWith('video/')) return '🎬';
      return '📎';
    };
    window.formatBytes = function (bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
      return (bytes/1024/1024).toFixed(1) + ' MB';
    };
    window.nowTime = function () {
      return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    };

    (function setupDragAndClicks() {
      var $area = $('#messagesArea');
      var $input = $('#chatInput');

      function handleAttachmentFile(file) {
        if (!file) return false;
        if (!file.type.startsWith('image/') && !file.type.startsWith('video/')) {
          frappe.msgprint(__('Only image and video files are supported.'));
          return false;
        }
        openMediaPreview(file);
        return true;
      }

      $area.on('dragenter', function(e) {
        e.preventDefault(); e.stopPropagation();
        dragCounter++;
        $(this).addClass('drag-over');
        $('#dropZoneOverlay').addClass('visible');
      });
      $area.on('dragover', function(e) { e.preventDefault(); e.stopPropagation(); });
      $area.on('dragleave', function(e) {
        e.preventDefault(); e.stopPropagation();
        dragCounter--;
        if (dragCounter <= 0) {
          dragCounter = 0;
          $(this).removeClass('drag-over');
          $('#dropZoneOverlay').removeClass('visible');
        }
      });
      $area.on('drop', function(e) {
        e.preventDefault(); e.stopPropagation();
        dragCounter = 0;
        $(this).removeClass('drag-over');
        $('#dropZoneOverlay').removeClass('visible');
        var files = e.originalEvent.dataTransfer.files;
        if (!files || !files.length) return;
        handleAttachmentFile(files[0]);
      });

      $input.on('paste', function(e) {
        var oe = e.originalEvent || e;
        var cd = oe.clipboardData;
        if (!cd) return;

        var file = null;
        if (cd.files && cd.files.length) {
          file = cd.files[0];
        } else if (cd.items && cd.items.length) {
          for (var i = 0; i < cd.items.length; i++) {
            var item = cd.items[i];
            if (item && item.kind === 'file') {
              file = item.getAsFile();
              if (file) break;
            }
          }
        }

        if (!file) return; // normal text paste
        e.preventDefault();
        handleAttachmentFile(file);
      });

      // Keep dialog locked while open; close via explicit X button only.
      $('#chatModal').on('click', function(e) {
        if (e.target === this) {
          e.preventDefault();
          e.stopPropagation();
        }
      });

      $(document).on('click', function(e) {
        if (!$(e.target).closest('#emojiPickerWrap, #emojiBtn').length) closeEmojiPicker();
        if (!$(e.target).closest('#mentionPopup, #chatInput').length) closeMentionPopup();
        if (!$(e.target).closest('.msg-actions').length) {
          $('.msg-actions-menu.open').removeClass('open');
        }
      });
      $(document).on('keydown', function(e) {
        if (!$('#chatModal').hasClass('open')) return;
        var isFindShortcut = (e.key && e.key.toLowerCase() === 'f') && (e.ctrlKey || e.metaKey);
        if (isFindShortcut) {
          e.preventDefault();
          toggleChatFullscreen();
          return;
        }
        if (e.key !== 'Escape') return;
        var modalInner = document.getElementById('chatModalInner');
        if (modalInner && modalInner.classList.contains('fullscreen')) {
          e.preventDefault();
          setChatFullscreen(false);
        }
      });

      $('#typingIndicator').hide();
    })();

    window.__propms_tenant_chat_initialized = true;
}