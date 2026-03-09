# Consistency Rule Validator (CRV)

Aplicação local em Streamlit para consolidação e análise de consistência de trades em contas de prop trading.

## Funcionalidades

### Gerenciamento de Planilhas
- Upload de arquivos `.csv` ou `.xlsx`
- **Menu lateral** com planilhas já enviadas para acesso rápido (sem necessidade de re-upload)
- Remoção de planilhas salvas diretamente pelo sidebar
- Configurações de conta (**Saldo, Drawdown, Profit Target**) salvas por planilha em arquivo JSON, carregadas automaticamente ao trocar de planilha

### Parâmetros de Análise
- Agrupamento por **Opening Date** ou **Closing Date**
- Valor inicial da conta em **milhares** (ex.: `25` = $25.000)
- Limite de consistência percentual configurável (padrão: 40%)
- **Drawdown máximo** — valor em milhares abaixo do saldo inicial (exibe linha de referência no gráfico)
- **Meta de lucro (Profit Target)** — valor em milhares acima do saldo inicial (exibe linha de referência no gráfico)

### Métricas no Topo (8 colunas)
| Métrica | Descrição |
|---|---|
| Total de Trades | Número de operações no arquivo |
| Dias com Operação | Dias únicos após consolidação |
| Consistência Atual | % do maior dia em relação ao total (delta vs limite) |
| PnL Total Geral | Soma do PnL; delta mostra o valor equivalente ao limite % |
| Saldo da Conta | Valor inicial + PnL total (exibido quando conta configurada) |
| Dias que Excedem o Limite | Contagem de violações da regra |
| Dias acima de $100 | Dias com PnL consolidado > $100 |
| Regra de Consistência | ✅ Dentro / 🔴 Violada |

### Tabela Consolidada
- PnL diário com **cores verde/vermelho** para positivo/negativo
- Percentual de cada dia em relação ao total
- Indicador de violação por linha

### Plano de Recuperação (quando a regra está violada)
- Calcula o PnL total necessário para entrar na regra
- Exibe **tabela dia a dia** com:
  - PnL máximo permitido por dia (respeitando o limite %)
  - Total acumulado após cada dia
  - Status da regra por dia (⏳ / ✅)

### Gráfico de Saldo Acumulado
- Linha que **muda de cor** (verde/vermelho) ao cruzar o saldo inicial
- Linhas de referência pontilhadas para: Initial Balance, Max Drawdown, Profit Target
- Tema escuro, hover interativo

### Toggles
| Toggle | Comportamento |
|---|---|
| Incluir dias negativos na regra de % | Considera dias negativos no cálculo do percentual |
| Exibir apenas dias positivos | Filtra tabela e recalcula total usando só dias positivos |
| Destacar dias acima de $100 | Exibe seção separada com esses dias |

---

## Estrutura do Projeto

```
consistency-rule-validator/
├── app.py              # Aplicação principal (toda a lógica em um único arquivo)
├── requirements.txt    # Dependências Python
├── sample_data.csv     # Dados de exemplo para teste
├── uploads/            # Planilhas salvas e arquivos JSON de configuração
│   ├── planilha.csv
│   ├── planilha.json   # Configurações de saldo/drawdown/profit por arquivo
│   └── ...
└── README.md
```

### Módulos internos em `app.py`

| Função | Responsabilidade |
|---|---|
| `load_data()` | Leitura de CSV/XLSX |
| `validate_columns()` | Verifica colunas obrigatórias |
| `preprocess()` | Parsing de datas e PnL |
| `aggregate_by_date()` | Consolida PnL por dia |
| `compute_consistency()` | Calcula % e sinaliza violações |
| `build_balance_chart()` | Gráfico Plotly com segmentos coloridos |
| `_build_segments()` | Interpolação do ponto de cruzamento para cores |
| `_load_settings()` / `_save_settings()` | Persistência JSON por planilha |
| `_make_file_like()` | Adaptador para arquivos lidos do disco |

---

## Colunas Obrigatórias na Planilha

| Coluna | Tipo | Descrição |
|---|---|---|
| `Opening Date` | Data | Data de abertura do trade |
| `Closing Date` | Data | Data de fechamento do trade |
| `Trade PnL` | Número | Resultado financeiro do trade |

Outras colunas são ignoradas automaticamente.

---

## Como Rodar Localmente

### 1. Criar e ativar ambiente virtual

```bash
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows
```

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Executar a aplicação

```bash
streamlit run app.py
```

A aplicação abrirá automaticamente no navegador em `http://localhost:8501`.

---

## Como Usar

1. Selecione **"⬆️ Novo upload"** no sidebar e envie seu arquivo `.csv` ou `.xlsx`
2. O arquivo é salvo automaticamente em `uploads/` para reutilização futura
3. Escolha a coluna de data para agregação (**Opening** ou **Closing Date**)
4. Configure o **valor inicial da conta** (em milhares), o **limite de consistência %**, o **drawdown máximo** e a **meta de lucro**
5. Clique em **💾 Salvar configurações** para persistir os valores por planilha
6. Analise as métricas, a tabela consolidada e o gráfico de saldo
7. Caso a regra esteja violada, consulte o **Plano de Recuperação** para ver o caminho dia a dia

---

## Dependências

```
streamlit >= 1.32.0
pandas    >= 2.2.0
numpy     >= 1.26.0
openpyxl  >= 3.1.0
plotly
```
