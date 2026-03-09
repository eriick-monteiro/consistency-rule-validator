import streamlit as st
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# 1. LEITURA DE DADOS
# ─────────────────────────────────────────────

REQUIRED_COLUMNS = {"Opening Date", "Closing Date", "Trade PnL"}


def load_data(uploaded_file) -> pd.DataFrame:
    """Lê o arquivo CSV ou Excel enviado pelo usuário.

    Suporta:
    - Separador vírgula ou ponto-e-vírgula
    - Encoding UTF-8 com ou sem BOM
    - Arquivos .xlsx
    """
    if uploaded_file.name.endswith(".csv"):
        raw = uploaded_file.read()
        # Detecta separador: ponto-e-vírgula tem precedência se presente
        sample = raw[:2048].decode("utf-8-sig", errors="replace")
        sep = ";" if ";" in sample else ","
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, sep=sep, encoding="utf-8-sig")
    else:
        df = pd.read_excel(uploaded_file)

    # Remove colunas totalmente vazias (artefato de trailing separator)
    df = df.dropna(axis=1, how="all")
    # Limpa espaços extras nos nomes das colunas
    df.columns = df.columns.str.strip()
    return df


def validate_columns(df: pd.DataFrame) -> list[str]:
    """Retorna lista de colunas obrigatórias ausentes."""
    return [col for col in REQUIRED_COLUMNS if col not in df.columns]


# ─────────────────────────────────────────────
# 2. PROCESSAMENTO
# ─────────────────────────────────────────────

def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mantém apenas as 3 colunas necessárias, converte tipos e remove inválidos.

    Aceita datas nos formatos:
    - MM/DD/YYYY HH:MM:SS  (ex: 02/26/2026 12:36:08)
    - DD/MM/YYYY           (ex: 26/02/2026)
    - Qualquer formato reconhecido pelo pandas (fallback)
    """

    # Descarta tudo exceto as colunas de trabalho
    df = df[["Opening Date", "Closing Date", "Trade PnL"]].copy()

    for col in ("Opening Date", "Closing Date"):
        # Tenta MM/DD/YYYY HH:MM:SS primeiro (formato do arquivo real)
        parsed = pd.to_datetime(df[col], format="%m/%d/%Y %H:%M:%S", errors="coerce")
        # Para linhas que falharam, tenta inferência genérica
        mask_failed = parsed.isna()
        if mask_failed.any():
            parsed[mask_failed] = pd.to_datetime(
                df.loc[mask_failed, col], dayfirst=True, errors="coerce"
            )
        df[col] = parsed

    df["Trade PnL"] = pd.to_numeric(df["Trade PnL"], errors="coerce")

    # Remove linhas com qualquer valor inválido após conversão
    df.dropna(inplace=True)
    return df


# ─────────────────────────────────────────────
# 3. CÁLCULOS
# ─────────────────────────────────────────────

def aggregate_by_date(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    """Agrupa por data escolhida (sem horário) e soma o Trade PnL do dia."""
    df = df.copy()

    # Remove a parte de horário para que todas as linhas do
    # mesmo dia sejam agrupadas juntas
    df["_date"] = df[date_column].dt.normalize()
    grouped = (
        df.groupby("_date")["Trade PnL"]
        .sum()
        .reset_index()
        .rename(columns={"_date": "Data", "Trade PnL": "PnL do Dia"})
        .sort_values("Data")
    )
    grouped["Data"] = grouped["Data"].dt.strftime("%d/%m/%Y")
    return grouped


def compute_consistency(
    df_agg: pd.DataFrame,
    limit_pct: float,
    include_negatives: bool,
) -> tuple[pd.DataFrame, float]:
    """
    Calcula percentual por dia e marca dias que violam o limite.

    include_negatives: se True, dias negativos também podem ser sinalizados
    (usando valor absoluto do %); se False, só dias positivos são verificados
    contra o limite.
    """
    total = df_agg["PnL do Dia"].sum()
    df = df_agg.copy()
    df["% do Total"] = (df["PnL do Dia"] / total * 100).round(2)

    if include_negatives:
        df["Excede Limite"] = df["% do Total"].abs() > limit_pct
    else:
        # Dias negativos nunca são considerados violadores
        df["Excede Limite"] = (df["PnL do Dia"] > 0) & (df["% do Total"] > limit_pct)

    return df, total


# ─────────────────────────────────────────────
# 4. VISUALIZAÇÃO
# ─────────────────────────────────────────────

def _color_pnl(val: float) -> str:
    if val > 0:
        return "color: #27ae60"
    if val < 0:
        return "color: #e74c3c"
    return ""


def main():
    page_title = st.session_state.get("page_title", "CRV - Consistency Rule Validator")
    st.set_page_config(page_title=page_title, layout="wide")
    title_placeholder = st.empty()
    st.caption("Consolide resultados por data e valide a regra de consistência percentual.")

    # ── Upload ──────────────────────────────
    uploaded_file = st.file_uploader(
        "Faça o upload da planilha de trades (.csv ou .xlsx)",
        type=["csv", "xlsx"],
    )

    if uploaded_file is None:
        st.session_state.pop("page_title", None)
        title_placeholder.title("📊 Consistency Rule Validator - CRV")
        st.info("Aguardando upload do arquivo para iniciar a análise.")
        return

    file_name = uploaded_file.name.rsplit(".", 1)[0]
    st.session_state["page_title"] = f"{file_name} - CRV"
    title_placeholder.title(f"📊 {file_name} - CRV")

    # ── Leitura e validação ─────────────────
    df_raw = load_data(uploaded_file)
    missing = validate_columns(df_raw)
    if missing:
        st.error(f"Colunas obrigatórias ausentes: **{', '.join(missing)}**")
        st.stop()

    with st.expander("Ver tabela original", expanded=False):
        st.dataframe(df_raw, use_container_width=True)

    # ── Pré-processamento ───────────────────
    df = preprocess(df_raw)
    if df.empty:
        st.error("Nenhum dado válido encontrado após o tratamento. Verifique o arquivo.")
        st.stop()

    # ── Parâmetros principais ───────────────
    st.divider()
    col_a, col_b = st.columns([1, 1])

    with col_a:
        date_choice = st.selectbox(
            "📅 Agregar por qual data?",
            options=["Opening Date", "Closing Date"],
            help="Escolha a coluna de data base para a consolidação diária.",
        )

    with col_b:
        limit_pct = st.number_input(
            "⚠️ Limite de consistência (%)",
            min_value=1.0,
            max_value=100.0,
            value=40.0,
            step=1.0,
            help="Dias cujo PnL represente mais do que este percentual do total serão sinalizados.",
        )

    # ── Toggles ─────────────────────────────
    st.divider()
    tog1, tog2, tog3 = st.columns(3)

    with tog1:
        include_negatives = st.toggle(
            "Incluir dias negativos na regra de %",
            value=False,
            help="Por padrão, dias com PnL negativo não são sinalizados como violadores do limite. "
                 "Ative para verificar o valor absoluto de todos os dias.",
        )

    with tog2:
        only_positive = st.toggle(
            "Exibir apenas dias positivos",
            value=False,
            help="Filtra a tabela e o gráfico para mostrar somente dias com PnL > 0.",
        )

    with tog3:
        show_above_100 = st.toggle(
            "Destacar dias acima de $100",
            value=True,
            help="Exibe uma seção separada listando os dias com PnL consolidado acima de $100.",
        )

    # ── Cálculos ────────────────────────────
    df_agg = aggregate_by_date(df, date_choice)

    # Quando only_positive está ativo, o cálculo do total e dos % usa apenas dias positivos
    df_agg_calc = df_agg[df_agg["PnL do Dia"] > 0].copy() if only_positive else df_agg
    df_result, total_pnl = compute_consistency(df_agg_calc, limit_pct, include_negatives)

    df_display = df_result.copy()

    violations = df_result["Excede Limite"].sum()
    days_above_100 = df_result[df_result["PnL do Dia"] > 100]

    # ── Métricas no topo ────────────────────
    consistency_ok = violations == 0
    st.divider()
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total de Trades", len(df))
    m2.metric("Dias com Operação", len(df_result))
    m3.metric(
        "PnL Total Geral",
        f"{total_pnl:,.2f}",
        delta=f"{limit_pct:.0f}% = {total_pnl * limit_pct / 100:,.2f}",
        delta_color="off",
    )
    m4.metric(
        "Dias que Excedem o Limite",
        int(violations),
        delta=f"{limit_pct:.0f}% limite",
        delta_color="inverse" if violations > 0 else "off",
    )
    m5.metric("Dias acima de $100", len(days_above_100))
    m6.metric(
        "Regra de Consistência",
        "✅ Dentro" if consistency_ok else "🔴 Violada",
    )

    # ── Tabela consolidada ──────────────────
    st.subheader("📋 Resultado Consolidado por Data")

    display_df = df_display.copy()
    display_df["% do Total"] = display_df["% do Total"].map(lambda x: f"{x:.2f}%")
    display_df["Excede Limite"] = display_df["Excede Limite"].map(
        lambda x: "🔴 Sim" if x else "✅ Não"
    )
    display_df = display_df.rename(columns={"Excede Limite": f"Excede {limit_pct:.0f}%?"})
    styled = display_df.style.format({"PnL do Dia": "{:,.2f}"}).map(_color_pnl, subset=["PnL do Dia"])

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Gráfico ─────────────────────────────
    st.subheader("📈 PnL por Dia")
    chart_df = df_display.set_index("Data")[["PnL do Dia"]].copy()
    st.bar_chart(chart_df, color="#4C78A8")

    # ── Dias acima de $100 ──────────────────
    if show_above_100:
        st.subheader("💰 Dias com PnL acima de $100")
        if len(days_above_100) > 0:
            above_df = days_above_100.copy()
            above_df["% do Total"] = above_df["% do Total"].map(lambda x: f"{x:.2f}%")
            above_df = above_df.drop(columns=["Excede Limite"])
            styled_above = above_df.style.format({"PnL do Dia": "{:,.2f}"}).map(_color_pnl, subset=["PnL do Dia"])
            st.dataframe(styled_above, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum dia teve PnL consolidado acima de $100.")

    # ── Detalhes de violações ───────────────
    if violations > 0:
        st.subheader(f"🚨 Dias que excedem {limit_pct:.0f}% do total")
        violating = df_result[df_result["Excede Limite"]].copy()
        violating["% do Total"] = violating["% do Total"].map(lambda x: f"{x:.2f}%")
        violating = violating.drop(columns=["Excede Limite"])
        styled_viol = violating.style.format({"PnL do Dia": "{:,.2f}"}).map(_color_pnl, subset=["PnL do Dia"])
        st.dataframe(styled_viol, use_container_width=True, hide_index=True)
    else:
        st.success(f"Nenhum dia excedeu o limite de {limit_pct:.0f}% do total.")


if __name__ == "__main__":
    main()
