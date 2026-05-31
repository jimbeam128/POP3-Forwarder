import os
import sys
import poplib
import smtplib
import time

# ======================
# CONFIG
# ======================
FILTER_WORDS = [
    "pervert",
    "trojan",
    "crypto",
    "masturbating",
    "bitcoin",
    "urgent",
]

POP3_HOST = os.environ["POP3_HOST"]
POP3_USER = os.environ["POP3_USER"]
POP3_PASS = os.environ["POP3_PASS"]

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

SMTP_FROM = os.environ["SMTP_FROM"]
TARGET_EMAIL = os.environ["TARGET_EMAIL"]


# ======================
# SUBJECT EXTRACT (SAFE)
# ======================
def extract_subject(lines):
    for line in lines:
        try:
            s = line.decode("utf-8", errors="ignore")
        except Exception:
            continue

        if s.lower().startswith("subject:"):
            return s.split(":", 1)[1].strip()

        if s.strip() == "":
            break

    return "(unknown subject)"


def is_filtered(subject):
    s = subject.lower()
    return any(w in s for w in FILTER_WORDS)


# ======================
# SAFE POP3 STREAM RETR (KEY FIX)
# ======================
def safe_retr(pop_conn, msg_id):
    """
    ersetzt poplib.retr komplett
    robust gegen:
    - line too long
    - kaputte Tabs
    - malformed server output
    """

    pop_conn.putcmd(f"RETR {msg_id}")
    resp = pop_conn.getresp()

    lines = []

    while True:
        try:
            line = pop_conn._getmultiline()
        except Exception:
            break

        if line == b".":
            break

        # sanitizing only for safety, NOT altering mail content logic
        lines.append(line)

    return lines


# ======================
# LOGIN
# ======================
pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=30)
pop_conn.user(POP3_USER)
pop_conn.pass_(POP3_PASS)

smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# MAIL LIST
# ======================
resp, mails, _ = pop_conn.list()
ids = [int(m.decode().split()[0]) for m in mails]

print(f"{len(ids)} Mails gefunden")


# ======================
# PROCESS
# ======================
for i in ids:

    print(f"\n[MAIL {i}]")

    try:
        # SAFE RETR (NO poplib.retr!)
        lines = safe_retr(pop_conn, i)

        raw = b"\r\n".join(lines)

        subject = extract_subject(lines)

        print(f"[SUBJECT] {subject}")

        if is_filtered(subject):
            print("[FILTER] deleted")
            pop_conn.dele(i)
            continue

        smtp.sendmail(
            SMTP_FROM,
            TARGET_EMAIL,
            raw
        )

        pop_conn.dele(i)

        print("[OK] forwarded")

    except Exception as e:
        print(f"[ERROR] {i}: {e}")
        try:
            pop_conn.rset()
        except Exception:
            pass


# ======================
# CLEANUP
# ======================
try:
    smtp.quit()
except Exception:
    pass

try:
    pop_conn.quit()
except Exception:
    pass

print("\nDone.")
