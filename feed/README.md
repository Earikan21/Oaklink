# feed/

`market.json` is written by the `market-feed` GitHub Action (`fundkit feed-dump`) every Saturday 05:40 UTC
and on demand (Actions → market-feed → Run workflow). It holds quotes, info, 2–5 years of daily history,
financial statements, estimates and earnings dates for VTI, BND, every held and watchlist name, the names in
`tickers.txt`, the first 30 names of the Graham screen, and the peer screens for each. The weekly run uses
it with `FUNDKIT_MARKET=fake FUNDKIT_FAKE_MARKET_JSON=feed/market.json`. `__meta__.generated` says how
fresh it is; `__meta__.errors` lists the tickers/methods Yahoo did not return (the run fails closed on those).
