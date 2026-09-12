# Envio pelo Gmail

O botão Enviar por e-mail aparece depois de gerar um relatório aprovado e atual.
Anexa o PDF quando disponível e o HTML. O envio acontece somente ao clicar no
botão, para o endereço informado na tela.

Nos Secrets do aplicativo no Streamlit, acrescente:

```toml
SMTP_USER = "seuemail@gmail.com"
SMTP_PASSWORD = "senha de app do Google"
```

Use uma senha de app, criada na conta Google com verificação em duas etapas.
Não use a senha habitual e não coloque credenciais no GitHub.
Instruções oficiais: https://support.google.com/mail/answer/185833?hl=pt-BR

O padrão é smtp.gmail.com, porta 587, conexão STARTTLS. SMTP_HOST, SMTP_PORT e
SMTP_FROM podem ser configurados se necessário. Porta 465 usa TLS desde o início.
O ambiente de hospedagem precisa permitir conexão ao servidor SMTP.

Após configurar e publicar, gere o relatório e teste com seu próprio endereço.
A confirmação indica aceitação pelo servidor, não entrega garantida na caixa de entrada.
Os testes automatizados simulam o servidor: não enviam mensagens reais.
