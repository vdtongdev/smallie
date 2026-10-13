"""The 200 popular stocks loaded into the `market` schema, grouped by sector for readability.

Tickers use Yahoo Finance symbols (e.g. BRK-B). Acquired or delisted companies make
Yahoo return no data; the loader reports them so they can be replaced here.
"""

_GROUPS = {
    "technology": (
        "AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO ORCL ADBE CRM AMD INTC CSCO QCOM TXN IBM NOW "
        "INTU AMAT MU LRCX KLAC ADI SNPS CDNS PANW CRWD FTNT ANET MRVL NXPI ADSK WDAY SNOW PLTR "
        "SHOP UBER ABNB DDOG NET DELL HPQ HPE SMCI TSM ASML ARM ZS MDB TEAM SONY MSTR"
    ),
    "communication": "NFLX DIS CMCSA T VZ TMUS CHTR SPOT RBLX TTWO PINS SNAP ROKU",
    "consumer": (
        "WMT COST HD LOW TGT MCD SBUX NKE KO PEP PG PM MO CL MDLZ KHC HSY LULU CMG YUM BKNG MAR "
        "HLT TJX ORLY AZO F GM RIVN NIO TM EBAY DG KR BABA PDD DKNG CCL RCL GME"
    ),
    "financials": (
        "JPM BAC WFC C GS MS SCHW BLK AXP V MA PYPL COF USB PNC SPGI MCO ICE CME CB PGR AIG MET "
        "BRK-B KKR BX HOOD COIN SOFI XYZ"
    ),
    "healthcare": (
        "UNH JNJ LLY PFE MRK ABBV TMO ABT DHR BMY AMGN GILD CVS MDT ISRG SYK BSX VRTX REGN ZTS "
        "CI ELV HCA MRNA NVO"
    ),
    "industrials": "BA CAT DE GE HON LMT RTX NOC GD UPS FDX UNP CSX MMM ETN WM DAL UAL",
    "energy": "XOM CVX COP SLB EOG OXY PSX MPC KMI",
    "materials": "LIN SHW FCX NEM NUE",
    "utilities": "NEE DUK SO",
    "real_estate": "AMT PLD EQIX O",
}

TICKERS: list[str] = [t for group in _GROUPS.values() for t in group.split()]
