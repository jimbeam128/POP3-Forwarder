import os
import poplib
import smtplib
import time
from email import message_from_bytes
from email.message import EmailMessage
from email.header import decode_header, make_header

# ======================
# Helper
# ======================
def header_safe(value):
    if not value:
        return ""
    # zerlegt ALLE Whitespace-Arten (inkl. RFC folding & Unicode)
    return " ".join(str(value).split())

def decode_and_safe(header_value):
    if not header_value:
        return "(no subject)"
    try:
        return header_safe(str(make_header(decode_header(header_value))))
    except Exception:
        return "(invalid header)"

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

# ======================
# Zieladresse
# ======================
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
# Verbindung zu POP3 (mit Retry)
# ======================
pop_conn = None
for attempt in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 Verbindung fehlgeschlagen (Versuch {attempt + 1}): {e}")
        time.sleep(5)

if not pop_conn:
    raise RuntimeError("POP3 Verbindung nach mehreren Versuchen fehlgeschlagen")

# ======================
# UIDLs abrufen
# ======================
resp, uidl_list, _ = pop_conn.uidl()
uidls = {}
for entry in uidl_list:
    num, uid = entry.decode().split()
    uidls[int(num)] = uid

num_messages = len(uidls)
print(f"{num_messages} Mails im Quellpostfach gefunden.")

# ======================
# SMTP Verbindung
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# Weiterleitung
# ======================
for i in sorted(uidls.keys()):
    uid = uidls[i]

    if uid in processed_uidls:
        print(f"[SKIP] Mail {i} (UIDL bereits verarbeitet)")
        continue

    try:
        resp, lines, octets = pop_conn.retr(i)
        msg_content = b"\r\n".join(lines)
        from email import policy
        from email.parser import BytesParser
        email_msg = BytesParser(policy=policy.default).parsebytes(msg_content)

        # Original-Absender und Betreff dekodieren + header-safe
        from email.utils import parseaddr
        name, addr = parseaddr(str(email_msg['From']))
        sender_name = header_safe(name) or "Unknown Sender"
        original_from = addr or "unknown@example.com"
        original_subject = decode_and_safe(email_msg['Subject'])

        forward = EmailMessage()
        forward['Subject'] = original_subject
        forward['From'] = f"{sender_name} <{header_safe(SMTP_FROM)}>"
        forward['To'] = TARGET_EMAIL
        forward['Reply-To'] = header_safe(original_from)
        forward['X-Original-From'] = header_safe(original_from)
        forward['X-Forwarded-By'] = SMTP_FROM_NAME

        if email_msg.is_multipart():
            for part in email_msg.walk():
                ctype = part.get_content_type()
                cdisp = str(part.get('Content-Disposition'))
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'

                if payload is None:
                    continue

                if ctype == 'text/plain' and 'attachment' not in cdisp:
                    forward.set_content(payload.decode(charset, errors='replace'))
                elif ctype == 'text/html' and 'attachment' not in cdisp:
                    forward.add_alternative(payload.decode(charset, errors='replace'), subtype='html')
                elif 'attachment' in cdisp:
                    filename = part.get_filename()
                    if filename:
                        filename = decode_and_safe(filename)
                        forward.add_attachment(
                            payload,
                            maintype=part.get_content_maintype(),
                            subtype=part.get_content_subtype(),
                            filename=filename
                        )
        else:
            payload = email_msg.get_payload(decode=True)
            charset = email_msg.get_content_charset() or 'utf-8'
            if payload:
                forward.set_content(payload.decode(charset, errors='replace'))

        smtp.send_message(forward)
        pop_conn.dele(i)

        processed_uidls.add(uid)
        with open(UIDL_FILE, "a") as f:
            f.write(uid + "\n")

        print(f"[OK] Mail {i} weitergeleitet.")

    except Exception as e:
        print(f"[FEHLER] Mail {i}: {e}")
        try:
            pop_conn.rset()
        except:
            pass

# ======================
# Cleanup
# ======================
pop_conn.quit()
smtp.quit()
print("Alle Mails verarbeitet.")
