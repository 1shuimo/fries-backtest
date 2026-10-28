#!/usr/bin/env python3
"""Read-only Futu data and local backtest tools exposed over MCP stdio."""

from __future__ import annotations

import os
import socket
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from fetch_data import fetch_futu, write_rows
from qbacktest import Config, load_bars, run_backtest, summarize, write_outputs


ROOT = Path(__file__).resolve().parent
mcp = FastMCP(
    "Futu Backtest",
    instructions=(
        "Read historical bars from a locally running FutuOpenD, save normalized CSV files, "
        "and run the local Qullamaggie-style daily backtest. No account or trading tools exist."
    ),
)


def workspace_path(value: str, default_parent: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / default_parent / candidate
    candidate = candidate.resolve()
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError(f"路径必须位于工作区 {ROOT}")
    return candidate


@mcp.tool()
def check_futu_opend() -> dict[str, object]:
    """Check whether the local FutuOpenD TCP gateway is reachable."""
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return {"connected": True, "host": host, "port": port}
    except OSError as exc:
        return {"connected": False, "host": host, "port": port, "error": str(exc)}


@mcp.tool()
def download_futu_daily(symbols: list[str], start: str, end: str, output: str = "futu_daily.csv") -> dict[str, object]:
    """Download QFQ daily bars from FutuOpenD into a normalized workspace CSV.

    Symbols use Futu codes such as US.AAPL, SH.600000, or SZ.000001. Each distinct
    symbol consumes Futu historical-candlestick quota once per rolling seven days.
    """
    if not symbols or len(symbols) > 1000:
        raise ValueError("symbols 数量必须为 1..1000")
    target = workspace_path(output, "data")
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    rows = fetch_futu(symbols, start, end, host, port)
    write_rows(rows, str(target))
    return {"output": str(target), "rows": len(rows), "symbols": len(set(symbols)), "adjustment": "QFQ"}


@mcp.tool()
def run_daily_backtest(
    input_csv: str,
    strategy: str = "both",
    output_dir: str = "futu_backtest",
    initial_cash: float = 100_000.0,
    risk_fraction: float = 0.005,
    max_position_fraction: float = 0.20,
    cost_bps: float = 10.0,
    exit_ma: int = 10,
) -> dict[str, object]:
    """Run the local daily Breakout/EP portfolio backtest and persist its reports."""
    if strategy not in {"breakout", "ep", "both"}:
        raise ValueError("strategy 必须是 breakout、ep 或 both")
    if exit_ma not in {10, 20}:
        raise ValueError("exit_ma 必须是 10 或 20")
    source = workspace_path(input_csv, "data")
    target = workspace_path(output_dir, "results")
    cfg = Config(
        initial_cash=initial_cash,
        risk_fraction=risk_fraction,
        max_position_fraction=max_position_fraction,
        cost_bps=cost_bps,
        exit_ma=exit_ma,
    )
    grouped = load_bars(str(source))
    trades, curve = run_backtest(grouped, strategy, cfg)
    summary = summarize(trades, curve, cfg)
    write_outputs(str(target), trades, curve, summary)
    return {
        "summary": summary,
        "output_dir": str(target),
        "reports": [str(target / name) for name in ("summary.json", "trades.csv", "equity.csv")],
        "parameters": asdict(cfg),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
