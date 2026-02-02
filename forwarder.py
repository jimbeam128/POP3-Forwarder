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
# Helper
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
        pop_conn = None
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
# SMTP
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# Verarbeitung
# ======================
for i in sorted(uidls.keys()):
    print(f"\n[DEBUG] === Mail {i} ===")

    try:
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)
        email_msg = BytesParser(policy=policy.default).parsebytes(raw)

        print(f"[DEBUG] Content-Type: {email_msg.get_content_type()}")

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
        # BODY DEBUG + SAFE HANDLING
        # ======================
        if email_msg.is_multipart():
            print("[DEBUG] multipart detected")

            for part in email_msg.walk():
                # 🔥 WICHTIG: Multipart-Container überspringen
                if part.is_multipart():
                    continue

                ctype = part.get_content_type()
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or part.get_charset() or "utf-8"

                print(f"[DEBUG] usable part: {ctype}, payload={type(payload)}, charset={charset}")

                # 🔥 Zusätzlicher Schutz gegen None-Payload
                if payload is None:
                    payload = b""

                # Falls der Payload noch ein str ist (nicht bytes), in bytes konvertieren
                if isinstance(payload, str):
                    payload = payload.encode(charset, errors="replace")

                charset = str(charset)

                if ctype == "text/plain":
                    forward.set_content(payload.decode(charset, errors="replace"))
                elif ctype == "text/html":
                    if not forward.get_content():
                        forward.set_content("HTML-Mail (Text nicht verfügbar)")
                    forward.add_alternative(
                        payload.decode(charset, errors="replace"),
                        subtype="html"
                    )


                if isinstance(payload, str):
                    payload = payload.encode(str(charset), errors="replace")

                charset = str(charset)

                if ctype == "text/plain":
                    forward.set_content(payload.decode(charset, errors="replace"))
                elif ctype == "text/html":
                    if not forward.get_content():
                        forward.set_content("HTML-Mail")
                    forward.add_alternative(
                        payload.decode(charset, errors="replace"),
                        subtype="html"
                    )

        else:
            print("[DEBUG] singlepart detected")

            payload = email_msg.get_payload(decode=True)
            charset = email_msg.get_content_charset() or email_msg.get_charset() or "utf-8"

            print(f"[DEBUG] payload={type(payload)}, charset={charset}")

            if payload is None:
                payload = b""

            if isinstance(payload, str):
                payload = payload.encode(str(charset), errors="replace")

            charset = str(charset)

            if email_msg.get_content_type() == "text/html":
                forward.set_content("HTML-Mail")
                forward.add_alternative(
                    payload.decode(charset, errors="replace"),
                    subtype="html"
                )
            else:
                forward.set_content(payload.decode(charset, errors="replace"))

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
