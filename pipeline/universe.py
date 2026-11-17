"""Curated ETF menu shown on the site. Liquid, long-lived funds across asset classes.

Funds with short histories are allowed, but choosing one moves the common start date forward;
the site shows each fund's first date so the trade-off is visible.

Tickers are Yahoo Finance symbols. Each exchange maps to the currency its prices are quoted in;
non-USD prices are converted to US dollars in the database (see pipeline.refresh).
"""

EXCHANGES = {"US": "USD", "TASE": "ILS"}  # Tel Aviv prices are quoted in agorot; units cancel out in returns

UNIVERSE = [
    # ticker, name, category (US-listed unless noted)
    ("SPY", "S&P 500", "US equity"),
    ("QQQ", "Nasdaq-100", "US equity"),
    ("IWM", "Russell 2000 small caps", "US equity"),
    ("DIA", "Dow Jones Industrial Average", "US equity"),
    ("MDY", "S&P MidCap 400", "US equity"),
    ("VTI", "US total stock market", "US equity"),
    ("RSP", "S&P 500 equal weight", "US equity"),
    ("IWD", "Russell 1000 value", "US equity factors"),
    ("IWF", "Russell 1000 growth", "US equity factors"),
    ("USMV", "US minimum volatility", "US equity factors"),
    ("MTUM", "US momentum", "US equity factors"),
    ("QUAL", "US quality", "US equity factors"),
    ("XLK", "Technology", "US sectors"),
    ("XLF", "Financials", "US sectors"),
    ("XLE", "Energy", "US sectors"),
    ("XLV", "Health care", "US sectors"),
    ("XLY", "Consumer discretionary", "US sectors"),
    ("XLP", "Consumer staples", "US sectors"),
    ("XLI", "Industrials", "US sectors"),
    ("XLU", "Utilities", "US sectors"),
    ("XLB", "Materials", "US sectors"),
    ("EFA", "Developed markets ex-US", "International equity"),
    ("EEM", "Emerging markets", "International equity"),
    ("VGK", "Europe", "International equity"),
    ("EWJ", "Japan", "International equity"),
    ("FXI", "China large caps", "International equity"),
    ("EWZ", "Brazil", "International equity"),
    ("INDA", "India", "International equity"),
    ("SHY", "1-3 year Treasuries", "Bonds"),
    ("IEF", "7-10 year Treasuries", "Bonds"),
    ("TLT", "20+ year Treasuries", "Bonds"),
    ("TIP", "Inflation-protected Treasuries", "Bonds"),
    ("AGG", "US aggregate bonds", "Bonds"),
    ("LQD", "Investment-grade corporates", "Bonds"),
    ("HYG", "High-yield corporates", "Bonds"),
    ("MUB", "Municipal bonds", "Bonds"),
    ("EMB", "Emerging-market USD bonds", "Bonds"),
    ("BNDX", "International bonds (USD hedged)", "Bonds"),
    ("GLD", "Gold", "Real assets"),
    ("SLV", "Silver", "Real assets"),
    ("DBC", "Broad commodities", "Real assets"),
    ("VNQ", "US real estate (REITs)", "Real assets"),
    ("VNQI", "International real estate", "Real assets"),
]

# Tel Aviv Stock Exchange funds. Yahoo's history for TASE funds starts in August 2019, whatever the
# fund's real inception date. One fund per exposure, chosen for the most complete Yahoo history.
TASE = [
    ("KSM-F74.TA", "TA-125 (KSM)", "Israel (TASE)"),
    ("MTF-F16.TA", "TA-35 (MTF)", "Israel (TASE)"),
    ("HRL-F11.TA", "TA-90 mid caps (Harel)", "Israel (TASE)"),
    ("HRL-F15.TA", "TA real estate (Harel)", "Israel (TASE)"),
    ("HRL-F300.TA", "Tel Bond-Shekel 50 corporates (Harel)", "Israel (TASE)"),
    ("HRL-F303.TA", "Tel Bond banks & insurance (Harel)", "Israel (TASE)"),
    ("HRL-F109.TA", "Tel Bond high-yield shekel (Harel)", "Israel (TASE)"),
    ("TCH-F25.TA", "CPI-linked government bonds 2-5y (Tachlit)", "Israel (TASE)"),
]


def exchange_of(ticker):
    return "TASE" if ticker.endswith(".TA") else "US"


ALL_ASSETS = [(t, n, c, exchange_of(t)) for t, n, c in UNIVERSE + TASE]

FX = {"ILS": "RER_USD_ILS"}  # Bank of Israel representative rate, shekels per US dollar
RISK_FREE = "DTB3"  # FRED: 3-month Treasury bill secondary market rate, percent (US government data)
REQUIRED = ["SPY", "IEF"]  # used by the fixed benchmarks
