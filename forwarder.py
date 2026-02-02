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
    return " ".join(str(value).split())

def decode_and_safe(header_value):
    if not header_value:
        return "(no subject)"
    try:
        return header_safe(str(make_header(decode_header(header_value))))
    except Exception:
        return "(invalid header)"

# ======================
# Failure-Tracking
# ======================
FAILURE_FILE = "consecutive_failures.txt"
MAX_FAILURES = 3

def read_failures():
    if not os.path.exists(FAILURE_FILE):
        return 0
    with open(FAILURE_FILE, "r") as f:
        return int(f.read().strip() or 0)

def write_failures(n):
    with open(FAILURE_FILE, "w") as f:
        f.write(str(n))

had_fatal_error = False

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
pop_logged_in = False

for attempt in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        pop_logged_in = True
        break
    except Exception as e:
        print(f"[WARN] POP3 Verbindung fehlgeschlagen (Versuch {attempt + 1}): {e}")
        pop_conn = None
        time.sleep(5)

# ======================
# Fehlerbehandlung / Counter
# ======================
failures = read_failures()
if not pop_logged_in:
    had_fatal_error = True
    failures += 1
    write_failures(failures)
    print(f"[FATAL] POP3 Verbindung konnte nicht hergestellt werden. Consecutive failures: {failures}")
    if failures >= MAX_FAILURES:
        raise RuntimeError("Maximale Anzahl aufeinanderfolgender Fehler erreicht")
        exit(0)
else:
    write_failures(0)

# ======================
# Verarbeitung
# ======================
if not had_fatal_error and pop_logged_in:
    resp, uidl_list, _ = pop_conn.uidl()
    uidls = {
        int(parts[0]): parts[1]
        for e in uidl_list
        if (parts := e.decode(errors="replace").split()) and len(parts) == 2
    }
    print(f"{len(uidls)} Mails im Quellpostfach gefunden.")

    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    smtp.starttls()
    smtp.login(SMTP_USER, SMTP_PASS)

    for i in sorted(uidls.keys()):
        uid = uidls[i]
        if uid in processed_uidls:
            print(f"[SKIP] Mail {i} (UIDL bereits verarbeitet)")
            continue

        try:
            _, lines, _ = pop_conn.retr(i)
            msg_content = b"\r\n".join(lines)

            from email import policy
            from email.parser import BytesParser
            email_msg = BytesParser(policy=policy.default).parsebytes(msg_content)

            from email.utils import parseaddr
            from_name, from_addr = parseaddr(str(email_msg.get('From', '')))
            reply_name, reply_addr = parseaddr(str(email_msg.get('Reply-To', '')))
            _, return_addr = parseaddr(email_msg.get('Return-Path', ''))

            original_from = reply_addr or from_addr or return_addr or "unknown@example.com"

            if from_name:
                sender_name = header_safe(from_name)
            elif reply_name:
                sender_name = header_safe(reply_name)
            elif "@" in original_from:
                sender_name = original_from.split("@")[0]
            else:
                sender_name = "Mail Sender"

            forward = EmailMessage()
            forward['Subject'] = decode_and_safe(email_msg['Subject'])
            forward['From'] = f"\"{sender_name.replace('"','').strip()}\" <{SMTP_FROM}>"
            forward['To'] = TARGET_EMAIL
            forward['Reply-To'] = original_from
            forward['X-Original-From'] = original_from
            forward['X-Forwarded-By'] = SMTP_FROM_NAME

            if email_msg.is_multipart():
                for part in email_msg.walk():
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue

                    ctype = part.get_content_type()
                    cdisp = str(part.get('Content-Disposition'))
                    charset = part.get_content_charset() or part.get_charset() or 'utf-8'

                    if ctype == "text/plain" and "attachment" not in cdisp:
                        forward.set_content(payload.decode(charset, errors="replace"))
                    elif ctype == "text/html" and "attachment" not in cdisp:
                        if not forward.get_content():
                            forward.set_content("HTML-Mail (Text nicht verfügbar)")
                        forward.add_alternative(
                            payload.decode(charset, errors="replace"),
                            subtype="html"
                        )
                    elif "attachment" in cdisp:
                        filename = part.get_filename()
                        if filename:
                            forward.add_attachment(
                                payload,
                                maintype=part.get_content_maintype(),
                                subtype=part.get_content_subtype(),
                                filename=decode_and_safe(filename)
                            )
            else:
                payload = email_msg.get_payload(decode=True)
                if payload is not None:
                    charset = email_msg.get_content_charset() or email_msg.get_charset() or 'utf-8'
                    if email_msg.get_content_type() == "text/html":
                        forward.set_content("HTML-Mail (Text nicht verfügbar)")
                        forward.add_alternative(
                            payload.decode(charset, errors="replace"),
                            subtype="html"
                        )
                    else:
                        forward.set_content(payload.decode(charset, errors="replace"))

            smtp.send_message(forward, from_addr=SMTP_FROM, to_addrs=[TARGET_EMAIL])
            pop_conn.dele(i)

            processed_uidls.add(uid)
            with open(UIDL_FILE, "a") as f:
                f.write(uid + "\n")

            print(f"[OK] Mail {i} weitergeleitet.")

        except Exception as e:
            print(f"[FEHLER] Mail {i}: {e}")
            try:
                pop_conn.rset()
            except Exception:
                pass

    pop_conn.quit()
    smtp.quit()

print("Alle Mails verarbeitet.")
