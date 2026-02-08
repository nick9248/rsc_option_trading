# Testing and Verification Methodology

**MANDATORY for all new strategy implementations and significant code changes.**

We built a modular, scalable system where every component can be independently tested. When implementing new strategies or features, you MUST verify correctness by fetching real data and manually calculating expected values.

## Step-by-Step Verification Process

1. **Fetch Real API Data**: Use existing services to get live market data
   ```python
   from coding.service.deribit.deribit_api_service import DeribitAPIService

   api = DeribitAPIService()

   # Get ticker data
   ticker = api.get_ticker('ETH-27MAR26-3400-C')

   # Get book summary
   book_summary = api.get_book_summary_by_currency('ETH')

   # Get on-chain analysis
   from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
   analyzer = OnChainAnalyzer(book_summary)
   analysis = analyzer.calculate_all_metrics()
   ```

2. **Manual Calculation**: Calculate expected values step-by-step
   - Extract relevant data points (strikes, IVs, prices, greeks)
   - Apply the algorithm manually (e.g., profit/debit ratio calculation)
   - Document your manual calculations with comments
   - Compare manual results to implementation output

3. **Audit as an External Reviewer**: Check the code as if you didn't write it
   - Does the strike selection make financial sense?
   - Are the calculations mathematically correct?
   - Are edge cases handled (illiquid options, extreme strikes)?
   - Does it use existing verified services correctly?

4. **Test with Multiple Scenarios**:
   - Different currencies (BTC, ETH)
   - Different expirations (short-term, long-term)
   - Different market conditions (liquid vs illiquid)
   - Edge cases (very OTM strikes, near expiration)

5. **Document Findings**: List potential problems and bugs
   - What could go wrong?
   - What assumptions are made?
   - What needs improvement?
   - Fix issues before committing

## Example: Verifying Bull Call Spread Strike Selection

```python
# 1. Fetch real data
api = DeribitAPIService()
book_summary = api.get_book_summary_by_currency('ETH')
underlying_price = 2906.50  # From perpetual ticker

# 2. Filter call options for expiration
calls = {k: v for k, v in book_summary.items()
         if 'C' in k and '27MAR26' in k}

# 3. Manual calculation for one spread
long_strike = 3400
short_strike = 3700
long_call = calls['ETH-27MAR26-3400-C']
short_call = calls['ETH-27MAR26-3700-C']

long_cost = long_call['ask_price'] or long_call['mark_price']
short_credit = short_call['bid_price'] or short_call['mark_price']
net_debit = long_cost - short_credit
strike_width = short_strike - long_strike
max_profit = strike_width - net_debit
profit_debit_ratio = max_profit / net_debit

print(f"Manual calculation:")
print(f"  Long {long_strike}: ${long_cost:.2f}")
print(f"  Short {short_strike}: ${short_credit:.2f}")
print(f"  Net debit: ${net_debit:.2f}")
print(f"  Max profit: ${max_profit:.2f}")
print(f"  Profit/debit ratio: {profit_debit_ratio:.2f}")

# 4. Compare to implementation
from coding.core.strategy import create_strategy
strategy = create_strategy("Bull Call Spread", "ETH", "27MAR26", underlying_price)
strategy.build_legs(ticker_data=book_summary, spread_config=config)

implementation_debit = abs(strategy.get_total_cost())
implementation_profit = strategy.get_max_profit()
implementation_ratio = implementation_profit / implementation_debit

# 5. Verify match
assert abs(implementation_debit - net_debit) < 0.01, "Debit mismatch!"
assert abs(implementation_ratio - profit_debit_ratio) < 0.01, "Ratio mismatch!"
```

## Common Issues to Check

1. **Ask/Bid Price = 0**: This is CORRECT for illiquid options
   - Fallback to mark_price is expected behavior
   - Same pattern used in Long Call/Long Put strategies
   - Not a bug - it's proper handling of market reality

2. **Strike Selection**: Verify strikes are realistic
   - Not too far OTM (delta too low)
   - Not "lottery tickets" (extremely low probability)
   - Within reasonable range of underlying price

3. **Greek Aggregation**: For multi-leg strategies
   - Net greeks = sum(leg.greeks × leg.quantity)
   - Negative quantity for sold legs
   - Verify sign conventions

4. **Cost Calculation**:
   - Long legs: use ask_price (what you pay to buy)
   - Short legs: use bid_price (what you receive to sell)
   - Net cost = sum of all leg costs (credits are negative)

## When to Skip This Process

Only skip verification for:
- Trivial changes (typo fixes, comments, logging)
- Pure refactoring with 100% test coverage
- Changes to GUI only (no business logic)

For ALL strategy implementations and financial calculations, this verification is MANDATORY.
