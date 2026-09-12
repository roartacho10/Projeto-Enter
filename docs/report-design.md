# Apresentação do relatório

O PDF usa Liberation Sans incorporada no HTML, em pesos regular e negrito.
A licença acompanha os arquivos em assets/fonts. Os textos dos gráficos SVG
recebem a mesma família na impressão, sem depender das fontes do computador.

Corpo em 9,2 pt, títulos de seção em 10 pt e título principal em 16 pt.
O ajuste automático continua limitado ao orçamento existente de até 9%.
O texto é alinhado à esquerda; os números são alinhados à direita, com cifras
monetárias mantidas na mesma linha. Cabeçalhos seguem a largura do conteúdo.
Assinatura e divulgação ficam fora do bloco que muda de escala, com posição
constante. As páginas são numeradas. A verificação de layout espera as fontes
carregarem antes de medir colisões e transbordamentos.

## Chave da OpenAI salva

Nos Secrets do aplicativo no Streamlit, acrescente esta entrada no nível
principal, junto das entradas do Gmail, usando sua chave real:

```toml
OPENAI_API_KEY = "sua-chave-da-openai"
```

Depois de salvar, a aplicação usa a chave automaticamente a cada geração.
Não é preciso digitá-la em cada acesso. A chave não faz parte do código nem
deve ser publicada no GitHub. Localmente, a mesma configuração funciona em
.streamlit/secrets.toml, que é ignorado pelo Git, ou na variável de ambiente
OPENAI_API_KEY. A chave temporária na tela é um recurso para ambientes sem
configuração persistente.
