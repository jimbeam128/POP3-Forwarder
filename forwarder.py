import os
import sys
import socket
import ssl
import smtplib
import time

# ======================
# CONFIG
# ======================
FILTER_WORDS = [
    "pervert",
    "trojan",
    "crypto",
    "masturbating",
    "bitcoin",
    "urgent",
]

POP3_HOST = os.environ["POP3_HOST"]
POP3_USER = os.environ["POP3_USER"]
POP3_PASS = os.environ["POP3_PASS"]

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

SMTP_FROM = os.environ["SMTP_FROM"]
TARGET_EMAIL = os.environ["TARGET_EMAIL"]


# ======================
# SIMPLE SOCKET POP3 CLIENT
# ======================
class RawPOP3:
    def __init__(self, host, port=995):
        ctx = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        self.conn = ctx.wrap_socket(sock, server_hostname=host)

        self._readline()  # greeting

    def send(self, cmd):
        self.conn.sendall((cmd + "\r\n").encode())

    def _readline(self):
        return self.conn.recv(4096)

    def auth(self, user, pw):
        self.send(f"USER {user}")
        self._readline()

        self.send(f"PASS {pw}")
        self._readline()

    def list(self):
        self.send("LIST")
        data = self._multiline()
        ids = []
        for line in data:
            try:
                ids.append(int(line.split()[0]))
            except:
                pass
        return ids

    def retr(self, i):
        self.send(f"RETR {i}")
        return self._multiline(raw=True)

    def dele(self, i):
        self.send(f"DELE {i}")
        self._readline()

    def quit(self):
        try:
            self.send("QUIT")
            self._readline()
        except:
            pass

        self.conn.close()

    def _multiline(self, raw=False):
        buf = b""
        lines = []

        while True:
            chunk = self.conn.recv(4096)
            if not chunk:
                break

            buf += chunk

            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)

                if line == b".":
                    return lines

                if raw:
                    lines.append(line + b"\r\n")
                else:
                    lines.append(line.decode("utf-8", errors="ignore"))

        return lines


# ======================
# SUBJECT EXTRACTION
# ======================
def extract_subject(lines):
    for l in lines:
        s = l.decode("utf-8", errors="ignore")
        if s.lower().startswith("subject:"):
            return s.split(":", 1)[1].strip()
        if s.strip() == "":
            break
    return "(unknown subject)"


def filtered(subject):
    s = subject.lower()
    return any(w in s for w in FILTER_WORDS)


# ======================
# CONNECT
# ======================
pop = RawPOP3(POP3_HOST)
pop.auth(POP3_USER, POP3_PASS)

smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# PROCESS
# ======================
ids = pop.list()

print(f"{len(ids)} Mails gefunden")

for i in ids:

    print(f"\n[MAIL {i}]")

    try:
        lines = pop.retr(i)

        subject = extract_subject(lines)

        print(f"[SUBJECT] {subject}")

        if filtered(subject):
            print("[FILTER] deleted")
            pop.dele(i)
            continue

        raw = b"".join(lines)

        smtp.sendmail(
            SMTP_FROM,
            TARGET_EMAIL,
            raw
        )

        pop.dele(i)

        print("[OK] forwarded")

    except Exception as e:
        print(f"[ERROR] {i}: {e}")

# ======================
# CLEANUP
# ======================
pop.quit()

try:
    smtp.quit()
except:
    pass

print("\nDone.")
