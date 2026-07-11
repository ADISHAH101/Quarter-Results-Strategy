# NSE Turnaround Stock Screener

This project implements a stock screener for the National Stock Exchange (NSE) of India that identifies potential turnaround companies using quarterly financial statements.

## The Strategy: Single-Quarter Deviation & Recovery

The screener targets a specific, high-probability pattern in the company's last 4 quarters of Net Income (from oldest to newest: $Q_{-3}, Q_{-2}, Q_{-1}, Q_0$):

1. **$Q_{-3}$ (4th oldest quarter)**: Profitable ($> 0$)
2. **$Q_{-2}$ (3rd oldest quarter)**: Profitable ($> 0$)
3. **$Q_{-1}$ (2nd oldest quarter / second latest)**: Loss-making ($\le 0$) — **The Trial/Setback**
4. **$Q_0$ (Latest quarter)**: Profitable ($> 0$) — **The Recovery**

### Strategic Rationale
This pattern represents a strong company that:
- Has a baseline track record of profitability ($Q_{-3}$ and $Q_{-2}$).
- Suffered a transient, one-quarter loss ($Q_{-1}$). This could be due to testing a new product line that failed and was quickly closed, an aggressive R&D write-off, or a one-time adjustment.
- Promptly corrected course and returned to profit in the latest quarter ($Q_0$).
- May have been temporarily devalued by the market due to the single-quarter loss, presenting an attractive entry point as it gets "back on track".

---

## Features
- **High Performance**: Uses Python's `ThreadPoolExecutor` to fetch and analyze stock financial statements in parallel (speeding up scanning time by over 10x).
- **Graceful Failures**: Isolates slow or failing ticker description calls (`ticker.info`) so that technical API issues do not cause matches to be skipped.
- **Crores Conversion**: Automatically scales the Net Income values from raw Rupees to **Crores INR** (1 Crore = 10,000,000 INR) to align with standard Indian financial reporting.
- **Dual-output System**: 
  - Saves matches to a local CSV (`nse_turnaround_matches.csv`) immediately.
  - Automatically exports results to a Google Sheet if `credentials.json` is present.

---

## Requirements

Install the dependencies:
```bash
pip install pandas yfinance gspread google-auth pytz
```

---

## Usage

1. Run the script with default settings (scans the first 150 NSE tickers with 10 threads):
   ```bash
   python nse_turnaround_screener.py
   ```

2. Scan more tickers and adjust execution parameters:
   ```bash
   python nse_turnaround_screener.py --limit 500 --threads 15
   ```

3. Specify a custom Google Sheet ID and credentials file:
   ```bash
   python nse_turnaround_screener.py --sheet-id "your-google-sheet-id" --creds "path/to/credentials.json"
   ```

### Command Line Arguments
* `--limit`: Number of NSE symbols to scan (default: 150).
* `--threads`: Number of concurrent threads to use (default: 10).
* `--sheet-id`: The Google Sheet ID to update (default: `1PlZ1GS5mYoGwwdSNuhR0bO8mvmt7zQQWihbCaNj32V4`).
* `--creds`: Path to Google Service Account JSON credentials (default: `credentials.json`).
* `--output-csv`: Name of the fallback CSV file (default: `nse_turnaround_matches.csv`).
