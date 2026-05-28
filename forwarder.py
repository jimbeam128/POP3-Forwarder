import poplib
import socket
import sys
import time


def safe_quit(pop):
    try:
        pop.quit()
    except Exception:
        try:
            pop.close()
        except:
            pass


def safe_retr(pop, i):
    try:
        return pop.retr(i)
    except poplib.error_proto as e:
        print(f"[POP3 BROKEN MAIL SKIP] {i}: {e}")
        return None
    except (socket.error, EOFError) as e:
        print(f"[POP3 CONNECTION RESET on mail {i}]: {e}")
        return None


def reconnect():
    return poplib.POP3_SSL(POP3_HOST, timeout=30)


# ======================
# MAIN LOOP FIXED CORE
# ======================
pop = reconnect()
pop.user(POP3_USER)
pop.pass_(POP3_PASS)

resp, mails, _ = pop.list()

print(f"{len(mails)} mails")


for i in range(1, len(mails) + 1):

    print(f"\n[MAIL {i}]")

    try:

        retr = safe_retr(pop, i)

        if retr is None:
            # 🔥 RECONNECT AFTER BROKEN STATE
            safe_quit(pop)
            pop = reconnect()
            pop.user(POP3_USER)
            pop.pass_(POP3_PASS)
            continue

        resp, lines, _ = retr
        raw = b"\r\n".join(lines)

        # SEND RAW (dein SMTP code hier einsetzen)
        smtp_raw_send(
            SMTP_HOST,
            SMTP_PORT,
            SMTP_USER,
            TARGET_EMAIL,
            raw
        )

        pop.dele(i)
        print(f"[OK] {i}")

    except Exception as e:
        print(f"[ERROR] {i}: {e}")

        try:
            pop.rset()
        except:
            pass


safe_quit(pop)
print("DONE")
