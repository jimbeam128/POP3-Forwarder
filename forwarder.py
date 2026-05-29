import os
import sys
import socket
import ssl
import smtplib

from email.parser import BytesParser
from email import policy
from email.utils import parseaddr

# ======================
# CONFIG
# ======================

POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']
POP3_PORT = 995

SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']

SMTP_FROM = os.environ['SMTP_FROM']
TARGET_EMAIL = os.environ['TARGET_EMAIL']

# ======================
# POP3 LOW LEVEL
# ======================

def recv_line(sock):
    data = b""
    while not data.endswith(b"\r\n"):
        data += sock.recv(1)
    return data


def recv_multiline(sock):
    lines = []
    while True:
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                break
            line += chunk

        if line == b".\r\n":
            break

        if line.startswith(b".."):
            line = line[1:]

        lines.append(line)

    return b"".join(lines)


def send_cmd(sock, cmd):
    sock.sendall((cmd + "\r\n").encode())
    return recv_line(sock)


# ======================
# CONNECT POP3
# ======================

print("[DEBUG] connecting POP3...")

raw_sock = socket.create_connection((POP3_HOST, POP3_PORT), timeout=60)
sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=POP3_HOST)

print("[SERVER]", recv_line(sock))

print(send_cmd(sock, f"USER {POP3_USER}"))
print(send_cmd(sock, f"PASS {POP3_PASS}"))

stat = send_cmd(sock, "STAT").decode(errors="ignore")
mail_count = int(stat.split()[1])

print(f"{mail_count} mails found")

# ======================
# SMTP
# ======================

smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# HEADERS TO REMOVE
# ======================

STRIP_HEADERS = {
    "return-path",
    "delivered-to",
    "received",
    "dkim-signature",
    "authentication-results",
    "arc-seal",
    "arc-message-signature",
    "arc-authentication-results"
}

# ======================
# PROCESS MAILS
# ======================

for i in range(1, mail_count + 1):

    print(f"\n[MAIL {i}]")

    try:
        send_cmd(sock, f"RETR {i}")

        raw = recv_multiline(sock)

        print(f"[DEBUG] raw size: {len(raw)} bytes")

        # ======================
        # PARSE MIME
        # ======================

        msg = BytesParser(policy=policy.default).parsebytes(raw)

        subject = msg.get("Subject", "(no subject)")
        print("[SUBJECT]", subject)

        # ======================
        # REMOVE TRANSPORT HEADERS
        # ======================

        for h in list(msg.keys()):
            if h.lower() in STRIP_HEADERS:
                del msg[h]

        # ======================
        # SET CLEAN REPLY-TO (optional)
        # ======================

        original_reply_to = msg.get("Reply-To")

        if original_reply_to:
            msg.replace_header("Reply-To", original_reply_to)
        else:
            from_name, from_addr = parseaddr(msg.get("From", ""))
            if from_addr:
                msg["Reply-To"] = from_addr

        # ======================
        # FORWARD CLEAN MESSAGE
        # ======================

        smtp.send_message(
            msg,
            from_addr=SMTP_FROM,
            to_addrs=TARGET_EMAIL
        )

        print("[OK] forwarded")

        send_cmd(sock, f"DELE {i}")

    except Exception as e:
        print(f"[ERROR MAIL {i}]", e)

# ======================
# CLEANUP
# ======================

try:
    send_cmd(sock, "QUIT")
except Exception:
    pass

smtp.quit()
sock.close()

print("\nDONE")
