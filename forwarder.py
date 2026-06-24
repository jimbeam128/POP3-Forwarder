import os
import sys
import poplib
import smtplib
import time

from email.parser import HeaderParser
from email.header import decode_header, make_header

# ======================
# FILTER KONFIG
# ======================
FILTER_WORDS = [
    "pervert",
    "trojan",
    "crypto",
    "masturbating",
    "recorded",
    "Porno-Webseiten",
    "bitcoin",
    "urgent",
    "Verdachts auf schädliche Aktivitäten"
]

# ======================
# Helper
# ======================
def decode_subject(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def contains_filter(subject: str) -> bool:
    s = subject.lower()
    return any(w.lower() in s for w in FILTER_WORDS)


def inject_reply_to(headers: bytes, reply_to: str) -> bytes:
    """
    Entfernt Reply-To und setzt neues sauber.
    """
    lines = headers.split(b"\r\n")
    new_lines = []

    for line in lines:
        if line.lower().startswith(b"reply-to:"):
            continue
        new_lines.append(line)

    if reply_to:
        new_lines.append(f"Reply-To: {reply_to}".encode())

    return b"\r\n".join(new_lines)


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
# LOGIN POP3
# ======================
pop_conn = None

for i in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 Login fehlgeschlagen {i+1}: {e}")
        time.sleep(5)

if not pop_conn:
    print("POP3 Login failed")
    sys.exit(1)


# ======================
# LIST MAILS
# ======================
resp, mails, _ = pop_conn.list()
ids = [int(m.decode().split()[0]) for m in mails]

print(f"{len(ids)} Mails gefunden.")


# ======================
# SMTP LOGIN
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# PROCESS MAILS
# ======================
for msg_id in ids:

    print(f"\n[MAIL {msg_id}]")

    try:
        resp, lines, _ = pop_conn.retr(msg_id)
        raw = b"\r\n".join(lines)

        split_pos = raw.find(b"\r\n\r\n")
        if split_pos == -1:
            print("[ERROR] Invalid mail format")
            continue

        raw_headers = raw[:split_pos]
        raw_body = raw[split_pos + 4:]

        headers = HeaderParser().parsestr(
            raw_headers.decode(errors="ignore")
        )

        subject = decode_subject(headers.get("Subject", ""))
        print(f"[SUBJECT] {subject}")

        # ======================
        # FILTER
        # ======================
        if contains_filter(subject):
            print("[FILTER] Mail gelöscht")
            pop_conn.dele(msg_id)
            continue

        # ======================
        # Kleinanzeigen erkennen
        # ======================
        from_header = headers.get("From", "").lower()
        is_kleinanzeigen = "kleinanzeigen.de" in from_header

        # ======================
        # Reply-To bestimmen
        # ======================
        reply_to = headers.get("Reply-To")

        if not reply_to:
            reply_to = headers.get("From", "")

        # ======================
        # HEADER PATCH
        # ======================
        new_headers = inject_reply_to(raw_headers, reply_to)

        # ======================
        # DMARC FIX (nur Kleinanzeigen)
        # ======================
        if is_kleinanzeigen:
            print("[DEBUG] Kleinanzeigen -> DMARC-safe rewrite aktiv")

            # WICHTIG: From überschreiben
            # damit Gmail NICHT glaubt, wir senden im Namen von kleinanzeigen.de

            lines = new_headers.split(b"\r\n")
            cleaned = []

            for line in lines:
                if line.lower().startswith(b"from:"):
                    continue
                cleaned.append(line)

            cleaned.append(f"From: {SMTP_FROM}".encode())

            new_headers = b"\r\n".join(cleaned)

        # ======================
        # FINAL MAIL
        # ======================
        final_mail = new_headers + b"\r\n\r\n" + raw_body

        # ======================
        # SEND
        # ======================
        smtp.sendmail(
            SMTP_FROM,
            TARGET_EMAIL,
            final_mail
        )

        pop_conn.dele(msg_id)
        print("[OK] forwarded")

    except Exception as e:
        print(f"[ERROR] {msg_id}: {e}")
        try:
            pop_conn.rset()
        except:
            pass


# ======================
# CLEANUP
# ======================
try:
    smtp.quit()
except:
    pass

try:
    pop_conn.quit()
except:
    pass

print("\nDone.")
