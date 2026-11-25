"""Live Terminal Dashboard (Section 23) built with Rich + safe plain-text fallback."""

from __future__ import annotations

import io
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from bot.core.models import MarketStateSnapshot, Position, RiskState


@dataclass
class DashboardState:
    api_connected: bool = False
    engine_status: str = "STARTING"
    mode: str = "ANALYSIS_ONLY"
    market_states: dict[str, MarketStateSnapshot] | None = None
    open_positions: list[Position] | None = None
    risk_state: RiskState | None = None
    latest_action: str = "NONE"
    latest_reason: str = "awaiting_closed_candle"
    daily_pnl_r: float = 0.0


class TerminalDashboard:
    """Renders the GLVNS SQUEEZE TRADE MANAGEMENT ENGINE status dashboard."""

    def render_text(self, state: DashboardState) -> str:
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=96, color_system=None)
        console.print(self.build_layout(state))
        return buf.getvalue()

    def build_layout(self, state: DashboardState) -> Panel:
        api_badge = "CONNECTED" if state.api_connected else "DISCONNECTED"
        risk_lbl = state.risk_state.level.value if state.risk_state else "NORMAL"
        dd_pct = (state.risk_state.daily_drawdown_pct * 100.0) if state.risk_state else 0.0
        open_positions = state.open_positions or []
        pos_by_sym = {p.symbol: p for p in open_positions}

        header = Table.grid(expand=True)
        header.add_column(justify="left")
        header.add_column(justify="left")
        header.add_row("API", f"* {api_badge}  |  MODE: {state.mode}")
        header.add_row("ENGINE", f"* {state.engine_status}")
        header.add_row("RISK", f"* {risk_lbl} (DD: {dd_pct:.2f}%)")

        sym_table = Table(title="ACTIVE SYMBOLS & SQUEEZE STATE", expand=True)
        sym_table.add_column("Symbol", style="bold")
        sym_table.add_column("Squeeze")
        sym_table.add_column("Momentum")
        sym_table.add_column("Regime")
        sym_table.add_column("Pos Stage")
        sym_table.add_column("Current P/L", justify="right")

        for sym, ms in sorted((state.market_states or {}).items()):
            pos = pos_by_sym.get(sym)
            pos_stage = pos.stage.value if pos else "-"
            pnl_str = f"{pos.unrealized_r():+.2f}R" if pos else "+0.00R"
            sqz_short = ms.squeeze_state.value.replace("SQUEEZE_", "")
            if ms.squeeze.sqz_fired:
                sqz_short = "RELEASE"
            sym_table.add_row(
                sym,
                sqz_short,
                ms.momentum_state.value,
                ms.regime.value,
                pos_stage,
                pnl_str,
            )

        footer = Table.grid(expand=True)
        footer.add_row(f"ACTIVE TRADES: {len(open_positions)}   |   DAILY P/L: {state.daily_pnl_r:+.2f}R")
        footer.add_row(f"LATEST ACTION: {state.latest_action}   |   REASON: {state.latest_reason}")

        wrapper = Table.grid(expand=True)
        wrapper.add_row(header)
        wrapper.add_row(sym_table)
        wrapper.add_row(footer)

        return Panel(wrapper, title="GLVNS SQUEEZE TRADE MANAGEMENT ENGINE", border_style="cyan")

    def print_snapshot(self, state: DashboardState) -> None:
        text = self.render_text(state)
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))
