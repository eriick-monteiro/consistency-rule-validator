# Consistency Rule Validator (CRV)

Aplicação Streamlit para consolidação e análise de consistência de trades em contas de prop trading.

## Funcionalidades

### Gerenciamento de Planilhas
- Upload de arquivos `.csv` ou `.xlsx`
- **Menu lateral** com planilhas já enviadas para acesso rápido (sem re-upload)
- Remoção de planilhas salvas diretamente pelo sidebar
- Configurações de conta salvas por planilha em JSON, carregadas automaticamente ao trocar de planilha

---

## Navegação por Abas

A aplicação é dividida em duas abas principais:

### ⚙️ Consistency Rule
Focada na análise de consistência. Contém:
- Seleção de coluna de data e configuração do valor inicial da conta
- Limite de consistência percentual (padrão: 40%)
- Métricas no topo, toggles de visualização
- Tabela consolidada por dia com violações destacadas
- Plano de recuperação (quando a regra está violada)
- Histórico de Daily Loss (quando configurado)

### 📊 Dashboard
Visão operacional da conta. Contém:
- **Painel de configuração** — permite editar diretamente Tamanho da Conta, Profit Target, Drawdown Máximo e Drawdown Diário; salva automaticamente ao alterar
- **Gráficos Donut** — exibem o progresso em % para Profit, Drawdown e Daily Drawdown (apenas quando configurados > 0)
- **Painel de status** — Status, HWM, Saldo Atual, Saldo Flutuante, Drawdown Máximo calculado pelo modo ativo
- **Tipo de Drawdown** — seletor Static / EOD / Trailing visível ao lado do toggle "Por trade"
- **Estatísticas de negociação** — Melhor/Pior Negociação, Média Lucro/Perda, Negociações, Taxa de Vitória, Fator de Lucro
- **Gráfico de Acompanhamento de Saldo** — com linhas de referência dinâmicas
- **Resultado Consolidado por Data** — mesma tabela da aba Consistency Rule

---

## Parâmetros de Conta

| Parâmetro | Onde configurar | Descrição |
|---|---|---|
| Valor inicial da conta (K) | Ambas as abas | Ex.: `25` = $25.000 |
| Limite de consistência (%) | ⚙️ Consistency Rule | Padrão 40% |
| Profit Target (K) | 📊 Dashboard | Meta de lucro acima do saldo inicial |
| Drawdown Máximo (K) | 📊 Dashboard | Perda máxima tolerada |
| Drawdown Diário (K) | 📊 Dashboard | Limite de perda por dia |
| Tipo de Drawdown | ⚙️ Consistency Rule | Static, EOD ou Trailing |

---

## Tipos de Drawdown

| Tipo | Comportamento |
|---|---|
| Static | Limite fixo abaixo do saldo inicial durante toda a conta |
| EOD | Limite recalculado ao fim de cada dia com base no saldo de fechamento |
| Trailing | Limite acompanha o pico histórico (HWM), travando no saldo inicial ao atingir breakeven |

### HWM e Drawdown Máximo nos Donuts
- **HWM** (High Water Mark): calculado per-trade em ordem cronológica — maior saldo já atingido em qualquer momento
- **Drawdown Máximo** exibido no painel: reflete o limite efetivo do modo ativo
  - Trailing → `min(HWM − dd_amount, saldo_inicial)`
  - EOD → `saldo_EOD_anterior − dd_amount`
  - Static → `saldo_inicial − dd_amount`

---

## Status da Conta

| Status | Cor | Condição |
|---|---|---|
| Aprovado | 🟢 Verde | Saldo ≥ Profit Target |
| Failed | 🔴 Vermelho | Saldo ≤ limite de drawdown efetivo |
| Temporary Blocked | 🟠 Laranja | Drawdown diário atingido no último dia |
| Active | 🟢 Verde | Nenhuma das condições acima |

---

## Métricas no Topo (aba Consistency Rule)

| Métrica | Descrição |
|---|---|
| Total de Trades | Número de operações no arquivo |
| Dias com Operação | Dias únicos após consolidação |
| Consistência Atual | % do maior dia em relação ao total (delta vs limite) |
| PnL Total Geral | Soma do PnL; delta mostra o valor equivalente ao limite % |
| Saldo da Conta | Valor inicial + PnL total real |
| Dias que Excedem o Limite | Contagem de violações da regra de consistência |
| Dias acima de $100 | Dias com PnL consolidado > $100 |
| Regra de Consistência | ✅ Dentro / 🔴 Violada |

---

## Toggles

| Toggle | Comportamento |
|---|---|
| Incluir dias negativos na regra de % | Considera dias negativos no cálculo do percentual |
| Exibir apenas dias positivos | Filtra tabela e recalcula usando só dias positivos |
| Destacar dias acima de $100 | Exibe seção separada com esses dias |
| Por trade | Exibe gráfico de saldo por operação (em vez de por dia) |

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
3. Na aba **📊 Dashboard**: configure Tamanho da Conta, Profit, Drawdown e DD Diário — salva automaticamente
4. Na aba **⚙️ Consistency Rule**: ajuste a coluna de data, limite de consistência e tipo de drawdown
5. Clique em **💾 Salvar configurações** para persistir limite % e tipo de drawdown
6. Analise métricas, tabela consolidada e gráficos
7. Use o botão **🚪 Sair** no sidebar para encerrar a sessão

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
