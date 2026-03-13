# Consistency Rule Validator (CRV)

Aplicação Streamlit para consolidação e análise de consistência de trades em contas de prop trading.

## Funcionalidades

### Gerenciamento de Planilhas
- Upload de arquivos `.csv` ou `.xlsx`
- **Menu lateral** com planilhas já enviadas para acesso rápido (sem re-upload)
- Remoção de planilhas salvas diretamente pelo sidebar
- Configurações de conta salvas por planilha em JSON, carregadas automaticamente ao trocar de planilha

### Parâmetros de Conta
- Agrupamento por **Opening Date** ou **Closing Date**
- Valor inicial da conta em **milhares** (ex.: `25` = $25.000)
- Limite de consistência percentual configurável (padrão: 40%)
- **Drawdown máximo** — valor em milhares (linha de referência no gráfico)
- **Meta de lucro (Profit Target)** — valor em milhares (linha de referência no gráfico)
- **Daily Drawdown** — limite de perda diária com histórico de violações
- **Tipo de Drawdown** — Static, EOD (End of Day) ou Trailing até Breakeven

### Tipos de Drawdown
| Tipo | Comportamento |
|---|---|
| Static | Limite fixo abaixo do saldo inicial durante toda a conta |
| EOD | Limite recalculado ao fim de cada dia com base no saldo de fechamento |
| Trailing | Limite acompanha o pico histórico (HWM), travando no saldo inicial ao atingir breakeven |

### Métricas no Topo
| Métrica | Descrição |
|---|---|
| Total de Trades | Número de operações no arquivo |
| Dias com Operação | Dias únicos após consolidação |
| Consistência Atual | % do maior dia em relação ao total (delta vs limite) |
| PnL Total Geral | Soma do PnL; delta mostra o valor equivalente ao limite % |
| Saldo da Conta | Valor inicial + PnL total real (todos os dias, positivos e negativos) |
| Dias que Excedem o Limite | Contagem de violações da regra de consistência |
| Dias acima de $100 | Dias com PnL consolidado > $100 |
| Regra de Consistência | ✅ Dentro / 🔴 Violada |

### Gráfico de Saldo
- Linha que **muda de cor** (verde/vermelho) ao cruzar o saldo inicial
- Linhas de referência para Initial Balance, Max Drawdown, Profit Target e limite de Drawdown
- Reflete fielmente todos os trades (lucros e perdas) em ordem cronológica

### Toggles
| Toggle | Comportamento |
|---|---|
| Incluir dias negativos na regra de % | Considera dias negativos no cálculo do percentual |
| Exibir apenas dias positivos | Filtra tabela e recalcula usando só dias positivos |
| Destacar dias acima de $100 | Exibe seção separada com esses dias |

---

## Estrutura do Projeto

```
consistency-rule-validator/
├── app.py                       # Aplicação principal
├── requirements.txt             # Dependências Python
├── sample_data.csv              # Dados de exemplo
├── .streamlit/
│   └── secrets.toml             # Credenciais locais (não versionado)
├── uploads/                     # Planilhas salvas e configurações JSON
│   ├── planilha.csv
│   ├── planilha.json
│   └── ...
└── README.md
```

---

## Autenticação

A aplicação possui um sistema de login protegido por senha com hash bcrypt.

### Configuração local

Crie o arquivo `.streamlit/secrets.toml` (nunca suba para o git):

```toml
LOGIN         = "seu_usuario"
PASSWORD_HASH = "$2b$12$..."   # hash bcrypt da senha
```

Para gerar o hash da senha:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'SUA_SENHA', bcrypt.gensalt()).decode())"
```

### Deploy no Streamlit Cloud

O arquivo `secrets.toml` **não precisa subir para o servidor**.

1. No painel do app → **Settings → Secrets**
2. Cole o conteúdo:
   ```toml
   LOGIN         = "seu_usuario"
   PASSWORD_HASH = "$2b$12$..."
   ```
3. Salve — o app reinicia com as credenciais disponíveis.

---

## Como Rodar Localmente

```bash
# 1. Criar e ativar ambiente virtual
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Executar
streamlit run app.py
```

A aplicação abrirá em `http://localhost:8501`.

---

## Como Usar

1. Faça login com as credenciais configuradas
2. Selecione **"⬆️ Novo upload"** no sidebar e envie seu arquivo `.csv` ou `.xlsx`
3. Escolha a coluna de data para agregação (**Opening** ou **Closing Date**)
4. Configure os **⚙️ Parâmetros da Conta**: saldo inicial, limite de consistência, drawdown, profit target e tipo de drawdown
5. Clique em **💾 Salvar configurações** para persistir os valores por planilha
6. Analise as métricas, a tabela consolidada e o gráfico de saldo
7. Caso a regra esteja violada, consulte o **Plano de Recuperação** para ver o caminho dia a dia
8. Use o botão **🚪 Sair** no sidebar para encerrar a sessão

---

## Colunas Obrigatórias na Planilha

| Coluna | Tipo | Descrição |
|---|---|---|
| `Opening Date` | Data | Data de abertura do trade |
| `Closing Date` | Data | Data de fechamento do trade |
| `Trade PnL` | Número | Resultado financeiro do trade |

Outras colunas são ignoradas automaticamente.

---

## Dependências

```
streamlit  >= 1.32.0
pandas     >= 2.2.0
numpy      >= 1.26.0
openpyxl   >= 3.1.0
bcrypt     >= 4.0.0
plotly
```
