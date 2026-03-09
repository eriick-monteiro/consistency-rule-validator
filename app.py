import io
import json
import types
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

UPLOADS_DIR = Path(__file__).parent / "uploads"

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

def _load_settings(stem: str) -> dict:
    p = UPLOADS_DIR / f"{stem}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _save_settings(stem: str, account_k: float, drawdown_k: float, profit_k: float) -> None:
    (UPLOADS_DIR / f"{stem}.json").write_text(json.dumps({
        "account_value_k": account_k,
        "max_drawdown_k":  drawdown_k,
        "profit_target_k": profit_k,
    }))


def _make_file_like(path: Path):
    """Cria um objeto file-like a partir de um arquivo local, compatível com load_data()."""
    data = path.read_bytes()
    buf = io.BytesIO(data)
    obj = types.SimpleNamespace(name=path.name, read=buf.read, seek=buf.seek)
    return obj


def _build_segments(dates: list, balances: list, threshold: float) -> list:
    """Divide a série em segmentos verde/vermelho conforme cruzam o threshold."""
    segments = []
    seg_x = [dates[0]]
    seg_y = [balances[0]]
    current_above = balances[0] >= threshold

    for i in range(1, len(dates)):
        prev_above = balances[i - 1] >= threshold
        curr_above = balances[i] >= threshold

        if prev_above != curr_above and balances[i] != balances[i - 1]:
            # Ponto de cruzamento interpolado
            t = (threshold - balances[i - 1]) / (balances[i] - balances[i - 1])
            cross_x = dates[i - 1] + (dates[i] - dates[i - 1]) * t
            seg_x.append(cross_x)
            seg_y.append(threshold)
            segments.append((list(seg_x), list(seg_y), "#27ae60" if current_above else "#e74c3c"))
            current_above = curr_above
            seg_x = [cross_x, dates[i]]
            seg_y = [threshold, balances[i]]
        else:
            seg_x.append(dates[i])
            seg_y.append(balances[i])

    segments.append((list(seg_x), list(seg_y), "#27ae60" if current_above else "#e74c3c"))
    return segments


def build_balance_chart(
    trade_dates,
    daily_pnl_values,
    initial_balance: float,
    max_drawdown: float | None = None,
    profit_target: float | None = None,
) -> go.Figure:
    """Gráfico de saldo acumulado estilo drawdown de prop firms."""
    dates_dt = pd.to_datetime(trade_dates, format="%d/%m/%Y")
    start_date = dates_dt.iloc[0] - pd.Timedelta(days=1)
    all_dates = [start_date] + list(dates_dt)
    cum_pnl = [0.0] + list(pd.Series(list(daily_pnl_values)).cumsum())
    balances = [initial_balance + c for c in cum_pnl]

    segments = _build_segments(all_dates, balances, initial_balance)

    fig = go.Figure()
    seen_colors: set = set()
    for seg_x, seg_y, color in segments:
        name = "Acima do Inicial" if color == "#27ae60" else "Abaixo do Inicial"
        show_legend = color not in seen_colors
        seen_colors.add(color)
        fig.add_trace(go.Scatter(
            x=seg_x,
            y=seg_y,
            mode="lines",
            line=dict(color=color, width=2),
            name=name,
            showlegend=show_legend,
            hovertemplate="%{x|%d/%m/%Y}<br><b>$%{y:,.2f}</b><extra></extra>",
        ))

    fig.add_hline(
        y=initial_balance,
        line_dash="dot", line_color="#888888", line_width=1.5,
        annotation_text=f"  Initial Balance: ${initial_balance:,.2f}",
        annotation_position="top left",
        annotation_font_color="#aaaaaa",
    )
    if max_drawdown is not None:
        fig.add_hline(
            y=max_drawdown,
            line_dash="dot", line_color="#e74c3c", line_width=1.5,
            annotation_text=f"  Max Drawdown: ${max_drawdown:,.2f}",
            annotation_position="bottom left",
            annotation_font_color="#e74c3c",
        )
    if profit_target is not None:
        fig.add_hline(
            y=profit_target,
            line_dash="dot", line_color="#27ae60", line_width=1.5,
            annotation_text=f"  Profit Target: ${profit_target:,.2f}",
            annotation_position="top left",
            annotation_font_color="#27ae60",
        )

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font=dict(color="#fafafa", size=12),
        xaxis=dict(gridcolor="#1e2030", showgrid=True, tickformat="%d/%m/%Y", title=""),
        yaxis=dict(gridcolor="#1e2030", showgrid=True, tickprefix="$", tickformat=",.2f", title="Saldo"),
        hovermode="x unified",
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#444", borderwidth=1),
        margin=dict(l=20, r=20, t=30, b=20),
        height=420,
    )
    return fig


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

    # ── Sidebar: planilhas salvas ────────────
    UPLOADS_DIR.mkdir(exist_ok=True)
    saved_files = sorted(
        list(UPLOADS_DIR.glob("*.csv")) + list(UPLOADS_DIR.glob("*.xlsx")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    with st.sidebar:
        st.header("📁 Planilhas Salvas")
        options = ["⬆️ Novo upload"] + [f.name for f in saved_files]
        sidebar_choice = st.radio("", options, label_visibility="collapsed")

        if sidebar_choice != "⬆️ Novo upload":
            st.divider()
            if st.button("🗑️ Remover planilha", use_container_width=True):
                (UPLOADS_DIR / sidebar_choice).unlink(missing_ok=True)
                st.rerun()

    # ── Fonte do arquivo ─────────────────────
    file_source = None
    file_name = None

    if sidebar_choice == "⬆️ Novo upload":
        uploaded_file = st.file_uploader(
            "Faça o upload da planilha de trades (.csv ou .xlsx)",
            type=["csv", "xlsx"],
        )
        if uploaded_file is not None:
            save_path = UPLOADS_DIR / uploaded_file.name
            save_path.write_bytes(uploaded_file.read())
            uploaded_file.seek(0)
            file_source = uploaded_file
            file_name = uploaded_file.name.rsplit(".", 1)[0]
    else:
        selected_path = UPLOADS_DIR / sidebar_choice
        if selected_path.exists():
            file_source = _make_file_like(selected_path)
            file_name = selected_path.stem

    if file_source is None:
        st.session_state.pop("page_title", None)
        title_placeholder.title("📊 Consistency Rule Validator - CRV")
        st.info("Aguardando upload do arquivo para iniciar a análise.")
        return

    st.session_state["page_title"] = f"{file_name} - CRV"
    title_placeholder.title(f"📊 {file_name} - CRV")

    # ── Configurações persistidas por arquivo ─
    _init_key = f"_cfg_{file_name}"
    # Recarrega do JSON sempre que trocar de planilha ou na primeira visita
    if _init_key not in st.session_state or st.session_state.get("_last_file") != file_name:
        s = _load_settings(file_name)
        st.session_state[f"{file_name}_account_k"]  = float(s.get("account_value_k", 0.0))
        st.session_state[f"{file_name}_drawdown_k"] = float(s.get("max_drawdown_k",  0.0))
        st.session_state[f"{file_name}_profit_k"]   = float(s.get("profit_target_k", 0.0))
        st.session_state[_init_key] = True
    st.session_state["_last_file"] = file_name

    # ── Leitura e validação ─────────────────
    df_raw = load_data(file_source)
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
    # _btn_col, _ = st.columns([1, 5])
    # with _btn_col:
    #     if st.button("📂 Carregar configurações salvas", help="Recarrega Saldo, Drawdown e Profit do arquivo JSON"):
    #         s = _load_settings(file_name)
    #         st.session_state[f"{file_name}_account_k"]  = float(s.get("account_value_k", 0.0))
    #         st.session_state[f"{file_name}_drawdown_k"] = float(s.get("max_drawdown_k",  0.0))
    #         st.session_state[f"{file_name}_profit_k"]   = float(s.get("profit_target_k", 0.0))
    #         st.rerun()

    col_a, col_b, col_c = st.columns([1, 1, 1])

    with col_a:
        date_choice = st.selectbox(
            "📅 Agregar por qual data?",
            options=["Opening Date", "Closing Date"],
            help="Escolha a coluna de data base para a consolidação diária.",
        )

    with col_b:
        account_value_k = st.number_input(
            "💰 Valor inicial da conta (em milhares)",
            min_value=0.0,
            step=1.0,
            format="%.0f",
            help="Digite o valor em milhares. Ex: 25 = $25.000",
            key=f"{file_name}_account_k",
        )
        account_value = account_value_k * 1_000

    with col_c:
        limit_pct = st.number_input(
            "⚠️ Limite de consistência (%)",
            min_value=1.0,
            max_value=100.0,
            value=40.0,
            step=1.0,
            help="Dias cujo PnL represente mais do que este percentual do total serão sinalizados.",
        )

    col_d, col_e, _ = st.columns([1, 1, 1])
    with col_d:
        max_drawdown_k = st.number_input(
            "📉 Drawdown máximo (em milhares)",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            help="Valor abaixo do inicial. Ex: 4 = $4.000 abaixo → linha em $146.000 (conta de $150k)",
            key=f"{file_name}_drawdown_k",
        )
        max_drawdown_val = account_value - max_drawdown_k * 1_000 if max_drawdown_k > 0 else None
    with col_e:
        profit_target_k = st.number_input(
            "🎯 Meta de lucro (em milhares)",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            help="Valor acima do inicial. Ex: 9 = $9.000 acima → linha em $159.000 (conta de $150k)",
            key=f"{file_name}_profit_k",
        )
        profit_target_val = account_value + profit_target_k * 1_000 if profit_target_k > 0 else None

    if st.button("💾 Salvar configurações", help="Salva Saldo, Drawdown e Profit para esta planilha"):
        _save_settings(file_name, account_value_k, max_drawdown_k, profit_target_k)
        st.toast("Configurações salvas!", icon="✅")

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
            value=False,
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
    balance = account_value + total_pnl

    if include_negatives:
        current_max_pct = df_result["% do Total"].abs().max()
    else:
        _pos = df_result[df_result["PnL do Dia"] > 0]
        current_max_pct = _pos["% do Total"].max() if len(_pos) > 0 else 0.0

    st.divider()
    m1, m2, m2b, m3, m3b, m4, m5, m6 = st.columns(8)
    m1.metric("Total de Trades", len(df))
    m2.metric("Dias com Operação", len(df_result))
    diff_pct = current_max_pct - limit_pct
    m2b.metric(
        "Consistência Atual",
        f"{current_max_pct:.2f}%",
        delta=f"{diff_pct:+.2f}% vs {limit_pct:.0f}%",
        delta_color="inverse",
    )
    m3.metric(
        "PnL Total Geral",
        f"{total_pnl:,.2f}",
        delta=f"{limit_pct:.0f}% = {total_pnl * limit_pct / 100:,.2f}",
        delta_color="off",
    )
    if account_value > 0:
        m3b.metric(
            "Saldo da Conta",
            f"{balance:,.2f}",
            delta=f"{total_pnl:+,.2f}",
            delta_color="normal",
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

    # ── Plano de recuperação ─────────────────
    if not consistency_ok:
        violating_days = df_result[df_result["Excede Limite"]]
        v_max = violating_days["PnL do Dia"].max()
        required_total = v_max * 100 / limit_pct
        additional_needed = required_total - total_pnl
        p = limit_pct / 100

        if total_pnl > 0:
            days_needed = int(np.ceil(np.log(required_total / total_pnl) / np.log(1 + p)))
        else:
            days_needed = int(np.ceil(additional_needed / (required_total * p)))

        st.warning(
            f"**Para entrar na regra de consistência você precisa de:**\n\n"
            f"- **PnL adicional necessário:** ${additional_needed:,.2f} "
            f"(atual: ${total_pnl:,.2f} → necessário: ${required_total:,.2f})\n"
            f"- **Dias mínimos:** {days_needed} dia(s) com ganho máximo de "
            f"**{limit_pct:.0f}% do total acumulado no dia anterior**"
        )

        # Tabela dia a dia
        running = total_pnl
        plan_rows = []
        for d in range(1, days_needed + 1):
            max_day_pnl = running * p
            running += max_day_pnl
            plan_rows.append({
                "Dia": d,
                f"PnL máx. do dia ({limit_pct:.0f}% do total anterior)": max_day_pnl,
                "Total acumulado após o dia": running,
                "Regra OK?": "✅ Sim" if running >= required_total else "⏳ Não ainda",
            })
        df_plan = pd.DataFrame(plan_rows)
        styled_plan = (
            df_plan.style
            .format({
                f"PnL máx. do dia ({limit_pct:.0f}% do total anterior)": "${:,.2f}",
                "Total acumulado após o dia": "${:,.2f}",
            })
            .map(lambda v: "color: #27ae60" if v == "✅ Sim" else ("color: #f0a500" if v == "⏳ Não ainda" else ""), subset=["Regra OK?"])
        )
        st.dataframe(styled_plan, use_container_width=True, hide_index=True)

    # ── Gráfico de saldo ─────────────────────
    chart_col, btn_col = st.columns([11, 1])
    with chart_col:
        st.subheader("📈 Acompanhamento de Saldo")
    with btn_col:
        st.write("")
        st.write("")
        if st.button("🔄", help="Atualizar gráfico"):
            st.rerun()

    fig = build_balance_chart(
        trade_dates=df_agg["Data"],
        daily_pnl_values=df_agg["PnL do Dia"],
        initial_balance=account_value,
        max_drawdown=max_drawdown_val,
        profit_target=profit_target_val,
    )
    st.plotly_chart(fig, use_container_width=True)

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
