import os
import sys
import poplib
import socket
import ssl
import time


# ======================
# CONFIG
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']

SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
TARGET_EMAIL = os.environ['TARGET_EMAIL']

POP3_TIMEOUT = 30


# ======================
# RAW SMTP SENDER (NO LIMITS)
# ======================
def smtp_raw_send(host, port, user, rcpt, raw_message: bytes):

    sock = socket.create_connection((host, port))
    sock.settimeout(30)

    def recv():
        try:
            return sock.recv(65536).decode(errors="ignore")
        except:
            return ""

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
# POP3 HELPERS
# ======================
def connect_pop3():
    pop = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
    pop.user(POP3_USER)
    pop.pass_(POP3_PASS)
    return pop


def safe_quit(pop):
    try:
        pop.quit()
    except:
        try:
            pop.close()
        except:
            pass


def safe_retr(pop, i):
    try:
        return pop.retr(i)
    except Exception as e:
        print(f"[POP3 ERROR] Mail {i}: {e}")
        return None


# ======================
# MAIN
# ======================
pop = connect_pop3()

resp, mails, _ = pop.list()

print(f"{len(mails)} Mails gefunden.")


for i in range(1, len(mails) + 1):

    print(f"\n[MAIL {i}]")

    try:
        retr = safe_retr(pop, i)

        if not retr:
            print("[RECONNECT] POP3 broken state")
            safe_quit(pop)
            pop = connect_pop3()
            continue

        resp, lines, _ = retr
        raw = b"\r\n".join(lines)

        # minimal safety only
        if not raw:
            print("[SKIP] empty mail")
            continue

        # send RAW unchanged
        smtp_raw_send(
            SMTP_HOST,
            SMTP_PORT,
            SMTP_USER,
            TARGET_EMAIL,
            raw
        )

        pop.dele(i)
        print(f"[OK] Mail {i} forwarded")

    except Exception as e:
        print(f"[ERROR] Mail {i}: {e}")

        try:
            pop.rset()
        except:
            pass


safe_quit(pop)

print("\nDONE")
