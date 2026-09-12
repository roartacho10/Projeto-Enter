"""Send an approved report through the deployment's authenticated SMTP account."""
import re
import smtplib
import ssl
from email.message import EmailMessage


def send_report(recipient, attachments, settings, *, client_name="Albert", advisor_name="Antonio Bicudo"):
    if not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", recipient):
        raise ValueError("Informe um único endereço de e-mail válido.")
    if not attachments:
        raise ValueError("Nenhum relatório disponível para envio.")
    message = EmailMessage()
    message["Subject"] = "Relatório mensal de investimentos"
    message["From"] = settings["SMTP_FROM"]
    message["To"] = recipient
    first_name = client_name.strip().split()[0] if client_name.strip() else ""
    greeting = f"Olá, {first_name}!" if first_name else "Olá!"
    message.set_content(
        f"{greeting}\n\n"
        "Espero que esteja bem.\n\n"
        "Encaminho em anexo o relatório mensal de investimentos, com um resumo "
        "do desempenho da sua carteira e as sugestões de alocação para sua avaliação.\n\n"
        "Fico à disposição para conversarmos sobre os resultados, esclarecer dúvidas "
        "e avaliar juntos as alocações mais adequadas ao seu perfil e aos seus objetivos. "
        "Se desejar, podemos agendar uma conversa no horário que for melhor para você.\n\n"
        "Um abraço,\n"
        f"{advisor_name}\n"
        "Assessor de investimentos\n"
    )
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
