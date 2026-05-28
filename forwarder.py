import os
import sys
import poplib
import time
import socket
import ssl
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
# HELPERS
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
    except:
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
TARGET_EMAIL = os.environ['TARGET_EMAIL']


# ======================
# RAW SMTP SENDER (NO LIMITS)
# ======================
def smtp_raw_send(host, port, user, rcpt, raw_message: bytes):
    sock = socket.create_connection((host, port))
    sock.settimeout(30)

    def recv():
        return sock.recv(65536).decode(errors="ignore")

    def send(cmd: str):
        sock.send((cmd + "\r\n").encode())

    # Banner
    recv()

    send("EHLO localhost")
    recv()

    send("STARTTLS")
    recv()

    context = ssl.create_default_context()
    sock = context.wrap_socket(sock, server_hostname=host)

    send("EHLO localhost")
    recv()

    send(f"MAIL FROM:<{user}>")
    recv()

    send(f"RCPT TO:<{rcpt}>")
    recv()

    send("DATA")
    recv()

    sock.sendall(raw_message)
    sock.sendall(b"\r\n.\r\n")

    recv()

    send("QUIT")
    sock.close()


# ======================
# POP3 LOGIN
# ======================
pop_conn = None

for attempt in range(3):
    try:
        pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
        pop_conn.user(POP3_USER)
        pop_conn.pass_(POP3_PASS)
        break
    except Exception as e:
        print(f"[WARN] POP3 Login fehlgeschlagen ({attempt+1}): {e}")
        time.sleep(5)

if not pop_conn:
    sys.exit(0)


# ======================
# UIDL
# ======================
resp, uidl_list, _ = pop_conn.uidl()

uidls = {
    int(e.decode().split()[0]): e.decode().split()[1]
    for e in uidl_list
}

print(f"{len(uidls)} Mails gefunden.")


# ======================
# MAIL LOOP
# ======================
for i in sorted(uidls.keys()):

    print(f"\n[DEBUG] Mail {i}")

    subject = "(unknown subject)"

    try:
        # ======================
        # HEADER ONLY
        # ======================
        resp, lines, _ = pop_conn.top(i, 0)
        raw_header = b"\r\n".join(lines)

        header_msg = BytesParser(policy=policy.default).parsebytes(raw_header)

        subject = decode_and_safe(header_msg.get("Subject"))
        subject_lower = subject.lower()

        if any(w in subject_lower for w in FILTER_WORDS):
            print(f"[FILTERED] Mail {i} gelöscht: {subject}")
            pop_conn.dele(i)
            continue


        # ======================
        # FULL MAIL
        # ======================
        resp, lines, _ = pop_conn.retr(i)
        raw = b"\r\n".join(lines)


        # ======================
        # OPTIONAL: CLEAN DUPLICATE REPLY-TO HEADER (safe)
        # ======================
        try:
            raw_str = raw.decode("utf-8", errors="ignore")

            raw_str = re.sub(
                r"(?im)^Reply-To:.*\r?\n",
                "",
                raw_str
            )

            raw = raw_str.encode("utf-8", errors="ignore")

        except Exception as e:
            print(f"[WARN] Reply-To cleanup failed: {e}")


        # ======================
        # RAW SEND VIA SOCKET (NO LIMITS)
        # ======================
        smtp_raw_send(
            SMTP_HOST,
            SMTP_PORT,
            SMTP_USER,
            TARGET_EMAIL,
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
    pop_conn.quit()
except:
    pass

print("\nFertig.")
