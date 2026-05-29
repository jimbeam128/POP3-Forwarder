import os
import sys
import ssl
import socket
import smtplib
import time

from email.header import decode_header, make_header
from email.parser import BytesParser
from email import policy

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
# Konfiguration
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

POP3_PORT = 995


# ======================
# Socket Helper
# ======================

def recv_until(sock, marker=b"\r\n"):
    data = b""

    while marker not in data:
        chunk = sock.recv(1)

        if not chunk:
            break

        data += chunk

    return data


def recv_multiline(sock):
    """
    Korrektes POP3 multiline reading.
    Zeilenweise bis zu einer einzelnen "." Zeile.
    """

    lines = []

    while True:

        line = b""

        while not line.endswith(b"\r\n"):
            chunk = sock.recv(1)

            if not chunk:
                raise Exception("socket closed")

            line += chunk

        # DEBUG
        print(f"[LINE] {len(line)} bytes")

        # POP3 terminator
        if line == b".\r\n":
            break

        # dot unstuffing
        if line.startswith(b".."):
            line = line[1:]

        lines.append(line)

    return b"".join(lines)


def send_cmd(sock, cmd):
    print(f"\n[CLIENT] {cmd}")

    sock.sendall((cmd + "\r\n").encode())

    resp = recv_until(sock)

    print(f"[SERVER] {resp[:500]!r}")

    return resp


# ======================
# POP3 Verbindung
# ======================

print("[DEBUG] connecting...")

raw_sock = socket.create_connection((POP3_HOST, POP3_PORT), timeout=60)

sock = ssl.create_default_context().wrap_socket(
    raw_sock,
    server_hostname=POP3_HOST
)

# Greeting
greeting = recv_until(sock)

print(f"[SERVER GREETING] {greeting!r}")

# ======================
# Login
# ======================

send_cmd(sock, f"USER {POP3_USER}")
send_cmd(sock, f"PASS {POP3_PASS}")

# ======================
# STAT
# ======================

stat_resp = send_cmd(sock, "STAT")

try:
    parts = stat_resp.decode(errors="ignore").split()

    mail_count = int(parts[1])

except Exception:
    print("[ERROR] could not parse STAT")
    sys.exit(1)

print(f"\n{mail_count} mails found.")

if mail_count == 0:
    print("No mails.")
    send_cmd(sock, "QUIT")
    sys.exit(0)

# ======================
# SMTP Login
# ======================

smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)

smtp.starttls()

smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# Mail Verarbeitung
# ======================

for i in range(1, mail_count + 1):

    print(f"\n==============================")
    print(f"[MAIL {i}]")
    print(f"==============================")

    try:

        # ======================
        # RETR
        # ======================

        sock.sendall(f"RETR {i}\r\n".encode())

        first_line = recv_until(sock)

        print(f"[RETR RESPONSE] {first_line!r}")

        if not first_line.startswith(b"+OK"):
            print("[ERROR] RETR failed")
            continue

        print("[DEBUG] reading raw multiline mail...")

        raw_data = recv_multiline(sock)

        print(f"[DEBUG] total raw size: {len(raw_data)} bytes")

        # ======================
        # DEBUG: Zeilen analysieren
        # ======================

        lines = raw_data.split(b"\r\n")

        print(f"[DEBUG] total lines: {len(lines)}")

        longest = 0

        for idx, line in enumerate(lines):

            line_len = len(line)

            if line_len > longest:
                longest = line_len

            if line_len > 1000:
                print(
                    f"[LONG LINE] line={idx} bytes={line_len}"
                )

                print(line[:300])

        print(f"[DEBUG] longest line: {longest} bytes")

        # ======================
        # POP3 Terminator entfernen
        # ======================

        if raw_data.endswith(b"\r\n.\r\n"):
            raw_data = raw_data[:-5]

        # ======================
        # Header Parsing
        # ======================

        try:
            email_msg = BytesParser(
                policy=policy.default
            ).parsebytes(raw_data)

            subject = decode_and_safe(
                email_msg.get("Subject")
            )

        except Exception as e:

            print(f"[HEADER PARSE ERROR] {e}")

            subject = "(parse failed)"

        print(f"[SUBJECT] {subject}")

        # ======================
        # SMTP FORWARD
        # ======================

        try:

            smtp.sendmail(
                SMTP_FROM,
                TARGET_EMAIL,
                raw_data
            )

            print("[OK] forwarded")

        except Exception as e:

            print(f"[SMTP ERROR] {e}")

            continue

        # ======================
        # DELETE
        # ======================

        del_resp = send_cmd(sock, f"DELE {i}")

        print(f"[DELETE] {del_resp!r}")

    except Exception as e:

        print(f"[FATAL MAIL ERROR] {e}")

# ======================
# Cleanup
# ======================

print("\n[DEBUG] cleanup")

try:
    send_cmd(sock, "QUIT")
except Exception as e:
    print(f"[WARN] QUIT failed: {e}")

try:
    smtp.quit()
except Exception as e:
    print(f"[WARN] SMTP quit failed: {e}")

try:
    sock.close()
except Exception:
    pass

print("\nDone.")
