# Apresentação do relatório

O PDF usa Liberation Sans incorporada no HTML, em pesos regular e negrito.
A licença acompanha os arquivos em assets/fonts. Texto e tabelas usam a mesma
família na impressão, sem depender das fontes do computador.

Corpo em 9,2 pt, títulos de seção em 10 pt e título principal em 16 pt.
O ajuste automático continua limitado ao orçamento existente de até 9%.
Os parágrafos são justificados; títulos e primeira coluna seguem a mesma margem
esquerda. Os números são alinhados à direita, com cifras monetárias mantidas na
mesma linha. O cabeçalho aparece apenas na primeira página. A abertura vem antes
do resumo executivo, que é um parágrafo cobrindo desempenho, cenário e ajustes.
As tabelas substituem os gráficos: evolução dos ativos, carteira hoje e alvo e
ajustes sugeridos. Nenhuma operação ou posição é omitida para caber no papel.
Assinatura e divulgação ficam fora do bloco que muda de escala, com posição
constante. As páginas são numeradas. A verificação de layout espera as fontes
carregarem antes de medir colisões e transbordamentos. O gerador pode marcar
frases importantes com **negrito**; o renderizador aceita somente essa marcação,
escapa HTML e mantém os números intactos na verificação do texto.

## Chave da OpenAI salva

Nos Secrets do aplicativo no Streamlit, acrescente esta entrada no nível
principal, junto das entradas do Gmail, usando sua chave real:

```toml
OPENAI_API_KEY = ""
```

Cole sua chave entre as aspas. Depois de salvar, a aplicação usa a chave automaticamente a cada geração.
Não é preciso digitá-la em cada acesso. A chave não faz parte do código nem
deve ser publicada no GitHub. Localmente, a mesma configuração funciona em
.streamlit/secrets.toml, que é ignorado pelo Git, ou na variável de ambiente
OPENAI_API_KEY. A chave temporária na tela é um recurso para ambientes sem
configuração persistente.
