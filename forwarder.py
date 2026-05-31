import os
import sys
import poplib
import smtplib
import time

from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import parseaddr

# ======================
# Betreff-Filter
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
    except Exception:
        return "(invalid subject)"


# ======================
# POP3 Konfiguration
# ======================
POP3_HOST = os.environ["POP3_HOST"]
POP3_USER = os.environ["POP3_USER"]
POP3_PASS = os.environ["POP3_PASS"]

POP3_TIMEOUT = 30
POP3_RETRIES = 3

# ======================
# SMTP Konfiguration
# ======================
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

SMTP_FROM = os.environ["SMTP_FROM"]
TARGET_EMAIL = os.environ["TARGET_EMAIL"]


# ======================
# POP3 Login
# ======================
pop_conn = None

for attempt in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(
            POP3_HOST,
            timeout=POP3_TIMEOUT
        )
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

uidls = {
    int(e.decode().split()[0]): e.decode().split()[1]
    for e in uidl_list
}

print(f"{len(uidls)} Mails im Quellpostfach gefunden.")

if not uidls:
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
        # RAW MAIL HOLEN
        # ======================
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)

        # ======================
        # SAFE PARSING (FIX HIER!)
        # ======================
        try:
            original_msg = BytesParser(
                policy=policy.compat32   # <<< WICHTIGER FIX
            ).parsebytes(raw)

        except Exception as e:
            print(f"[WARN] Parserfehler → RAW fallback: {e}")

            smtp.sendmail(
                SMTP_FROM,
                TARGET_EMAIL,
                raw
            )

            pop_conn.dele(i)
            continue

        subject = decode_and_safe(
            original_msg.get("Subject", "")
        )

        subject_lower = subject.lower()

        # ======================
        # FILTER
        # ======================
        if any(word in subject_lower for word in FILTER_WORDS):
            print(f"[FILTER] Mail gelöscht | Subject: {subject}")
            pop_conn.dele(i)
            continue

        # ======================
        # FROM / REPLY-TO FIX
        # ======================
        reply_addr = ""
        from_addr = ""

        if original_msg.get("Reply-To"):
            _, reply_addr = parseaddr(original_msg["Reply-To"])

        if original_msg.get("From"):
            _, from_addr = parseaddr(original_msg["From"])

        original_sender = reply_addr or from_addr or SMTP_FROM

        # ======================
        # CLEAN MESSAGE BUILD
        # ======================
        clean_msg = EmailMessage()

        clean_msg["From"] = SMTP_FROM
        clean_msg["To"] = TARGET_EMAIL
        clean_msg["Reply-To"] = original_sender
        clean_msg["Subject"] = subject

        # ======================
        # MIME COPY (BODY + ATTACHMENTS)
        # ======================
        if original_msg.is_multipart():

            for part in original_msg.walk():

                if part.is_multipart():
                    continue

                payload = part.get_payload(decode=True)
                maintype = part.get_content_maintype()
                subtype = part.get_content_subtype()

                if part.get_content_disposition() == "attachment":

                    clean_msg.add_attachment(
                        payload,
                        maintype=maintype,
                        subtype=subtype,
                        filename=part.get_filename()
                    )

                else:
                    charset = part.get_content_charset() or "utf-8"

                    try:
                        text = payload.decode(charset, errors="replace")
                    except Exception:
                        text = payload.decode("utf-8", errors="replace")

                    if subtype == "html":
                        clean_msg.add_alternative(text, subtype="html")
                    else:
                        clean_msg.set_content(text)

        else:
            payload = original_msg.get_payload(decode=True)

            charset = original_msg.get_content_charset() or "utf-8"

            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")

            subtype = original_msg.get_content_subtype()

            if subtype == "html":
                clean_msg.add_alternative(text, subtype="html")
            else:
                clean_msg.set_content(text)

        # ======================
        # SEND
        # ======================
        smtp.send_message(clean_msg)

        pop_conn.dele(i)

        print(f"[OK] Mail {i} weitergeleitet & gelöscht")

    except Exception as e:

        print(f"[FEHLER] Mail {i} | Subject: {subject} | Error: {e}")

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
except Exception as e:
    print(f"[WARN] POP3 QUIT Fehler: {e}")

print("\nAlle Mails verarbeitet.")
