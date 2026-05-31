import os
import sys
import poplib
import smtplib
import time

from email.header import decode_header, make_header
from email.utils import parseaddr
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage

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
        return "(invalid subject)"


# ======================
# POP3 CONFIG
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']
POP3_TIMEOUT = 30
POP3_RETRIES = 3

# ======================
# SMTP CONFIG
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']
SMTP_FROM = os.environ['SMTP_FROM']
TARGET_EMAIL = os.environ['TARGET_EMAIL']

# ======================
# LOGIN POP3
# ======================
pop_conn = None
for attempt in range(POP3_RETRIES):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 login failed {attempt+1}: {e}")
        time.sleep(5)

if not pop_conn:
    print("POP3 login failed completely")
    sys.exit(1)


# ======================
# LIST MAILS
# ======================
resp, uidl_list, _ = pop_conn.uidl()
uidls = {int(x.decode().split()[0]): x.decode().split()[1] for x in uidl_list}

print(f"{len(uidls)} mails found.")


# ======================
# SMTP
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# PROCESS MAILS
# ======================
for i in sorted(uidls.keys()):
    print(f"\n[MAIL {i}]")

    try:
        # ---- POP3 RETR ----
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)

        # ---- PARSE SAFE ----
        msg = BytesParser(policy=policy.default).parsebytes(raw)

        subject = decode_and_safe(msg.get("Subject"))
        print(f"[SUBJECT] {subject}")

        # ======================
        # CLEAN MESSAGE BUILD
        # ======================
        clean_msg = EmailMessage()

        # keep only safe headers
        for h in ["From", "To", "Cc", "Bcc", "Date", "Subject", "Reply-To"]:
            if msg.get(h):
                clean_msg[h] = msg.get(h)

        # override From (optional safety)
        clean_msg["From"] = SMTP_FROM
        clean_msg["To"] = TARGET_EMAIL

        # IMPORTANT: preserve Reply-To (your requirement)
        if msg.get("Reply-To"):
            clean_msg["Reply-To"] = msg.get("Reply-To")

        # BODY + MIME preservation
        if msg.is_multipart():
            clean_msg.set_content(msg.get_body(preferencelist=('plain', 'html')).get_content())

            for part in msg.iter_parts():
                if part.get_content_disposition() == "attachment":
                    clean_msg.add_attachment(
                        part.get_payload(decode=True),
                        maintype=part.get_content_maintype(),
                        subtype=part.get_content_subtype(),
                        filename=part.get_filename()
                    )
        else:
            clean_msg.set_content(msg.get_content())

        # ======================
        # SEND SAFE
        # ======================
        smtp.send_message(clean_msg)

        pop_conn.dele(i)
        print(f"[OK] forwarded + deleted")

    except Exception as e:
        print(f"[ERROR] {i}: {e}")
        try:
            pop_conn.rset()
        except:
            pass


# ======================
# CLEANUP
# ======================
smtp.quit()
pop_conn.quit()

print("\nDone.")
