import os
import sys
import poplib
import smtplib
import time
import re

from email.header import decode_header, make_header
from email.parser import BytesParser
from email import policy


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
# POP3 KONFIG
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']

POP3_TIMEOUT = 30
POP3_RETRIES = 3


# ======================
# SMTP KONFIG
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))

SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']

TARGET_EMAIL = os.environ['TARGET_EMAIL']


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
    print("[WARN] POP3 Login endgültig fehlgeschlagen")
    sys.exit(0)


# ======================
# UIDLs
# ======================
resp, uidl_list, _ = pop_conn.uidl()

uidls = {
    int(e.decode().split()[0]): e.decode().split()[1]
    for e in uidl_list
}

print(f"{len(uidls)} Mails gefunden.")


if not uidls:
    pop_conn.quit()
    sys.exit(0)


# ======================
# SMTP LOGIN
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)


# ======================
# MAIL LOOP
# ======================
for i in sorted(uidls.keys()):

    print(f"\n[DEBUG] Mail {i}")

    subject = "(unknown subject)"

    try:

        # ======================
        # HEADER ONLY (FILTER)
        # ======================
        resp, lines, _ = pop_conn.top(i, 0)
        raw_header = b"\r\n".join(lines)

        header_msg = BytesParser(
            policy=policy.default
        ).parsebytes(raw_header)

        subject = decode_and_safe(header_msg.get('Subject'))
        subject_lower = subject.lower()

        if any(word in subject_lower for word in FILTER_WORDS):
            print(f"[FILTERED] Mail {i} gelöscht: {subject}")
            pop_conn.dele(i)
            continue


        # ======================
        # FULL MAIL FETCH
        # ======================
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)


        # ======================
        # ORIGINAL HEADER ANALYSE (nur für Reply-To)
        # ======================
        email_msg = BytesParser(
            policy=policy.default
        ).parsebytes(raw)

        from_addr = email_msg.get("From", "")
        reply_to = email_msg.get("Reply-To", "")
        return_path = email_msg.get("Return-Path", "")

        original_reply = reply_to or from_addr or return_path


        # ======================
        # RAW STRING MANIPULATION (Reply-To INJECTION)
        # ======================
        try:
            raw_str = raw.decode("utf-8", errors="ignore")

            # bestehenden Reply-To entfernen (falls vorhanden)
            raw_str = re.sub(
                r"(?im)^Reply-To:.*\r?\n",
                "",
                raw_str
            )

            # Header-Injection
            raw_str = raw_str.replace(
                "\r\n\r\n",
                f"\r\nReply-To: {original_reply}\r\n\r\n",
                1
            )

            raw = raw_str.encode("utf-8", errors="ignore")

        except Exception as e:
            print(f"[WARN] Reply-To Injection failed: {e}")


        # ======================
        # RAW FORWARD (UNCHANGED STRUCTURE)
        # ======================
        smtp.sendmail(
            SMTP_USER,
            [TARGET_EMAIL],
            raw
        )


        # ======================
        # DELETE AFTER SUCCESS
        # ======================
        pop_conn.dele(i)

        print(f"[OK] Mail {i} forwarded")

    except Exception as e:

        print(f"[FEHLER] Mail {i} | {subject} | {e}")

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
    try:
        pop_conn.close()
    except:
        pass

print("\nFertig.")
