import os
import poplib
import smtplib
import time
from email import message_from_bytes
from email.message import EmailMessage
from email.header import decode_header, make_header

# ======================
# Helper
# ======================
def header_safe(value):
    if not value:
        return ""
    # zerlegt ALLE Whitespace-Arten (inkl. RFC folding & Unicode)
    return " ".join(str(value).split())

def decode_and_safe(header_value):
    if not header_value:
        return "(no subject)"
    try:
        return header_safe(str(make_header(decode_header(header_value))))
    except Exception:
        return "(invalid header)"

# ======================
# Failure-Tracking
# ======================
FAILURE_FILE = "consecutive_failures.txt"    
MAX_FAILURES = 3                             

def read_failures():                         
    if not os.path.exists(FAILURE_FILE):
        return 0
    with open(FAILURE_FILE, "r") as f:
        return int(f.read().strip() or 0)

def write_failures(n):                       
    with open(FAILURE_FILE, "w") as f:
        f.write(str(n))

had_fatal_error = False                      

# ======================
# POP3 Konfiguration
# ======================
POP3_HOST = os.environ['POP3_HOST']
POP3_USER = os.environ['POP3_USER']
POP3_PASS = os.environ['POP3_PASS']
POP3_TIMEOUT = 30
POP3_RETRIES = 3

# ======================
# SMTP Konfiguration
# ======================
SMTP_HOST = os.environ['SMTP_HOST']
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ['SMTP_USER']
SMTP_PASS = os.environ['SMTP_PASS']

SMTP_FROM = os.environ['SMTP_FROM']
SMTP_FROM_NAME = "POP3 Forwarder"

# ======================
# Zieladresse
# ======================
TARGET_EMAIL = os.environ['TARGET_EMAIL']

# ======================
# UIDL Schutz
# ======================
UIDL_FILE = "processed_uidls.txt"
processed_uidls = set()

if os.path.exists(UIDL_FILE):
    with open(UIDL_FILE, "r") as f:
        processed_uidls = set(line.strip() for line in f if line.strip())

try:
    # ======================
    # Verbindung zu POP3 (mit Retry)
    # ======================
    pop_conn = None
    for attempt in range(POP3_RETRIES):
        try:
            pop_conn = poplib.POP3_SSL(POP3_HOST, timeout=POP3_TIMEOUT)
            pop_conn.user(POP3_USER)
            pop_conn.pass_(POP3_PASS)
            break
        except Exception as e:
            print(f"[WARN] POP3 Verbindung fehlgeschlagen (Versuch {attempt + 1}): {e}")
            time.sleep(5)
    
    if not pop_conn:
        had_fatal_error = True
        
    
    # ======================
    # UIDLs abrufen
    # ======================
    resp, uidl_list, _ = pop_conn.uidl()
    uidls = {}
    for entry in uidl_list:
        num, uid = entry.decode().split()
        uidls[int(num)] = uid
    
    num_messages = len(uidls)
    print(f"{num_messages} Mails im Quellpostfach gefunden.")
    
    # ======================
    # SMTP Verbindung
    # ======================
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    smtp.starttls()
    smtp.login(SMTP_USER, SMTP_PASS)
    
    # ======================
    # Weiterleitung
    # ======================
    for i in sorted(uidls.keys()):
        uid = uidls[i]
    
        if uid in processed_uidls:
            print(f"[SKIP] Mail {i} (UIDL bereits verarbeitet)")
            continue
    
        try:
            resp, lines, octets = pop_conn.retr(i)
            msg_content = b"\r\n".join(lines)
            from email import policy
            from email.parser import BytesParser
            email_msg = BytesParser(policy=policy.default).parsebytes(msg_content)
    
            # Original-Absender und Betreff dekodieren + header-safe
            from email.utils import parseaddr
            from_name, from_addr = parseaddr(str(email_msg.get('From', '')))
            reply_name, reply_addr = parseaddr(str(email_msg.get('Reply-To', '')))
    
            # Determine display name
            if from_name:
                sender_name = header_safe(from_name)
            elif reply_name:
                sender_name = header_safe(reply_name)
            elif reply_addr and "@" in reply_addr:
                sender_name = reply_addr.split("@")[0]
            elif from_addr and "@" in from_addr:
                sender_name = from_addr.split("@")[0]
            else:
                sender_name = "Mail Sender"
    
            # Preserve real reply target
            original_from = reply_addr or from_addr or "unknown@example.com"
        
            original_subject = decode_and_safe(email_msg['Subject'])
    
            forward = EmailMessage()
            forward['Subject'] = original_subject
            forward['From'] = f"{sender_name} <{header_safe(SMTP_FROM)}>"
            forward['To'] = TARGET_EMAIL
            forward['Reply-To'] = header_safe(original_from)
            forward['X-Original-From'] = header_safe(original_from)
            forward['X-Forwarded-By'] = SMTP_FROM_NAME
    
            if email_msg.is_multipart():
                for part in email_msg.walk():
                    ctype = part.get_content_type()
                    cdisp = str(part.get('Content-Disposition'))
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or 'utf-8'
    
                    if payload is None:
                        continue
    
                    if ctype == 'text/plain' and 'attachment' not in cdisp:
                        forward.set_content(payload.decode(charset, errors='replace'))
                    elif ctype == 'text/html' and 'attachment' not in cdisp:
                        forward.add_alternative(payload.decode(charset, errors='replace'), subtype='html')
                    elif 'attachment' in cdisp:
                        filename = part.get_filename()
                        if filename:
                            filename = decode_and_safe(filename)
                            forward.add_attachment(
                                payload,
                                maintype=part.get_content_maintype(),
                                subtype=part.get_content_subtype(),
                                filename=filename
                            )
            else:
                payload = email_msg.get_payload(decode=True)
                charset = email_msg.get_content_charset() or 'utf-8'
                if payload:
                    forward.set_content(payload.decode(charset, errors='replace'))
    
            smtp.send_message(forward)
            pop_conn.dele(i)
    
            processed_uidls.add(uid)
            with open(UIDL_FILE, "a") as f:
                f.write(uid + "\n")
    
            print(f"[OK] Mail {i} weitergeleitet.")
    
        except Exception as e:
            print(f"[FEHLER] Mail {i}: {e}")
            try:
                pop_conn.rset()
            except Exception:
                pass
    
    # ======================
    # Cleanup
    # ======================
    pop_conn.quit()
    smtp.quit()

except Exception as e:
    had_fatal_error = True
    print(f"[FATAL] {e}")

# ======================
# Finaler Status
# ======================
if not had_fatal_error:                       
    write_failures(0)                          
    print("Status: Erfolg")                    
else:                                          
    failures = read_failures() + 1             
    write_failures(failures)                   
    print(f"[WARN] Fehlgeschlagene Läufe in Folge: {failures}") 
    if failures >= MAX_FAILURES:              
        raise RuntimeError("Maximale Anzahl aufeinanderfolgender Fehler erreicht") 

print("Alle Mails verarbeitet.")
