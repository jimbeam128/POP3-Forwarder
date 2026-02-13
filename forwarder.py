import os
import sys
import poplib
import smtplib
import time
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import parseaddr
from email import policy
from email.parser import BytesParser

# ======================
# Helper Funktionen
# ======================
def header_safe(value):
    if not value:
        return ""
    return " ".join(str(value).split())

def decode_and_safe(header_value):
    if not header_value:
        return "(no subject)"
    try:
        return header_safe(str(make_header(decode_header(header_value))))
    except Exception as e:
        print(f"[DEBUG] Subject decode error: {e}")
        return "(invalid subject)"

def get_text_from_part(part):
    """Return safe string from email part, never None"""
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or part.get_charset() or "utf-8"
        if isinstance(payload, bytes):
            return payload.decode(charset, errors="replace")
        return str(payload)
    except Exception as e:
        print(f"[DEBUG] payload decode failed: {e}")
        return ""

# ======================
# POP3 Konfiguration
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']
POP3_TIMEOUT = 30
POP3_RETRIES = 3

# ======================
# SMTP Konfiguration
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']
SMTP_FROM = os.environ['SMTP_FROM']
SMTP_FROM_NAME = "POP3 Forwarder"
TARGET_EMAIL = os.environ['TARGET_EMAIL']

# ======================
# UIDL Schutz
# ======================
UIDL_FILE = "processed_uidls.txt"
processed_uidls = set()
if os.path.exists(UIDL_FILE):
    with open(UIDL_FILE, "r") as f:
        processed_uidls = set(line.strip() for line in f if line.strip())

# ======================
# POP3 Login
# ======================
pop_conn = None
for attempt in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 Login fehlgeschlagen ({attempt+1}): {e}")
        time.sleep(5)

if not pop_conn:
    print("[WARN] POP3 Login endgültig fehlgeschlagen")
    sys.exit(0)

# ======================
# UIDLs abrufen
# ======================
resp, uidl_list, _ = pop_conn.uidl()
uidls = {int(e.decode().split()[0]): e.decode().split()[1] for e in uidl_list}
print(f"{len(uidls)} Mails im Quellpostfach gefunden.")

if not uidls:
    print("Keine Mails vorhanden – SMTP wird nicht aufgebaut.")
    pop_conn.quit()
    sys.exit(0)

# ======================
# SMTP Login
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# Mail-Verarbeitung
# ======================
for i in sorted(uidls.keys()):
    print(f"\n[DEBUG] === Mail {i} ===")
    subject = "(unknown subject)"

    try:
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)
        email_msg = BytesParser(policy=policy.default).parsebytes(raw)

        from_name, from_addr = parseaddr(str(email_msg.get('From', '')))
        reply_name, reply_addr = parseaddr(str(email_msg.get('Reply-To', '')))
        return_path = email_msg.get('Return-Path', '')
        _, return_addr = parseaddr(return_path)
        original_from = reply_addr or from_addr or return_addr or "unknown@example.com"

        sender_name = (
            header_safe(from_name)
            or header_safe(reply_name)
            or original_from.split("@")[0]
        )

        subject = decode_and_safe(email_msg.get('Subject'))

        forward = EmailMessage()
        forward['From'] = f"\"{sender_name}\" <{SMTP_FROM}>"
        forward['To'] = TARGET_EMAIL
        forward['Subject'] = subject
        forward['Reply-To'] = original_from

        # ======================
        # BODY HANDLING (minimaler DHL-Fix)
        # ======================
        body_set = False
        text_body = None
        html_body = None

        if email_msg.is_multipart():
            print("[DEBUG] multipart detected")

            for part in email_msg.walk():
                if part.is_multipart():
                    continue

                ctype = part.get_content_type()
                disposition = part.get_content_disposition()
                text = get_text_from_part(part)

                print(f"[DEBUG] usable part: {ctype}, length={len(text)}")

                if disposition is None:
                    if ctype == "text/plain" and text.strip():
                        text_body = text
                        if not body_set:
                            forward.set_content(text_body)
                            body_set = True

                    elif ctype == "text/html" and text.strip():
                        html_body = text

                elif disposition == "attachment":
                    payload = part.get_payload(decode=True)
                    filename = part.get_filename()
                    if payload:
                        forward.add_attachment(
                            payload,
                            maintype=part.get_content_maintype(),
                            subtype=part.get_content_subtype(),
                            filename=filename
                        )
                        print(f"[DEBUG] Attachment hinzugefügt: {filename}, {len(payload)} Bytes")

        else:
            print("[DEBUG] singlepart detected")
            ctype = email_msg.get_content_type()
            text = get_text_from_part(email_msg)

            if ctype == "text/plain":
                text_body = text or ""
                forward.set_content(text_body)
                body_set = True
            elif ctype == "text/html":
                html_body = text

        # 🔥 DHL / HTML-only Fix
        if html_body and not text_body:
            text_body = (
                "Diese E-Mail enthält HTML-Inhalte.\n\n"
                "Bitte öffnen Sie diese Nachricht in einem HTML-fähigen E-Mail-Client."
            )
            if not body_set:
                forward.set_content(text_body)
                body_set = True

        if html_body:
            def rfc_safe_lines(text, maxlen=900):
                return "\r\n".join(text[i:i+maxlen] for i in range(0, len(text), maxlen))
            safe_html = rfc_safe_lines(html_body)
            forward.add_alternative(safe_html, subtype="html")

        # ======================
        # Mail senden & löschen
        # ======================
        smtp.send_message(forward)
        pop_conn.dele(i)
        print(f"[OK] Mail {i} weitergeleitet & gelöscht")

    except Exception as e:
        safe_subject = subject if subject else "(no subject)"
        print(f"[FEHLER] Mail {i} | Subject: {safe_subject} | Error: {e}")
        try:
            pop_conn.rset()
        except Exception:
            pass

# ======================
# Cleanup
# ======================
smtp.quit()
pop_conn.quit()
print("\nAlle Mails verarbeitet.")
