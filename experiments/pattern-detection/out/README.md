# Generated results

Everything here is reproducible with `scripts/run_all.py` plus the VCP
scripts named in each document. The large intermediate JSON (signal dumps,
per-trade records) and the sample PNGs are gitignored — regenerate them with
`scripts/scan_real.py`, `scripts/vcp_entry_policies.py` and
`scripts/plot_samples.py`.

| File | Produced by |
|---|---|
| `FINAL_strict_report.txt`, `FINAL_relaxed_report.txt` | `run_synthetic_eval.py` |
| `public_named_cases.txt` | `run_public_eval.py` |
| `real_*_summary.txt`, `real_*_analysis_*.txt` | `scan_real.py`, `analyse_signals.py` |
| `vcp_pre_report.txt`, `vcp_entry_report.txt` | `vcp_pre_breakout.py`, `vcp_entry_policies.py` |
| `vcp_ema_exit_report.txt`, `vcp_exit_grid_report.txt` | `vcp_ema_exit.py`, `vcp_exit_grid.py` |
| `vcp_final_report.txt` | `vcp_final.py` |
| `vcp_ranking_report.txt`, `vcp_regime_report.txt` | `vcp_ranking.py`, `vcp_regime.py` |
| `vcp_losses_report.txt` | `vcp_losses.py` |
| `vcp_dashboard*.html` | `vcp_backtest.py` |

Dashboard variants:

| File | Rules |
|---|---|
| `vcp_dashboard.html` | **adopted** — 3+ contractions, stop ≥0.5 ATR, ₹1L a trade |
| `vcp_dashboard_3T_150k.html` | the same at ₹1.5L a trade (best balance) |
| `vcp_dashboard_allT.html` | stop gate only, all contraction counts |
| `vcp_dashboard_nofilter.html` | neither gate |
| `vcp_dashboard_compound.html` | bet sized at equity/10 instead of a fixed amount |
