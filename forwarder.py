import os
import sys
import poplib
import socket
import ssl
import time


# ======================
# POP3 CONFIG
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']


# ======================
# SMTP CONFIG
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
TARGET_EMAIL = os.environ['TARGET_EMAIL']


# ======================
# RAW SMTP (NO LIMITS)
# ======================
def smtp_raw_send(host, port, user, rcpt, raw_message: bytes):

    sock = socket.create_connection((host, port))
    sock.settimeout(30)

    def recv():
        return sock.recv(65536).decode(errors="ignore")

    def send(cmd):
        sock.send((cmd + "\r\n").encode())

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
pop = poplib.POP3_SSL(POP3_HOST, timeout=30)
pop.user(POP3_USER)
pop.pass_(POP3_PASS)


resp, mails, _ = pop.list()

print(f"{len(mails)} mails found")


# ======================
# MAIN LOOP
# ======================
for i in range(1, len(mails) + 1):

    print(f"\n[MAIL {i}]")

    try:
        # 🚨 ONLY RAW RETR (NO PARSING, NO TOP)
        resp, lines, _ = pop.retr(i)
        raw = b"\r\n".join(lines)

        # optional safety only
        if len(raw) == 0:
            print("[SKIP] empty mail")
            continue


        # 🚀 DIRECT PIPE
        smtp_raw_send(
            SMTP_HOST,
            SMTP_PORT,
            SMTP_USER,
            TARGET_EMAIL,
            raw
        )

        pop.dele(i)
        print(f"[OK] forwarded {i}")

    except Exception as e:
        print(f"[ERROR] {i}: {e}")
        try:
            pop.rset()
        except:
            pass


pop.quit()
print("\nDONE")
