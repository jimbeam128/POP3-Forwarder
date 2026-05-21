import os
import sys
import poplib
import smtplib
import time
from email.header import decode_header, make_header
from email.utils import parseaddr
from email import policy
from email.parser import BytesParser

# ======================
# FILTER KONFIG
# ======================
FILTER_WORDS = [
    "pervert",
    "trojan",
    "crypto",
    "urgent",
    "masturbating",
    "recorded",
]

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
        # RAW Mail holen
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)

        # Header parsen
        email_msg = BytesParser(policy=policy.default).parsebytes(raw)

        subject = decode_and_safe(email_msg.get('Subject'))
        subject_lower = subject.lower()

        # ======================
        # FILTER CHECK
        # ======================
        if any(word in subject_lower for word in FILTER_WORDS):
            print(f"[FILTERED] Mail {i} gelöscht (Subject Match: {subject})")

            pop_conn.dele(i)
            continue

        from_name, from_addr = parseaddr(str(email_msg.get('From', '')))
        reply_name, reply_addr = parseaddr(str(email_msg.get('Reply-To', '')))
        return_path = email_msg.get('Return-Path', '')
        _, return_addr = parseaddr(return_path)

        original_from = (
            reply_addr
            or from_addr
            or return_addr
            or "unknown@example.com"
        )

        sender_name = (
            header_safe(from_name)
            or header_safe(reply_name)
            or original_from.split("@")[0]
        )

        # ======================
        # BULLETPROOF RAW FORWARD
        # ======================
        forward_raw = raw

        smtp.sendmail(
            SMTP_FROM,
            TARGET_EMAIL,
            forward_raw
        )

        # löschen nach Erfolg
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
