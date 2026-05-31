import os
import sys
import poplib
import smtplib
import time
import re

# ======================
# FILTER KONFIG
# ======================
FILTER_WORDS = [
    "pervert",
    "trojan",
    "crypto",
    "masturbating",
    "recorded",
    "bitcoin",
    "urgent",
]

# ======================
# POP3 CONFIG
# ======================
POP3_HOST = os.environ["POP3_HOST"]
POP3_USER = os.environ["POP3_USER"]
POP3_PASS = os.environ["POP3_PASS"]

POP3_TIMEOUT = 30
POP3_RETRIES = 3

# ======================
# SMTP CONFIG
# ======================
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

SMTP_FROM = os.environ["SMTP_FROM"]
TARGET_EMAIL = os.environ["TARGET_EMAIL"]

# ======================
# FAST SUBJECT EXTRACTION (RAW)
# ======================
def extract_subject(raw_lines):
    """
    liest nur Header-Teil bis zur ersten Leerzeile
    ohne EmailParser -> 100% safe
    """
    subject = "(unknown subject)"

    for line in raw_lines:
        if isinstance(line, bytes):
            line_str = line.decode("utf-8", errors="ignore")
        else:
            line_str = str(line)

        if line_str.lower().startswith("subject:"):
            subject = line_str.split(":", 1)[1].strip()
            break

        # Header-Ende
        if line_str.strip() == "":
            break

    return subject


def subject_match(subject):
    s = subject.lower()
    return any(word in s for word in FILTER_WORDS)


# ======================
# POP3 LOGIN
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
    print("[FATAL] POP3 Login failed")
    sys.exit(1)


# ======================
# LIST MAILS
# ======================
resp, mails, _ = pop_conn.list()

mail_ids = [int(m.decode().split()[0]) for m in mails]

print(f"{len(mail_ids)} Mails im Postfach")

if not mail_ids:
    pop_conn.quit()
    sys.exit(0)


# ======================
# SMTP LOGIN
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# PROCESS MAILS
# ======================
for i in mail_ids:

    print(f"\n[MAIL {i}]")

    try:
        # ======================
        # FULL RAW MAIL
        # ======================
        resp, lines, _ = pop_conn.retr(i)

        raw = b"\r\n".join(lines)

        # ======================
        # SUBJECT FILTER (RAW)
        # ======================
        subject = extract_subject(lines)

        print(f"[SUBJECT] {subject}")

        if subject_match(subject):
            print("[FILTER] deleted")
            pop_conn.dele(i)
            continue

        # ======================
        # RAW FORWARD (UNCHANGED)
        # ======================
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
# CLEANUP (SAFE)
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
