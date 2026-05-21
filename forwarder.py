import os
import sys
import time
import poplib
import smtplib

from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import parseaddr


# ======================
# CONFIG
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']

SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']

SMTP_FROM = os.environ['SMTP_FROM']
TARGET_EMAIL = os.environ['TARGET_EMAIL']

POP3_TIMEOUT = 30
POP3_RETRIES = 3

UIDL_FILE = "processed_uidls.txt"


# ======================
# UIDL handling
# ======================
processed_uidls = set()

if os.path.exists(UIDL_FILE):
    with open(UIDL_FILE, "r") as f:
        processed_uidls = set(line.strip() for line in f if line.strip())


def save_uidl(uidl: str):
    with open(UIDL_FILE, "a") as f:
        f.write(uidl + "\n")


# ======================
# safe header decode
# ======================
def decode_header_safe(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return "(decode error)"


# ======================
# POP3 CONNECT
# ======================
pop_conn = None

for i in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 login failed ({i+1}): {e}")
        time.sleep(5)

if not pop_conn:
    print("[FATAL] POP3 login failed")
    sys.exit(1)


# ======================
# UIDL FETCH
# ======================
resp, uidl_list, _ = pop_conn.uidl()
uidls = {}

for entry in uidl_list:
    parts = entry.decode().split()
    idx = int(parts[0])
    uidl = parts[1]
    uidls[idx] = uidl

print(f"{len(uidls)} messages found.")

if not uidls:
    pop_conn.quit()
    sys.exit(0)


# ======================
# SMTP CONNECT
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# MAIN LOOP
# ======================
for msg_id in sorted(uidls.keys()):
    uidl = uidls[msg_id]

    if uidl in processed_uidls:
        print(f"[SKIP] already processed {msg_id}")
        continue

    print(f"\n[INFO] Processing mail {msg_id}")

    try:
        # ----------------------
        # FETCH RAW MAIL
        # ----------------------
        resp, lines, _ = pop_conn.retr(msg_id)
        raw = b"\r\n".join(lines)

        # ----------------------
        # PARSE (RFC safe policy)
        # ----------------------
        msg = BytesParser(policy=policy.SMTP).parsebytes(raw)

        subject = decode_header_safe(msg.get("Subject"))
        from_name, from_addr = parseaddr(str(msg.get("From", "")))

        print(f"[MAIL] Subject: {subject} | From: {from_addr}")

        # ----------------------
        # SEND (IMPORTANT FIX)
        # ----------------------
        smtp.send_message(
            msg,
            from_addr=SMTP_FROM,
            to_addrs=TARGET_EMAIL
        )

        # ----------------------
        # MARK AS DONE
        # ----------------------
        pop_conn.dele(msg_id)
        save_uidl(uidl)
        processed_uidls.add(uidl)

        print(f"[OK] forwarded & deleted {msg_id}")

    except Exception as e:
        print(f"[ERROR] mail {msg_id}: {e}")

        try:
            pop_conn.rset()
        except Exception:
            pass


# ======================
# CLEANUP
# ======================
smtp.quit()
pop_conn.quit()

print("\nDone.")
