"""Send an approved report through the deployment's authenticated SMTP account."""
import re
import smtplib
import ssl
from email.message import EmailMessage


def send_report(recipient, attachments, settings):
    if not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", recipient):
        raise ValueError("Informe um único endereço de e-mail válido.")
    if not attachments:
        raise ValueError("Nenhum relatório disponível para envio.")
    message = EmailMessage()
    message["Subject"] = "Relatório mensal de investimentos"
    message["From"] = settings["SMTP_FROM"]
    message["To"] = recipient
    message.set_content("Olá! Segue em anexo o relatório mensal de investimentos.")
    for filename, content, mime in attachments:
        main, sub = mime.split("/", 1)
        message.add_attachment(content, maintype=main, subtype=sub, filename=filename)
    port = int(settings.get("SMTP_PORT") or 587)
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    kwargs = {"context": context} if port == 465 else {}
    with factory(settings["SMTP_HOST"], port, timeout=30, **kwargs) as server:
        if port != 465:
            server.starttls(context=context)
        server.login(settings["SMTP_USER"], settings["SMTP_PASSWORD"])
        server.send_message(message)
