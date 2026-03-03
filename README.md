# Trade PnL Analyzer

Aplicação local em Streamlit para consolidação e análise de consistência de trades.

## Funcionalidades

- Upload de planilha `.csv` ou `.xlsx`
- Agrupamento por **Opening Date** ou **Closing Date**
- Cálculo do PnL total por dia
- Cálculo percentual de cada dia em relação ao total
- Regra de consistência: destaca dias que excedem o limite configurado
- Gráfico de barras com PnL diário
- Métricas resumidas no topo

## Estrutura do Projeto

```
consistency-rule-validator/
├── app.py            # Aplicação principal
├── requirements.txt  # Dependências Python
├── sample_data.csv   # Dados de exemplo para teste
└── README.md
```

## Colunas Obrigatórias na Planilha

| Coluna         | Tipo    | Descrição                        |
|----------------|---------|----------------------------------|
| `Opening Date` | Data    | Data de abertura do trade        |
| `Closing Date` | Data    | Data de fechamento do trade      |
| `Trade PnL`    | Número  | Resultado financeiro do trade    |

Outras colunas são ignoradas automaticamente.

## Como Rodar Localmente

### 1. Criar e ativar ambiente virtual

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows
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

## Como Usar

1. Faça upload do seu arquivo `.csv` ou `.xlsx`
2. Escolha a coluna de data para agregação (Opening ou Closing Date)
3. Defina o limite de consistência percentual (padrão: 40%)
4. Analise os resultados na tabela e no gráfico
5. Verifique os dias sinalizados que ultrapassaram o limite

## Exemplo de Dados

Use o arquivo `sample_data.csv` incluído para testar a aplicação.
