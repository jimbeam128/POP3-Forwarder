import os
import poplib
import smtplib
from email import message_from_bytes
from email.message import EmailMessage
from email.header import decode_header, make_header

def clean_header(value):
    if not value:
        return "(no subject)"
    try:
        return str(make_header(decode_header(value))).replace('\r', '').replace('\n', '')
    except Exception:
        return "(invalid subject)"

# ======================
# POP3 Konfiguration
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']

# ======================
# SMTP Konfiguration
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']

SMTP_FROM = os.environ['SMTP_FROM']   # MUSS deine Domain sein
SMTP_FROM_NAME = "POP3 Forwarder"

# ======================
# Zieladresse (Gmail)
# ======================
TARGET_EMAIL = os.environ['TARGET_EMAIL']

# ======================
# Verbindung zu POP3
# ======================
pop_conn = poplib.POP3_SSL(POP3_HOST)
pop_conn.user(POP3_USER)
pop_conn.pass_(POP3_PASS)

num_messages = len(pop_conn.list()[1])
print(f"{num_messages} Mails im Quellpostfach gefunden.")

# ======================
# Verbindung zu SMTP
# ======================
smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
smtp.starttls()
smtp.login(SMTP_USER, SMTP_PASS)

# ======================
# Weiterleitung
# ======================
for i in range(num_messages):
    try:
        # Mail abrufen
        resp, lines, octets = pop_conn.retr(i + 1)
        msg_content = b"\r\n".join(lines)
        email_msg = message_from_bytes(msg_content)

        # Original-Absender für Reply-To / X-Original-From
        original_from = email_msg.get('From', 'unknown@example.com')
        original_subject = clean_header(email_msg.get('Subject'))

        # Dynamischer Absendername für From und Betreff
        sender = str(make_header(decode_header(original_from)))
        if "<" in sender:
            sender_name = sender.split("<")[0].strip()
        else:
            sender_name = sender

        # Neues Forward-Objekt
        forward = EmailMessage()
        forward['Subject'] = original_subject
        forward['From'] = f"{sender_name} <{SMTP_FROM}>"
        forward['To'] = TARGET_EMAIL
        forward['Reply-To'] = original_from
        forward['X-Original-From'] = original_from
        forward['X-Forwarded-By'] = SMTP_FROM_NAME

        # Multipart / HTML / Plaintext / Attachments
        if email_msg.is_multipart():
            for part in email_msg.walk():
                ctype = part.get_content_type()
                cdisp = str(part.get('Content-Disposition'))
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'

                if payload is None:
                    continue

                if ctype == 'text/plain' and 'attachment' not in cdisp:
                    forward.set_content(payload.decode(charset, errors='replace'), subtype='plain')
                elif ctype == 'text/html' and 'attachment' not in cdisp:
                    forward.add_alternative(payload.decode(charset, errors='replace'), subtype='html')
                elif 'attachment' in cdisp:
                    filename = part.get_filename()
                    if filename:
                        forward.add_attachment(payload,
                                               maintype=part.get_content_maintype(),
                                               subtype=part.get_content_subtype(),
                                               filename=filename)
        else:
            payload = email_msg.get_payload(decode=True)
            charset = email_msg.get_content_charset() or 'utf-8'
            ctype = email_msg.get_content_type()
            if payload is not None:
                if ctype == 'text/html':
                    forward.add_alternative(payload.decode(charset, errors='replace'), subtype='html')
                else:
                    forward.set_content(payload.decode(charset, errors='replace'), subtype='plain')

        # Mail senden
        smtp.send_message(forward)
        print(f"[OK] Mail {i + 1} weitergeleitet.")

        # Erfolgreich → aus POP3 löschen
        pop_conn.dele(i + 1)

    except Exception as e:
        print(f"[FEHLER] Mail {i + 1} konnte nicht weitergeleitet werden: {e}")

# ======================
# Verbindungen schließen
# ======================
pop_conn.quit()
smtp.quit()
print("Alle Mails verarbeitet.")
