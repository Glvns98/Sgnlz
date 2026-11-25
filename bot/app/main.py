"""CLI entrypoint for run, backtest, walkforward, and report commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bot.analytics.management_analysis import analyze_management_quality
from bot.analytics.performance import compute_performance_metrics
from bot.analytics.trade_analysis import PostTradeLearningEngine
from bot.app.lifecycle import EngineOrchestrator
from bot.app.logging_setup import configure_logging
from bot.backtest.engine import BacktestRunner, WalkForwardValidator
from bot.backtest.replay import generate_synthetic_deriv_candles, load_candles_from_csv
from bot.config.settings import load_settings
from bot.core.enums import OperatingMode
from bot.storage.database import SQLiteStorageEngine


def _safe_print(text: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squeeze-bot",
        description="Deriv Squeeze Intelligent Trade Management Engine",
    )
    parser.add_argument("--settings", default="config/settings.yaml", help="Path to settings.yaml")
    parser.add_argument("--symbols-cfg", default="config/symbols.yaml", help="Path to symbols.yaml")

    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run live/paper/analysis trading engine")
    run_p.add_argument(
        "--mode",
        choices=["analysis", "paper", "live"],
        default="analysis",
        help="Operating mode (default: analysis)",
    )
    run_p.add_argument("--max-seconds", type=float, default=None, help="Optional bounded runtime in seconds")
    run_p.add_argument("--no-dashboard", action="store_true", help="Disable terminal dashboard output")

    bt_p = sub.add_parser("backtest", help="Run 5-policy management comparison backtest")
    bt_p.add_argument("--symbols", default="R_100,R_75,R_50", help="Comma-separated symbols")
    bt_p.add_argument("--bars", type=int, default=1200, help="Synthetic bars per symbol if no CSV")
    bt_p.add_argument("--csv", default=None, help="Optional CSV OHLC path for single symbol")

    wf_p = sub.add_parser("walkforward", help="Run Train -> Validate -> Out-of-Sample walk-forward")
    wf_p.add_argument("--symbols", default="R_100,R_75", help="Comma-separated symbols")
    wf_p.add_argument("--bars", type=int, default=1800, help="Synthetic bars per symbol")
    wf_p.add_argument("--folds", type=int, default=4, help="Number of walk-forward folds")

    rep_p = sub.add_parser("report", help="Print performance & management-quality report from SQLite")
    rep_p.add_argument("--db", default=None, help="SQLite DB path override")

    return parser


def _run_backtest_cmd(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings, args.symbols_cfg)
    configure_logging(settings.logging)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    candles_by_sym = {}
    if args.csv:
        sym = syms[0]
        tf = settings.symbols[sym].primary_timeframe if sym in settings.symbols else 900
        candles_by_sym[sym] = load_candles_from_csv(args.csv, sym, timeframe_sec=tf)
    else:
        for idx, sym in enumerate(syms):
            tf = settings.symbols[sym].primary_timeframe if sym in settings.symbols else 900
            candles_by_sym[sym] = generate_synthetic_deriv_candles(
                sym, bars=args.bars, timeframe_sec=tf, seed=42 + idx * 17
            )

    runner = BacktestRunner(settings)
    report = runner.run_policy_comparison(candles_by_sym)

    _safe_print("\n=== DERIV SQUEEZE MANAGEMENT POLICY COMPARISON ===")
    header = f"{'Policy':<22} {'Trades':>6} {'Win%':>7} {'Exp(R)':>8} {'PF':>7} {'MaxDD%':>8} {'EndBal':>10}"
    _safe_print(header)
    _safe_print("-" * len(header))
    for pol_name, m in report["comparison"]["metrics"].items():
        end_bal = report["ending_balances"].get(pol_name, 0.0)
        _safe_print(
            f"{pol_name:<22} {m['total_trades']:>6d} {m['win_rate']*100:>6.1f}% "
            f"{m['expectancy_r']:>+8.3f} {m['profit_factor']:>7.2f} "
            f"{m['max_drawdown_pct']*100:>7.2f}% {end_bal:>10.2f}"
        )

    _safe_print("\n=== MANAGEMENT VERDICTS ===")
    _safe_print(json.dumps(report["comparison"]["verdicts"], indent=2))
    dyn_mq = report["comparison"]["management_quality"].get("DYNAMIC_MANAGEMENT", {})
    _safe_print("\n=== DYNAMIC MANAGEMENT QUALITY ===")
    _safe_print(json.dumps(dyn_mq.get("summary_answers", {}), indent=2))
    _safe_print(f"Quadrants: {json.dumps(dyn_mq.get('quadrant_counts', {}))}")
    _safe_print("\n=== POST-TRADE LEARNING INSIGHTS (PROPOSED ONLY) ===")
    _safe_print(json.dumps(report["learning"], indent=2))
    return 0


def _run_walkforward_cmd(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings, args.symbols_cfg)
    configure_logging(settings.logging)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    candles_by_sym = {
        sym: generate_synthetic_deriv_candles(
            sym,
            bars=args.bars,
            timeframe_sec=settings.symbols[sym].primary_timeframe if sym in settings.symbols else 900,
            seed=100 + idx * 13,
        )
        for idx, sym in enumerate(syms)
    }
    wf = WalkForwardValidator(settings).run(candles_by_sym, folds=args.folds)
    _safe_print("\n=== WALK-FORWARD VALIDATION REPORT ===")
    _safe_print(json.dumps(wf, indent=2))
    return 0


def _run_report_cmd(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings, args.symbols_cfg)
    db_path = args.db or settings.storage.sqlite_path
    if not Path(db_path).exists():
        _safe_print(f"Database not found at {db_path}")
        return 1
    storage = SQLiteStorageEngine(db_path)
    try:
        trades = storage.load_closed_trades()
        perf = compute_performance_metrics(trades, settings.risk.account_balance)
        mq = analyze_management_quality(trades)
        learn = PostTradeLearningEngine().analyze(trades, config_version=settings.config_version)
        _safe_print("\n=== TRADE JOURNAL PERFORMANCE ===")
        _safe_print(json.dumps(perf.to_dict(), indent=2))
        _safe_print("\n=== MANAGEMENT QUALITY ===")
        _safe_print(json.dumps(mq.to_dict(), indent=2))
        _safe_print("\n=== LEARNING INSIGHTS ===")
        _safe_print(json.dumps(learn, indent=2))
    finally:
        storage.close()
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "backtest":
        return _run_backtest_cmd(args)
    if args.command == "walkforward":
        return _run_walkforward_cmd(args)
    if args.command == "report":
        return _run_report_cmd(args)

    mode_map = {
        "analysis": OperatingMode.ANALYSIS_ONLY,
        "paper": OperatingMode.PAPER_TRADING,
        "live": OperatingMode.LIVE_TRADING,
    }
    settings = load_settings(
        args.settings,
        args.symbols_cfg,
        mode_override=mode_map[args.mode],
    )
    configure_logging(settings.logging)
    orch = EngineOrchestrator(settings, show_dashboard=not args.no_dashboard)
    orch.install_signal_handlers()
    asyncio.run(orch.start(max_seconds=args.max_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
