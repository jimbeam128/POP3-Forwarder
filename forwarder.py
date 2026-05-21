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
        # ======================
        # 1. HEADER ONLY FETCH (KEIN RETR!)
        # ======================
        resp, lines, _ = pop_conn.top(i, 0)
        raw_header = b"\r\n".join(lines)

        email_msg = BytesParser(policy=policy.default).parsebytes(raw_header)

        subject = decode_and_safe(email_msg.get('Subject'))
        subject_lower = subject.lower()

        # ======================
        # FILTER BEFORE RETR
        # ======================
        if any(word in subject_lower for word in FILTER_WORDS):
            print(f"[FILTERED] Mail {i} gelöscht (Header match)")

            pop_conn.dele(i)
            continue

        # ======================
        # 2. NOW SAFE RETR
        # ======================
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)

        email_msg = BytesParser(policy=policy.default).parsebytes(raw)

        # ======================
        # SEND
        # ======================
        smtp.send_message(
            email_msg,
            from_addr=SMTP_FROM,
            to_addrs=TARGET_EMAIL
        )

        pop_conn.dele(i)
        print(f"[OK] Mail {i} forwarded & deleted")

    except Exception as e:
        print(f"[FEHLER] Mail {i} | Subject: {subject} | Error: {e}")

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
