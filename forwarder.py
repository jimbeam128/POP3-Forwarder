import os
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

# 🔥 FIX: robuste Text-Extraktion (decode=True → Fallback)
def get_text_from_part(part, mail_index, part_index):
    charset = part.get_content_charset() or "utf-8"

    payload = part.get_payload(decode=True)

    if payload is None:
        print(f"[DEBUG] Mail {mail_index} Part {part_index}: decode=True returned None, fallback")
        payload = part.get_payload()

        if payload is None:
            print(f"[WARN] Mail {mail_index} Part {part_index}: payload still None")
            return ""

        if isinstance(payload, str):
            return payload

        print(f"[WARN] Mail {mail_index} Part {part_index}: unexpected payload type {type(payload)}")
        return ""

    if isinstance(payload, bytes):
        try:
            return payload.decode(charset, errors="replace")
        except Exception as e:
            print(f"[DEBUG] decode failed, fallback utf-8: {e}")
            return payload.decode("utf-8", errors="replace")

    if isinstance(payload, str):
        return payload

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
    raise RuntimeError("POP3 Login endgültig fehlgeschlagen")

# ======================
# UIDLs abrufen
# ======================
resp, uidl_list, _ = pop_conn.uidl()
uidls = {int(e.decode().split()[0]): e.decode().split()[1] for e in uidl_list}
print(f"{len(uidls)} Mails im Quellpostfach gefunden.")

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
        # BODY + ATTACHMENTS
        # ======================
        if email_msg.is_multipart():
            print("[DEBUG] multipart detected")
            part_index = 0

            for part in email_msg.walk():
                part_index += 1

                if part.is_multipart():
                    print(f"[DEBUG] Part {part_index} is multipart container, skipping")
                    continue

                ctype = part.get_content_type()
                cdisp = part.get_content_disposition()
                filename = part.get_filename()

                print(f"[DEBUG] Part {part_index}: content_type={ctype}, content_disposition={cdisp}")

                # Attachment
                if cdisp == "attachment" and filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        forward.add_attachment(
                            payload,
                            maintype=part.get_content_maintype(),
                            subtype=part.get_content_subtype(),
                            filename=decode_and_safe(filename)
                        )
                        print(f"[DEBUG] Attachment hinzugefügt: {filename}, {len(payload)} Bytes")
                    continue

                # Text
                if ctype in ("text/plain", "text/html"):
                    text = get_text_from_part(part, i, part_index)
                    if not text.strip():
                        continue

                    print(f"[DEBUG] usable part: {ctype}, length={len(text)}")

                    if ctype == "text/plain" and not forward.get_content():
                        forward.set_content(text)
                    elif ctype == "text/html":
                        if not forward.get_content():
                            forward.set_content("HTML-Mail (Text nicht verfügbar)")
                        forward.add_alternative(text, subtype="html")

        else:
            print("[DEBUG] singlepart detected")
            ctype = email_msg.get_content_type()
            text = get_text_from_part(email_msg, i, 1)

            print(f"[DEBUG] singlepart content type: {ctype}, length={len(text)}")

            if ctype == "text/plain":
                forward.set_content(text)
            elif ctype == "text/html":
                forward.set_content("HTML-Mail (Text nicht verfügbar)")
                forward.add_alternative(text, subtype="html")
            else:
                forward.set_content(text)

        smtp.send_message(forward)
        pop_conn.dele(i)
        print(f"[OK] Mail {i} weitergeleitet & gelöscht")

    except Exception as e:
        print(f"[FEHLER] Mail {i}: {e}")
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
