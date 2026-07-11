#!/usr/bin/env python3
"""
NSE Turnaround Stock Screener
-----------------------------
Screens NSE (India) stocks to find companies that match a specific quarterly turnaround pattern:
1. Q-3 (4th oldest quarter): Profitable (> 0)
2. Q-2 (3rd oldest quarter): Profitable (> 0)
3. Q-1 (2nd oldest quarter / second latest): Loss-making (<= 0)  <-- The Dip/Product Trial
4. Q0  (Latest quarter): Profitable (> 0)                       <-- The Recovery

This implies the company has a strong baseline of profitability, experienced a temporary setback 
(e.g., failed product launch or investment write-down) in the second-to-last quarter, 
and has successfully returned to profitability in the latest quarter.

Author: Antigravity AI Coding Assistant
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from pytz import timezone

# Configure Logging (Set level to WARNING to avoid log messages cluttering the printed stock list)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("turnaround_screener")

# ANSI Color Codes for beautiful terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Lock for thread-safe console printing and spreadsheet updating
print_lock = threading.Lock()
sheet_lock = threading.Lock()

def get_nse_symbols():
    """Fetches the list of active equity symbols from NSE India."""
    url = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
    try:
        df = pd.read_csv(url)
        # Clean columns and extract symbols
        df.columns = [col.strip() for col in df.columns]
        symbols = df['SYMBOL'].dropna().str.strip().tolist()
        # Append .NS suffix for Yahoo Finance
        yf_symbols = [f"{s}.NS" for s in symbols]
        return yf_symbols
    except Exception as e:
        # Fallback list of major NSE tickers in case of network issues
        return [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
            "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "LTIM.NS"
        ]

def check_turnaround_pattern(net_incomes):
    """
    Checks if the last 4 quarters follow the turnaround pattern:
    Q-3 > 0, Q-2 > 0, Q-1 <= 0, Q0 > 0
    
    Parameters:
        net_incomes (pd.Series): Net income sorted chronologically (ascending index).
        
    Returns:
        tuple: (is_match, reason, q3_val, q2_val, q1_val, q0_val)
    """
    if len(net_incomes) < 4:
        return False, f"Insufficient quarters (found {len(net_incomes)}, need 4)", None, None, None, None
        
    # Get last 4 values
    q3 = net_incomes.iloc[-4]
    q2 = net_incomes.iloc[-3]
    q1 = net_incomes.iloc[-2]
    q0 = net_incomes.iloc[-1]
    
    # Check for NaN values
    if pd.isna(q3) or pd.isna(q2) or pd.isna(q1) or pd.isna(q0):
        return False, "NaN values present in recent quarters", None, None, None, None
        
    # Apply logic check with specific reasons for skip
    if q3 <= 0:
        return False, f"Q-3 not profitable ({q3})", q3, q2, q1, q0
    if q2 <= 0:
        return False, f"Q-2 not profitable ({q2})", q3, q2, q1, q0
    if q1 > 0:
        return False, f"Q-1 profitable ({q1}), expected loss", q3, q2, q1, q0
    if q0 <= 0:
        return False, f"Q0 loss-making ({q0}), expected recovery", q3, q2, q1, q0
        
    return True, "Match", q3, q2, q1, q0

def process_symbol(symbol):
    """
    Fetches financial statements and checks the turnaround strategy for a single symbol.
    """
    try:
        ticker = yf.Ticker(symbol)
        
        # 1. Fetch quarterly income statement
        income_stmt = ticker.quarterly_income_stmt
        if income_stmt is None or income_stmt.empty:
            with print_lock:
                print(f"{YELLOW}[SKIP]        {symbol:<12} - No quarterly statements found{RESET}")
            return None
            
        # Ensure 'Net Income' row exists
        if 'Net Income' not in income_stmt.index:
            with print_lock:
                print(f"{YELLOW}[SKIP]        {symbol:<12} - Net Income row not found in statement{RESET}")
            return None
            
        # Extract net income row and sort chronologically (ascending dates)
        net_incomes = income_stmt.loc['Net Income'].sort_index(ascending=True)
        
        # 2. Check turnaround strategy criteria
        is_match, reason, q3, q2, q1, q0 = check_turnaround_pattern(net_incomes)
        
        # Lambda to convert values to Crores (1 Crore = 10,000,000 INR)
        to_crores = lambda x: round(x / 10_000_000, 2) if isinstance(x, (int, float)) else x
        
        if not is_match:
            with print_lock:
                # Format explanation using Crores for readable print
                if q1 is not None and q0 is not None:
                    explanation = f"Pattern mismatch (Q-3: {to_crores(q3)} Cr, Q-2: {to_crores(q2)} Cr, Q-1: {to_crores(q1)} Cr, Q0: {to_crores(q0)} Cr)"
                else:
                    explanation = reason
                print(f"{RESET}[SKIP]        {symbol:<12} - {explanation}{RESET}")
            return None
            
        # 3. Gather supplementary metrics if it's a match
        company_name = symbol
        pe = 'N/A'
        peg = 'N/A'
        quick_ratio = 'N/A'
        current_price = 'N/A'
        
        # Fetch info with error isolation so that slow/failing info calls do not drop the match
        try:
            info = ticker.info
            if info:
                company_name = info.get('longName', symbol)
                pe = info.get('trailingPE', 'N/A')
                peg = info.get('pegRatio', 'N/A')
                quick_ratio = info.get('quickRatio', 'N/A')
                current_price = info.get('regularMarketPrice', 'N/A')
                # Fallback check for regularMarketPrice in other fields
                if current_price == 'N/A':
                    current_price = info.get('currentPrice', 'N/A')
        except Exception as e:
            # We still keep the match, just with default metric values
            logger.debug(f"Info call failed for {symbol}: {e}")
            
        # Latest Quarter End Date
        latest_date = net_incomes.index[-1]
        date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else str(latest_date)
        
        match_data = {
            'Symbol': symbol,
            'Company Name': company_name,
            'Latest Quarter End': date_str,
            'Q-3 Net Income (Cr)': to_crores(q3),
            'Q-2 Net Income (Cr)': to_crores(q2),
            'Q-1 Net Income (Cr)': to_crores(q1),
            'Q0 Net Income (Cr)': to_crores(q0),
            'P/E Ratio': pe,
            'PEG Ratio': peg,
            'Quick Ratio': quick_ratio,
            'Current Price': current_price
        }
        
        with print_lock:
            print(f"{GREEN}{BOLD}[MATCH FOUND] {symbol:<12} - {company_name} | Q-1 Net: {to_crores(q1)} Cr -> Q0 Net: {to_crores(q0)} Cr{RESET}")
            
        return match_data
        
    except Exception as e:
        with print_lock:
            print(f"{RED}[ERROR]       {symbol:<12} - Exception: {str(e)}{RESET}")
        return None

def write_to_google_sheet(sheet_id, data_rows, credentials_path):
    """Writes the matched results to a Google Sheet."""
    headers = [
        'Symbol', 'Company Name', 'Latest Quarter End', 
        'Q-3 Net Income (Cr)', 'Q-2 Net Income (Cr)', 'Q-1 Net Income (Cr)', 'Q0 Net Income (Cr)',
        'P/E Ratio', 'PEG Ratio', 'Quick Ratio', 'Current Price'
    ]
    
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_id(sheet_id).sheet1
        
        # Retrieve or initialize headers
        sheet.clear()
        sheet.append_row(headers)
        
        # Prepare rows
        rows_to_append = []
        for d in data_rows:
            rows_to_append.append([
                d['Symbol'], d['Company Name'], d['Latest Quarter End'],
                d['Q-3 Net Income (Cr)'], d['Q-2 Net Income (Cr)'], d['Q-1 Net Income (Cr)'], d['Q0 Net Income (Cr)'],
                d['P/E Ratio'], d['PEG Ratio'], d['Quick Ratio'], d['Current Price']
            ])
            
        if rows_to_append:
            sheet.append_rows(rows_to_append)
        
        print(f"\n{GREEN}{BOLD}Successfully wrote {len(data_rows)} matches to Google Sheet.{RESET}")
        return True
    except Exception as e:
        print(f"\n{RED}Google Sheets integration failed: {e}{RESET}")
        return False

def main():
    parser = argparse.ArgumentParser(description="NSE Turnaround Stock Screener")
    parser.add_argument("--limit", type=int, default=None, help="Number of symbols to scan (default: None for all stocks)")
    parser.add_argument("--threads", type=int, default=15, help="Number of concurrent execution threads (default: 15)")
    parser.add_argument("--sheet-id", type=str, default="1PlZ1GS5mYoGwwdSNuhR0bO8mvmt7zQQWihbCaNj32V4", help="Google Sheet ID")
    parser.add_argument("--creds", type=str, default="credentials.json", help="Path to Google credentials.json file")
    parser.add_argument("--output-csv", type=str, default="nse_turnaround_matches.csv", help="Fallback local CSV name")
    
    args = parser.parse_args()
    
    start_time = time.time()
    
    # 1. Fetch NSE Symbols
    all_symbols = get_nse_symbols()
    
    if args.limit is not None and args.limit > 0:
        scan_list = all_symbols[:args.limit]
    else:
        scan_list = all_symbols
        
    print(f"{CYAN}{BOLD}========================================================================{RESET}")
    print(f"{CYAN}{BOLD}Starting analysis on {len(scan_list)} NSE symbols using {args.threads} threads...{RESET}")
    print(f"{CYAN}{BOLD}========================================================================{RESET}\n")
    
    matched_results = []
    processed_count = 0
    
    # 2. Parallel stock processing
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        future_to_symbol = {executor.submit(process_symbol, sym): sym for sym in scan_list}
        
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            processed_count += 1
            
            try:
                res = future.result()
                if res:
                    matched_results.append(res)
            except Exception as exc:
                logger.debug(f"{symbol} generated an exception during processing: {exc}")
                
    duration = time.time() - start_time
    print(f"\n{CYAN}{BOLD}========================================================================{RESET}")
    print(f"{CYAN}{BOLD}Scanning completed in {duration:.2f} seconds. Found {len(matched_results)} matching stocks.{RESET}")
    print(f"{CYAN}{BOLD}========================================================================{RESET}\n")
    
    # 3. Output handling
    if not matched_results:
        print(f"{YELLOW}No stocks matched the recovery criteria in this scan.{RESET}")
        return
        
    # Write to local CSV always as a backup
    df_results = pd.DataFrame(matched_results)
    df_results.to_csv(args.output_csv, index=False)
    print(f"Saved local copy of matches to {CYAN}{args.output_csv}{RESET}")
    
    # Attempt Google Sheets write
    if os.path.exists(args.creds):
        print("Credentials file found. Exporting to Google Sheets...")
        success = write_to_google_sheet(args.sheet_id, matched_results, args.creds)
        if not success:
            print(f"{YELLOW}Google Sheet update was unsuccessful. Local CSV contains all matches.{RESET}")
    else:
        print(f"{YELLOW}Google Sheet credentials file '{args.creds}' not found. Skipping export.{RESET}")
        print("To enable Google Sheets export, place your 'credentials.json' file in the directory.")

if __name__ == "__main__":
    main()

