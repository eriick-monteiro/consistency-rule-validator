import bcrypt
import hmac
import io
import json
import types
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go

UPLOADS_DIR = Path(__file__).parent / "uploads"


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

def _check_credentials(login: str, password: str) -> bool:
    expected_login = st.secrets.get("LOGIN", "")
    password_hash  = st.secrets.get("PASSWORD_HASH", "").encode()
    login_ok    = hmac.compare_digest(login.strip(), expected_login)
    password_ok = bool(password_hash) and bcrypt.checkpw(password.strip().encode(), password_hash)
    return login_ok and password_ok


def _login_page() -> None:
    st.set_page_config(page_title="Login — CRV", layout="centered")
    st.title("🔐 Acesso Restrito")
    st.caption("Insira suas credenciais para acessar o Consistency Rule Validator.")

    with st.form("login_form"):
        login    = st.text_input("Usuário", autocomplete="username")
        password = st.text_input("Senha", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)

    # Garante que o navegador reconheça os campos para sugestão de preenchimento automático
    components.html("""
        <script>
            const doc = window.parent.document;
            function tag() {
                doc.querySelectorAll('input[type="text"]').forEach(el => {
                    el.setAttribute("autocomplete", "username");
                    el.setAttribute("name", "username");
                });
                doc.querySelectorAll('input[type="password"]').forEach(el => {
                    el.setAttribute("autocomplete", "current-password");
                    el.setAttribute("name", "password");
                });
            }
            tag();
            setTimeout(tag, 300);
            setTimeout(tag, 800);
        </script>
    """, height=0)

    if submitted:
        if _check_credentials(login, password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")


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


def compute_daily_loss_analysis(
    df: pd.DataFrame,
    date_col: str,
    initial_balance: float,
    daily_loss_limit: float,
) -> pd.DataFrame:
    """Para cada dia de trading calcula a perda intraday máxima e detecta Soft Breach.

    A lógica replica o modelo de Daily Loss Limit de prop firms:
    - O limite é recalculado a partir do saldo inicial de CADA dia
    - O pior equity intraday é estimado pela menor equity acumulada no dia
      (soma progressiva dos trades ordenados por horário)
    - Soft Breach = perda máxima do dia >= daily_loss_limit
    """
    df_sorted = df.sort_values(date_col).copy()
    df_sorted["_date"] = df_sorted[date_col].dt.normalize()

    running_balance = initial_balance
    rows = []

    for date, day_trades in df_sorted.groupby("_date", sort=True):
        day_sorted = day_trades.sort_values(date_col)
        start_balance = running_balance

        pnls = day_sorted["Trade PnL"].tolist()
        running = 0.0
        min_running = 0.0
        for p in pnls:
            running += p
            min_running = min(min_running, running)

        min_equity = start_balance + min_running
        max_loss = start_balance - min_equity        # >= 0
        end_balance = start_balance + sum(pnls)
        remaining = max(0.0, daily_loss_limit - max_loss)

        rows.append({
            "Data": date.strftime("%d/%m/%Y"),
            "Saldo Início do Dia": start_balance,
            "Pior Equity no Dia": min_equity,
            "Perda Máx. no Dia": max_loss,
            "Limite Diário": daily_loss_limit,
            "Restante": remaining,
            "Soft Breach": max_loss >= daily_loss_limit,
        })

        running_balance = end_balance

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# 4. VISUALIZAÇÃO
# ─────────────────────────────────────────────

def _load_settings(stem: str) -> dict:
    p = UPLOADS_DIR / f"{stem}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _save_settings(
    stem: str,
    account_k: float,
    drawdown_k: float,
    profit_k: float,
    daily_dd_k: float,
    drawdown_type: str = "Static",
    limit_pct: float = 40.0,
) -> None:
    (UPLOADS_DIR / f"{stem}.json").write_text(json.dumps({
        "account_value_k":   account_k,
        "max_drawdown_k":    drawdown_k,
        "profit_target_k":   profit_k,
        "daily_drawdown_k":  daily_dd_k,
        "drawdown_type":     drawdown_type,
        "limit_pct":         limit_pct,
    }))


def _auto_save_drawdown_type(file_name: str) -> None:
    """Callback disparado quando o tipo de drawdown é alterado — persiste no JSON."""
    val = st.session_state.get(f"{file_name}_drawdown_type", "Static")
    # mantém o widget do dashboard em sincronia
    st.session_state[f"{file_name}_drawdown_type_dash"] = val
    _save_settings(
        file_name,
        st.session_state.get(f"{file_name}_account_k",    0.0),
        st.session_state.get(f"{file_name}_drawdown_k",   0.0),
        st.session_state.get(f"{file_name}_profit_k",     0.0),
        st.session_state.get(f"{file_name}_daily_dd_k",   0.0),
        val,
        st.session_state.get(f"{file_name}_limit_pct",    40.0),
    )


def _sync_dd_type_from_dash(file_name: str) -> None:
    """Callback do selectbox do dashboard — sincroniza para a chave principal e salva."""
    val = st.session_state.get(f"{file_name}_drawdown_type_dash", "Static")
    st.session_state[f"{file_name}_drawdown_type"] = val
    _save_settings(
        file_name,
        st.session_state.get(f"{file_name}_account_k",    0.0),
        st.session_state.get(f"{file_name}_drawdown_k",   0.0),
        st.session_state.get(f"{file_name}_profit_k",     0.0),
        st.session_state.get(f"{file_name}_daily_dd_k",   0.0),
        val,
        st.session_state.get(f"{file_name}_limit_pct",    40.0),
    )


def _auto_save_params_dash(file_name: str) -> None:
    """Callback para inputs do dashboard — sincroniza dash→main e persiste no JSON."""
    for src, dst in [
        ("dash_account_k",  "account_k"),
        ("dash_profit_k",   "profit_k"),
        ("dash_drawdown_k", "drawdown_k"),
        ("dash_daily_dd_k", "daily_dd_k"),
    ]:
        st.session_state[f"{file_name}_{dst}"] = st.session_state.get(f"{file_name}_{src}", 0.0)
    _save_settings(
        file_name,
        st.session_state.get(f"{file_name}_account_k",  0.0),
        st.session_state.get(f"{file_name}_drawdown_k", 0.0),
        st.session_state.get(f"{file_name}_profit_k",   0.0),
        st.session_state.get(f"{file_name}_daily_dd_k", 0.0),
        st.session_state.get(f"{file_name}_drawdown_type", "Static"),
        st.session_state.get(f"{file_name}_limit_pct",  40.0),
    )
    st.toast("Configurações salvas!", icon="✅")


def _make_file_like(path: Path):
    """Cria um objeto file-like a partir de um arquivo local, compatível com load_data()."""
    data = path.read_bytes()
    buf = io.BytesIO(data)
    obj = types.SimpleNamespace(name=path.name, read=buf.read, seek=buf.seek)
    return obj


def _compute_drawdown_series(
    balances: list,
    dd_amount: float,
    dd_type: str,
    trade_dates: list | None = None,
) -> list:
    """Calcula a série do limite de drawdown para cada ponto de saldo.

    balances   : lista de saldos (ponto 0 = saldo inicial, ponto i = após evento i)
    dd_amount  : valor absoluto do drawdown (initial_balance - static_limit)
    dd_type    : "Static" | "EOD" | "Trailing"
    trade_dates: para EOD em modo por-trade, lista de datetime de cada trade (sem o ponto inicial)
    """
    if dd_type == "Trailing":
        # Trailing until Breakeven: o limite acompanha o HWM, mas nunca ultrapassa
        # o saldo inicial da conta (travamento no breakeven).
        initial = balances[0]
        highest = initial
        result = []
        for b in balances:
            highest = max(highest, b)
            result.append(min(highest - dd_amount, initial))
        return result

    if dd_type == "EOD" and trade_dates is not None:
        # Por trade: limite atualiza na virada de dia (usa saldo EOD do dia anterior)
        current_limit = balances[0] - dd_amount
        current_day = trade_dates[0].date() if trade_dates else None
        result = [current_limit]
        for i, d in enumerate(trade_dates):
            if d.date() != current_day:
                current_limit = balances[i] - dd_amount  # saldo ao final do dia anterior
                current_day = d.date()
            result.append(current_limit)
        return result

    if dd_type == "EOD":
        # Por dia: cada ponto já é EOD → limite = saldo_EOD - dd_amount
        return [b - dd_amount for b in balances]

    # Static (default): limite fixo com base no saldo inicial
    return [balances[0] - dd_amount] * len(balances)


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
    daily_loss_limit: float | None = None,
    drawdown_type: str = "Static",
    soft_breach_dates: list | None = None,
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
            line_shape='spline',
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
        dd_amount = initial_balance - max_drawdown
        dd_limits = _compute_drawdown_series(balances, dd_amount, drawdown_type)
        fig.add_trace(go.Scatter(
            x=all_dates,
            y=dd_limits,
            mode="lines",
            line=dict(color="#e74c3c", width=1.5, dash="dot"),
            name=f"Max Drawdown ({drawdown_type})",
            hovertemplate="%{x|%d/%m/%Y}<br><b>$%{y:,.2f}</b><extra></extra>",
        ))
    if profit_target is not None:
        fig.add_hline(
            y=profit_target,
            line_dash="dot", line_color="#27ae60", line_width=1.5,
            annotation_text=f"  Profit Target: ${profit_target:,.2f}",
            annotation_position="top left",
            annotation_font_color="#27ae60",
        )
    if initial_balance > 0:
        # HWM dinâmico: máximo acumulado até cada ponto — cresce como escada
        current_hwm = balances[0]
        hwm_series = []
        for b in balances:
            current_hwm = max(current_hwm, b)
            hwm_series.append(current_hwm)
        fig.add_trace(go.Scatter(
            x=all_dates,
            y=hwm_series,
            mode="lines",
            line=dict(color="#f1c40f", width=1, dash="dash"),
            name="High Water Mark",
            hovertemplate="%{x|%d/%m/%Y}<br><b>HWM: $%{y:,.2f}</b><extra></extra>",
        ))
    if daily_loss_limit is not None:
        # Step function: threshold = start_of_day_balance - daily_loss_limit
        # Com line_shape="hv" a linha fica flat durante o dia e salta no fechamento
        daily_thresholds = [b - daily_loss_limit for b in balances]
        fig.add_trace(go.Scatter(
            x=all_dates,
            y=daily_thresholds,
            mode="lines",
            line=dict(color="#e67e22", width=1.5, dash="dot", shape="hv"),
            name="Daily Loss Limit",
            hovertemplate="%{x|%d/%m/%Y}<br><b>Limite diário: $%{y:,.2f}</b><extra></extra>",
        ))
    if soft_breach_dates:
        first = True
        for bd_str in soft_breach_dates:
            bd = pd.to_datetime(bd_str, format="%d/%m/%Y")
            fig.add_vrect(
                x0=str(bd - pd.Timedelta(hours=12)),
                x1=str(bd + pd.Timedelta(hours=12)),
                fillcolor="#c0392b",
                opacity=0.18,
                line_width=0,
                annotation_text="Soft Breach" if first else None,
                annotation_position="top left",
                annotation_font_color="#c0392b",
                annotation_font_size=9,
            )
            first = False

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font=dict(color="#fafafa", size=12),
        xaxis=dict(gridcolor="#1e2030", showgrid=True, tickformat="%d/%m/%Y", title=""),
        yaxis=dict(gridcolor="#1e2030", showgrid=True, tickprefix="$", tickformat=",.2f", title="Saldo"),
        hovermode="x unified",
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#444", borderwidth=1),
        margin=dict(l=20, r=20, t=30, b=20),
        height=440,
    )
    return fig


def build_trade_chart(
    df_trades: pd.DataFrame,
    date_col: str,
    initial_balance: float,
    max_drawdown: float | None = None,
    profit_target: float | None = None,
    daily_loss_limit: float | None = None,
    drawdown_type: str = "Static",
    soft_breach_dates: list | None = None,
) -> go.Figure:
    """Gráfico de saldo acumulado por operação individual."""
    df_sorted = df_trades.sort_values(date_col).reset_index(drop=True)
    pnl_vals = df_sorted["Trade PnL"].tolist()
    date_labels = ["Início"] + [
        d.strftime("%d/%m/%Y") for d in df_sorted[date_col]
    ]

    indices = list(range(len(pnl_vals) + 1))
    cum_pnl = [0.0] + list(pd.Series(pnl_vals).cumsum())
    balances = [initial_balance + c for c in cum_pnl]

    segments = _build_segments(indices, balances, initial_balance)

    fig = go.Figure()
    seen_colors: set = set()
    for seg_x, seg_y, color in segments:
        name = "Acima do Inicial" if color == "#27ae60" else "Abaixo do Inicial"
        show_legend = color not in seen_colors
        seen_colors.add(color)
        hover_text = [
            date_labels[int(x)] if x == int(x) and int(x) < len(date_labels) else ""
            for x in seg_x
        ]
        fig.add_trace(go.Scatter(
            x=seg_x,
            y=seg_y,
            mode="lines",
            line=dict(color=color, width=2),
            line_shape='spline',
            name=name,
            showlegend=show_legend,
            text=hover_text,
            hovertemplate="Trade %{x:.0f} — %{text}<br><b>$%{y:,.2f}</b><extra></extra>",
        ))

    fig.add_hline(
        y=initial_balance,
        line_dash="dot", line_color="#888888", line_width=1.5,
        annotation_text=f"  Initial Balance: ${initial_balance:,.2f}",
        annotation_position="top left",
        annotation_font_color="#aaaaaa",
    )
    if max_drawdown is not None:
        dd_amount = initial_balance - max_drawdown
        trade_dates_list = df_sorted[date_col].tolist()
        dd_limits = _compute_drawdown_series(balances, dd_amount, drawdown_type, trade_dates=trade_dates_list)
        fig.add_trace(go.Scatter(
            x=indices,
            y=dd_limits,
            mode="lines",
            line=dict(color="#e74c3c", width=1.5, dash="dot"),
            name=f"Max Drawdown ({drawdown_type})",
            hovertemplate="Trade %{x:.0f}<br><b>$%{y:,.2f}</b><extra></extra>",
        ))
    if profit_target is not None:
        fig.add_hline(
            y=profit_target,
            line_dash="dot", line_color="#27ae60", line_width=1.5,
            annotation_text=f"  Profit Target: ${profit_target:,.2f}",
            annotation_position="top left",
            annotation_font_color="#27ae60",
        )
    if initial_balance > 0:
        # HWM dinâmico: máximo acumulado até cada trade
        current_hwm = balances[0]
        hwm_series = []
        for b in balances:
            current_hwm = max(current_hwm, b)
            hwm_series.append(current_hwm)
        fig.add_trace(go.Scatter(
            x=indices,
            y=hwm_series,
            mode="lines",
            line=dict(color="#f1c40f", width=1, dash="dash"),
            name="High Water Mark",
            hovertemplate="Trade %{x:.0f}<br><b>HWM: $%{y:,.2f}</b><extra></extra>",
        ))
    if daily_loss_limit is not None:
        # Step function por trade: threshold = start_of_day_balance - daily_loss_limit
        daily_thresholds = [initial_balance - daily_loss_limit]
        current_day = None
        day_start_balance = initial_balance
        for i in range(len(pnl_vals)):
            trade_day = df_sorted[date_col].iloc[i].date()
            if trade_day != current_day:
                current_day = trade_day
                day_start_balance = balances[i]
            daily_thresholds.append(day_start_balance - daily_loss_limit)
        fig.add_trace(go.Scatter(
            x=indices,
            y=daily_thresholds,
            mode="lines",
            line=dict(color="#e67e22", width=1.5, dash="dot"),
            name="Daily Loss Limit",
            hovertemplate="Trade %{x:.0f}<br><b>Limite diário: $%{y:,.2f}</b><extra></extra>",
        ))
    if soft_breach_dates:
        breach_date_set = {pd.to_datetime(d, format="%d/%m/%Y").normalize() for d in soft_breach_dates}
        first = True
        for breach_date in sorted(breach_date_set):
            mask = df_sorted[date_col].dt.normalize() == breach_date
            if mask.any():
                idxs = df_sorted.index[mask].tolist()
                x0 = idxs[0] + 1 - 0.5   # +1: índice 0 no chart é o ponto inicial
                x1 = idxs[-1] + 1 + 0.5
                fig.add_vrect(
                    x0=x0, x1=x1,
                    fillcolor="#c0392b",
                    opacity=0.18,
                    line_width=0,
                    annotation_text="Soft Breach" if first else None,
                    annotation_position="top left",
                    annotation_font_color="#c0392b",
                    annotation_font_size=9,
                )
                first = False

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font=dict(color="#fafafa", size=12),
        xaxis=dict(gridcolor="#1e2030", showgrid=True, title="Trade #"),
        yaxis=dict(gridcolor="#1e2030", showgrid=True, tickprefix="$", tickformat=",.2f", title="Saldo"),
        hovermode="x unified",
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#444", borderwidth=1),
        margin=dict(l=20, r=20, t=30, b=20),
        height=440,
    )
    return fig


def _color_pnl(val: float) -> str:
    if val > 0:
        return "color: #27ae60"
    if val < 0:
        return "color: #e74c3c"
    return ""


def _build_donut(pct: float, color_filled: str) -> go.Figure:
    """Returns a thin donut gauge with rounded end caps."""
    pct_c = min(100.0, max(0.0, pct))
    color_empty = "#1e2230"

    R_outer = 1.0
    R_inner = 0.84
    R_mid = (R_outer + R_inner) / 2
    R_cap = (R_outer - R_inner) / 2

    # Clockwise from top: start = π/2, end decreases
    start_a = np.pi / 2
    end_a = start_a - (pct_c / 100.0) * 2 * np.pi

    def _ring_poly(a0, a1, n=200):
        th = np.linspace(a0, a1, n)
        xo, yo = np.cos(th) * R_outer, np.sin(th) * R_outer
        xi, yi = np.cos(th[::-1]) * R_inner, np.sin(th[::-1]) * R_inner
        return np.concatenate([xo, xi, [xo[0]]]), np.concatenate([yo, yi, [yo[0]]])

    def _circle_poly(cx, cy, r, n=40):
        th = np.linspace(0, 2 * np.pi, n + 1)
        return cx + np.cos(th) * r, cy + np.sin(th) * r

    def _scatter(x, y, color):
        return go.Scatter(
            x=x, y=y, fill="toself", fillcolor=color,
            line=dict(width=0), mode="lines",
            hoverinfo="skip", showlegend=False,
        )

    # Background ring
    xb, yb = _ring_poly(0, 2 * np.pi)
    traces = [_scatter(xb, yb, color_empty)]

    if pct_c > 0.001:
        # Filled arc
        xa, ya = _ring_poly(start_a, end_a)
        traces.append(_scatter(xa, ya, color_filled))
        # Rounded cap at start
        xc1, yc1 = _circle_poly(np.cos(start_a) * R_mid, np.sin(start_a) * R_mid, R_cap)
        traces.append(_scatter(xc1, yc1, color_filled))
        # Rounded cap at end
        xc2, yc2 = _circle_poly(np.cos(end_a) * R_mid, np.sin(end_a) * R_mid, R_cap)
        traces.append(_scatter(xc2, yc2, color_filled))

    fig = go.Figure(traces)
    fig.update_layout(
        showlegend=False,
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        margin=dict(t=5, b=5, l=5, r=5),
        height=230,
        xaxis=dict(visible=False, range=[-1.1, 1.1], scaleanchor="y", scaleratio=1, fixedrange=True),
        yaxis=dict(visible=False, range=[-1.1, 1.1], fixedrange=True),
        annotations=[dict(
            text=f"<b>{pct_c:.2f}%</b>",
            x=0.0, y=0.0, xref="x", yref="y",
            font=dict(size=18, color="white"),
            showarrow=False,
        )],
    )
    return fig


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
        if st.button("🚪 Sair", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()
        st.divider()
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
        st.session_state[f"{file_name}_account_k"]    = float(s.get("account_value_k",  0.0))
        st.session_state[f"{file_name}_drawdown_k"]   = float(s.get("max_drawdown_k",   0.0))
        st.session_state[f"{file_name}_profit_k"]     = float(s.get("profit_target_k",  0.0))
        st.session_state[f"{file_name}_daily_dd_k"]   = float(s.get("daily_drawdown_k", 0.0))
        st.session_state[f"{file_name}_drawdown_type"] = s.get("drawdown_type", "Static")
        st.session_state[f"{file_name}_limit_pct"]    = float(s.get("limit_pct", 40.0))
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

    # ── Valores lidos do session state (widgets renderizados nas abas) ──────────
    account_value_k   = st.session_state.get(f"{file_name}_account_k",    0.0)
    account_value     = account_value_k * 1_000
    limit_pct         = st.session_state.get(f"{file_name}_limit_pct",   40.0)
    max_drawdown_k    = st.session_state.get(f"{file_name}_drawdown_k",   0.0)
    max_drawdown_val  = account_value - max_drawdown_k * 1_000 if max_drawdown_k > 0 else None
    profit_target_k   = st.session_state.get(f"{file_name}_profit_k",     0.0)
    profit_target_val = account_value + profit_target_k * 1_000 if profit_target_k > 0 else None
    daily_drawdown_k  = st.session_state.get(f"{file_name}_daily_dd_k",   0.0)
    daily_loss_limit  = daily_drawdown_k * 1_000 if daily_drawdown_k > 0 else None
    include_negatives = st.session_state.get(f"{file_name}_include_neg",  False)
    only_positive     = st.session_state.get(f"{file_name}_only_pos",     False)
    show_above_100    = st.session_state.get(f"{file_name}_above_100",    False)
    date_choice       = st.session_state.get(f"{file_name}_date_choice",  "Opening Date")
    # inicializa chaves do dashboard (se ainda não existirem)
    if f"{file_name}_drawdown_type_dash" not in st.session_state:
        st.session_state[f"{file_name}_drawdown_type_dash"] = st.session_state.get(
            f"{file_name}_drawdown_type", "Static"
        )
    for _src, _dst in [
        ("account_k",  "dash_account_k"),
        ("profit_k",   "dash_profit_k"),
        ("drawdown_k", "dash_drawdown_k"),
        ("daily_dd_k", "dash_daily_dd_k"),
    ]:
        if f"{file_name}_{_dst}" not in st.session_state:
            st.session_state[f"{file_name}_{_dst}"] = st.session_state.get(f"{file_name}_{_src}", 0.0)

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
    # true_total_pnl sempre usa todos os dias (positivos e negativos),
    # independente do filtro only_positive — garante consistência com o gráfico.
    true_total_pnl = df_agg["PnL do Dia"].sum()
    balance = account_value + true_total_pnl

    hwm_balance = None
    if account_value > 0:
        if len(df) > 0:
            _sort_col = "Opening Date" if date_choice == "Opening Date" else "Closing Date"
            _pnl_sorted = df.sort_values(_sort_col)["Trade PnL"].tolist()
            _cum = list(pd.Series(_pnl_sorted).cumsum())
            hwm_balance = max([account_value] + [account_value + c for c in _cum])
        else:
            hwm_balance = account_value

    if include_negatives:
        current_max_pct = df_result["% do Total"].abs().max()
    else:
        _pos = df_result[df_result["PnL do Dia"] > 0]
        current_max_pct = _pos["% do Total"].max() if len(_pos) > 0 else 0.0

    # ── Daily Loss Analysis ──────────────────
    df_daily_loss = None
    soft_breach_dates: list[str] = []
    if daily_loss_limit and account_value > 0:
        df_daily_loss = compute_daily_loss_analysis(df, date_choice, account_value, daily_loss_limit)
        soft_breach_dates = df_daily_loss.loc[df_daily_loss["Soft Breach"], "Data"].tolist()

    tab_params, tab_dash = st.tabs(["⚙️ Consistency Rule", "📊 Dashboard"])

    with tab_dash:
        # ── Donuts de Acompanhamento ─────────────
        show_profit_donut = profit_target_k > 0 and account_value > 0
        show_dd_donut = max_drawdown_k > 0 and account_value > 0
        show_daily_donut = daily_drawdown_k > 0 and account_value > 0

        if account_value > 0:
            # ── Cálculos de stats ─────────────────
            wins = df[df["Trade PnL"] > 0]["Trade PnL"]
            losses = df[df["Trade PnL"] < 0]["Trade PnL"]
            total_trades = len(df)
            win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
            avg_win = wins.mean() if len(wins) > 0 else 0.0
            avg_loss = losses.mean() if len(losses) > 0 else 0.0
            profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")
            best_trade = df["Trade PnL"].max() if total_trades > 0 else 0.0
            worst_trade = df["Trade PnL"].min() if total_trades > 0 else 0.0
            pf_str = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"

            dd_type_label = st.session_state.get(f"{file_name}_drawdown_type", "Static")
            hwm_str = f"${hwm_balance:,.2f}" if hwm_balance is not None else "—"

            # ── Limite efetivo de drawdown (varia por modo) ──────────────────
            _dd_amount = max_drawdown_k * 1_000
            _last_eod_balance = (
                account_value + df_agg["PnL do Dia"].iloc[:-1].sum()
                if len(df_agg) > 1 else account_value
            )
            if max_drawdown_k > 0:
                if dd_type_label == "Trailing" and hwm_balance is not None:
                    current_dd_limit = min(hwm_balance - _dd_amount, account_value)
                elif dd_type_label == "EOD":
                    current_dd_limit = _last_eod_balance - _dd_amount
                else:
                    current_dd_limit = account_value - _dd_amount
                dd_max_str = f"${current_dd_limit:,.2f}"
                _dd_to_fail = max(0.0, balance - current_dd_limit)
                dd_to_fail_str = f"${_dd_to_fail:,.2f}"
            else:
                current_dd_limit = None
                dd_max_str = "—"
                dd_to_fail_str = ""

            # Status: prioridade Aprovado > Failed > Temporary Blocked > Active
            _last_day_pnl = float(df_agg.iloc[-1]["PnL do Dia"]) if len(df_agg) > 0 else 0.0
            _current_daily_dd = abs(min(0.0, _last_day_pnl))
            _daily_limit = daily_drawdown_k * 1_000
            if show_profit_donut and balance >= account_value + profit_target_k * 1_000:
                status_text, status_color = "Aprovado", "#27ae60"
            elif show_dd_donut and current_dd_limit is not None and balance <= current_dd_limit:
                status_text, status_color = "Failed", "#e74c3c"
            elif show_daily_donut and _daily_limit > 0 and _current_daily_dd >= _daily_limit:
                status_text, status_color = "Temporary Blocked", "#f0a500"
            else:
                status_text, status_color = "Active", "#27ae60"

            # ── Linha superior: donuts + painel da conta ──
            n_donuts = sum([show_profit_donut, show_dd_donut, show_daily_donut])
            if n_donuts > 0:
                top_cols = st.columns([3] * n_donuts + [3])
                panel_container = top_cols[n_donuts]
            else:
                top_cols = []
                panel_container = st.columns([1, 2, 1])[1]  # painel centralizado
            col_idx = 0

            if show_profit_donut:
                profit_limit = profit_target_k * 1_000
                current_profit = max(0.0, true_total_pnl)
                profit_pct = (current_profit / profit_limit * 100) if profit_limit > 0 else 0.0
                with top_cols[col_idx]:
                    st.plotly_chart(_build_donut(profit_pct, "#27ae60"), use_container_width=True, key=f"{file_name}_donut_profit")
                    st.markdown(
                        f"<div style='text-align:center; margin-top:-20px;'>"
                        f"<span style='color:#aaaaaa; font-size:13px;'>Meta de Lucro</span><br>"
                        f"<b style='color:white; font-size:16px;'>${profit_limit:,.2f}</b><br>"
                        f"<span style='color:#27ae60; font-size:12px;'>Lucro Atual</span><br>"
                        f"<b style='color:#27ae60; font-size:15px;'>${current_profit:,.2f}</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                col_idx += 1

            if show_dd_donut:
                dd_limit = max_drawdown_k * 1_000
                if dd_type_label == "Trailing" and hwm_balance is not None:
                    current_dd = max(0.0, hwm_balance - balance)
                elif dd_type_label == "EOD":
                    current_dd = max(0.0, _last_eod_balance - balance)
                else:
                    current_dd = max(0.0, account_value - balance)
                dd_pct = (current_dd / dd_limit * 100) if dd_limit > 0 else 0.0
                with top_cols[col_idx]:
                    st.plotly_chart(_build_donut(dd_pct, "#e74c3c"), use_container_width=True, key=f"{file_name}_donut_dd")
                    st.markdown(
                        f"<div style='text-align:center; margin-top:-20px;'>"
                        f"<span style='color:#aaaaaa; font-size:13px;'>Perda Máxima</span><br>"
                        f"<b style='color:white; font-size:16px;'>${dd_limit:,.2f}</b><br>"
                        f"<span style='color:#e74c3c; font-size:12px;'>Drawdown ({dd_type_label})</span><br>"
                        f"<b style='color:#e74c3c; font-size:15px;'>${current_dd:,.2f}</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                col_idx += 1

            if show_daily_donut:
                daily_limit = daily_drawdown_k * 1_000
                last_day_pnl = float(df_agg.iloc[-1]["PnL do Dia"]) if len(df_agg) > 0 else 0.0
                current_daily_dd = abs(min(0.0, last_day_pnl))
                daily_pct = (current_daily_dd / daily_limit * 100) if daily_limit > 0 else 0.0
                with top_cols[col_idx]:
                    st.plotly_chart(_build_donut(daily_pct, "#f0a500"), use_container_width=True, key=f"{file_name}_donut_daily")
                    st.markdown(
                        f"<div style='text-align:center; margin-top:-20px;'>"
                        f"<span style='color:#aaaaaa; font-size:13px;'>Máx. Perda Diária</span><br>"
                        f"<b style='color:white; font-size:16px;'>${daily_limit:,.2f}</b><br>"
                        f"<span style='color:#f0a500; font-size:12px;'>Drawdown Diário</span><br>"
                        f"<b style='color:#f0a500; font-size:15px;'>${current_daily_dd:,.2f}</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                col_idx += 1

            # ── Painel da conta (coluna direita) ──
            with panel_container:
                info_l, info_r = st.columns(2)
                with info_l:
                    st.markdown(
                        f"""<div style="display:flex; flex-direction:column; gap:10px; padding-top:4px;">
  <div style="background:#151820; border-radius:10px; padding:14px 16px;">
    <div style="color:#888; font-size:13px; margin-bottom:7px;">Status</div>
    <span style="background:{status_color}; color:white; font-size:14px; font-weight:700; padding:4px 14px; border-radius:6px;">{status_text}</span>
  </div>
</div>""",
                        unsafe_allow_html=True,
                    )
                    st.markdown("<div style='color:#888; font-size:12px; margin-top:10px; margin-bottom:2px;'>Tamanho da Conta (K)</div>", unsafe_allow_html=True)
                    st.number_input(
                        "Tamanho da Conta (K)",
                        min_value=0.0, step=1.0, format="%.0f",
                        key=f"{file_name}_dash_account_k",
                        label_visibility="collapsed",
                        on_change=_auto_save_params_dash, args=(file_name,),
                    )
                    st.markdown(
                        f"""<div style="background:#151820; border-radius:10px; padding:14px 16px; margin-top:6px;">
    <div style="color:#888; font-size:13px; margin-bottom:4px;">Saldo Atual</div>
    <div style="color:white; font-size:16px; font-weight:700;">${balance:,.2f}</div>
  </div>""",
                        unsafe_allow_html=True,
                    )
                with info_r:
                    st.markdown(
                        f"""<div style="display:flex; flex-direction:column; gap:10px; padding-top:4px;">
  <div style="background:#151820; border-radius:10px; padding:14px 16px;">
    <div style="color:#888; font-size:13px; margin-bottom:4px;">HWM</div>
    <div style="color:white; font-size:16px; font-weight:700;">{hwm_str}</div>
  </div>
  <div style="background:#151820; border-radius:10px; padding:14px 16px;">
    <div style="color:white; font-size:16px; font-weight:700;">—</div>
    <div style="color:#888; font-size:13px; margin-bottom:4px;">Saldo Flutuante</div>
  </div>
  <div style="background:#151820; border-radius:10px; padding:14px 16px;">
    <div style="color:#888; font-size:13px; margin-bottom:4px;">Drawdown Máximo</div>
    <div style="display:flex; align-items:baseline; gap:8px;">
      <span style="color:white; font-size:16px; font-weight:700;">{dd_max_str}</span>
      <span style="color:#f0c300; font-size:13px; font-weight:700;">{dd_to_fail_str}</span>
    </div>
  </div>
</div>""",
                        unsafe_allow_html=True,
                    )

                st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
                p_col, dd_col, ddd_col = st.columns(3)
                with p_col:
                    st.markdown("<div style='color:#888; font-size:12px; margin-bottom:2px;'>🎯 Profit (K)</div>", unsafe_allow_html=True)
                    st.number_input(
                        "Profit (K)", min_value=0.0, step=1.0, format="%.2f",
                        key=f"{file_name}_dash_profit_k",
                        label_visibility="collapsed",
                        on_change=_auto_save_params_dash, args=(file_name,),
                    )
                with dd_col:
                    st.markdown("<div style='color:#888; font-size:12px; margin-bottom:2px;'>📉 Drawdown (K)</div>", unsafe_allow_html=True)
                    st.number_input(
                        "Drawdown (K)", min_value=0.0, step=1.0, format="%.2f",
                        key=f"{file_name}_dash_drawdown_k",
                        label_visibility="collapsed",
                        on_change=_auto_save_params_dash, args=(file_name,),
                    )
                with ddd_col:
                    st.markdown("<div style='color:#888; font-size:12px; margin-bottom:2px;'>🟠 DD Diário (K)</div>", unsafe_allow_html=True)
                    st.number_input(
                        "DD Diário (K)", min_value=0.0, step=1.0, format="%.2f",
                        key=f"{file_name}_dash_daily_dd_k",
                        label_visibility="collapsed",
                        on_change=_auto_save_params_dash, args=(file_name,),
                    )

            # ── Linha inferior: stats de negociação ──
            st.write("")
            s1, s2, s3, s4, s5, s6, s7 = st.columns(7)

            def _stat_card_icon(col, icon_bg, icon, label, value, value_color):
                col.markdown(
                    f"""<div style="background:#151820; border-radius:10px; padding:12px 10px; display:flex; align-items:center; gap:10px; height:64px;">
  <span style="background:{icon_bg}; border-radius:8px; padding:7px; font-size:15px; flex-shrink:0;">{icon}</span>
  <div>
    <div style="color:#888; font-size:11px; line-height:1.2;">{label}</div>
    <div style="color:{value_color}; font-size:14px; font-weight:700; line-height:1.4;">{value}</div>
  </div>
</div>""",
                    unsafe_allow_html=True,
                )

            def _stat_card_plain(col, label, value):
                col.markdown(
                    f"""<div style="background:#151820; border-radius:10px; padding:12px 10px; text-align:center; height:64px; display:flex; flex-direction:column; justify-content:center;">
  <div style="color:#888; font-size:11px; margin-bottom:3px;">{label}</div>
  <div style="color:white; font-size:18px; font-weight:700;">{value}</div>
</div>""",
                    unsafe_allow_html=True,
                )

            _stat_card_icon(s1, "#1a3a2a", "📈", "Melhor Negociação", f"${best_trade:,.2f}", "#27ae60")
            _stat_card_icon(s2, "#3a1a1a", "📉", "Pior Negociação", f"${worst_trade:,.2f}", "#e74c3c")
            _stat_card_icon(s3, "#1a2a3a", "📊", "Média de Lucro", f"${avg_win:,.2f}", "#3498db")
            _stat_card_icon(s4, "#3a2a1a", "📊", "Média de Perda", f"${avg_loss:,.2f}", "#f0a500")
            _stat_card_plain(s5, "Negociações", str(total_trades))
            _stat_card_plain(s6, "Taxa de Vitória", f"{win_rate:.2f}%")
            _stat_card_plain(s7, "Fator de Lucro", pf_str)

            st.divider()

        # ── Gráfico de saldo ─────────────────────
        tog_col, dd_type_col = st.columns([2, 3])
        with tog_col:
            per_trade_mode = st.toggle("Por trade", value=False, help="Exibe um ponto por operação individual em vez de por dia.")
        with dd_type_col:
            drawdown_type = st.radio(
                "Tipo de Drawdown",
                options=["Static", "EOD", "Trailing"],
                label_visibility="hidden",
                disabled=max_drawdown_val is None,
                horizontal=True,
                key=f"{file_name}_drawdown_type",
                on_change=_auto_save_drawdown_type,
                args=(file_name,),
            )

        if per_trade_mode:
            fig = build_trade_chart(
                df_trades=df,
                date_col=date_choice,
                initial_balance=account_value,
                max_drawdown=max_drawdown_val,
                profit_target=profit_target_val,
                daily_loss_limit=daily_loss_limit,
                drawdown_type=drawdown_type,
                soft_breach_dates=soft_breach_dates or None,
            )
        else:
            fig = build_balance_chart(
                trade_dates=df_agg["Data"],
                daily_pnl_values=df_agg["PnL do Dia"],
                initial_balance=account_value,
                max_drawdown=max_drawdown_val,
                profit_target=profit_target_val,
                daily_loss_limit=daily_loss_limit,
                drawdown_type=drawdown_type,
                soft_breach_dates=soft_breach_dates or None,
            )
        st.plotly_chart(fig, use_container_width=True)

        # ── Histórico Daily Loss / Soft Breaches ─
        if df_daily_loss is not None:
            with st.expander("🔴 Histórico de Daily Loss Limit", expanded=False):
                n_breaches = int(df_daily_loss["Soft Breach"].sum())
                st.metric(
                    "Soft Breaches Histórico",
                    n_breaches,
                    delta="dias com bloqueio temporário",
                    delta_color="off",
                )
                if n_breaches > 0:
                    st.warning(
                        f"**{n_breaches} dia(s) com Soft Breach detectado(s).**  "
                        "A conta não foi encerrada, mas nesses dias o trading foi bloqueado temporariamente."
                    )
                else:
                    st.success("Nenhum Soft Breach detectado no período analisado.")

                dl_display = df_daily_loss.copy()
                dl_display["Soft Breach"] = dl_display["Soft Breach"].map(
                    lambda x: "🔴 Soft Breach" if x else "✅ OK"
                )
                styled_dl = (
                    dl_display.style
                    .format({
                        "Saldo Início do Dia": "${:,.2f}",
                        "Pior Equity no Dia":  "${:,.2f}",
                        "Perda Máx. no Dia":   "${:,.2f}",
                        "Limite Diário":        "${:,.2f}",
                        "Restante":            "${:,.2f}",
                    })
                    .map(
                        lambda v: "color: #c0392b; font-weight: bold" if v == "🔴 Soft Breach" else "color: #27ae60",
                        subset=["Soft Breach"],
                    )
                )
                st.dataframe(styled_dl, use_container_width=True, hide_index=True)

        # ── Resultado Consolidado por Data (Dashboard) ──────────────
        st.divider()
        st.subheader("📋 Resultado Consolidado por Data")
        _display_df = df_display.copy()
        _display_df["% do Total"] = _display_df["% do Total"].map(lambda x: f"{x:.2f}%")
        _display_df["Excede Limite"] = _display_df["Excede Limite"].map(
            lambda x: "🔴 Sim" if x else "✅ Não"
        )
        _display_df = _display_df.rename(columns={"Excede Limite": f"Excede {limit_pct:.0f}%?"})
        _styled = _display_df.style.format({"PnL do Dia": "{:,.2f}"}).map(_color_pnl, subset=["PnL do Dia"])
        st.dataframe(_styled, use_container_width=True, hide_index=True)

    with tab_params:
        # ── Parâmetros principais ───────────────
        st.subheader("⚙️ Parâmetros da Conta")
        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_a:
            date_choice = st.selectbox(
                "📅 Agregar por qual data?",
                options=["Opening Date", "Closing Date"],
                key=f"{file_name}_date_choice",
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
        with col_c:
            limit_pct = st.number_input(
                "⚠️ Limite de consistência (%)",
                min_value=1.0,
                max_value=100.0,
                step=1.0,
                help="Dias cujo PnL represente mais do que este percentual do total serão sinalizados.",
                key=f"{file_name}_limit_pct",
            )

        # Drawdown máximo, Meta de lucro e Drawdown diário são configurados na aba Dashboard
        max_drawdown_k   = st.session_state.get(f"{file_name}_drawdown_k",  0.0)
        profit_target_k  = st.session_state.get(f"{file_name}_profit_k",    0.0)
        daily_drawdown_k = st.session_state.get(f"{file_name}_daily_dd_k",  0.0)

        if st.button("💾 Salvar configurações", help="Salva Saldo, Drawdown e Profit para esta planilha"):
            _save_settings(
                file_name,
                st.session_state.get(f"{file_name}_account_k", 0.0),
                st.session_state.get(f"{file_name}_drawdown_k", 0.0),
                st.session_state.get(f"{file_name}_profit_k", 0.0),
                st.session_state.get(f"{file_name}_daily_dd_k", 0.0),
                st.session_state.get(f"{file_name}_drawdown_type", "Static"),
                st.session_state.get(f"{file_name}_limit_pct", 40.0),
            )
            st.toast("Configurações salvas!", icon="✅")

        # ── Toggles ─────────────────────────────
        st.divider()
        tog1, tog2, tog3 = st.columns(3)
        with tog1:
            include_negatives = st.toggle(
                "Incluir dias negativos na regra de %",
                value=False,
                key=f"{file_name}_include_neg",
                help="Por padrão, dias com PnL negativo não são sinalizados como violadores do limite. "
                     "Ative para verificar o valor absoluto de todos os dias.",
            )
        with tog2:
            only_positive = st.toggle(
                "Exibir apenas dias positivos",
                value=False,
                key=f"{file_name}_only_pos",
                help="Filtra a tabela e o gráfico para mostrar somente dias com PnL > 0.",
            )
        with tog3:
            show_above_100 = st.toggle(
                "Destacar dias acima de $100",
                value=False,
                key=f"{file_name}_above_100",
                help="Exibe uma seção separada listando os dias com PnL consolidado acima de $100.",
            )

        # ── Métricas ─────────────────────────────
        st.markdown("""
<style>
[data-testid="stMetricLabel"] { font-size: 20px !important; }
[data-testid="stMetricValue"] { font-size: 30px !important; }
[data-testid="stMetricDelta"] { font-size: 12px !important; }
</style>
""", unsafe_allow_html=True)
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
                delta=f"{true_total_pnl:+,.2f}",
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
        st.divider()
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
    if not st.session_state.get("authenticated"):
        _login_page()
    else:
        main()
