# Strategy and Backtesting

**Status:** Framework specified; baseline parameters require research<br>
**Last updated:** 2026-08-03

## Strategy responsibility

The strategy engine determines whether an AI-qualified candidate has a reproducible trade setup. It produces a setup state, entry trigger/zone, stop or invalidation, exit rules, expected reward-to-risk, and a machine-readable explanation.

Strategies operate on market data and approved candidate metadata. They do not fetch news, call an LLM, access future records, or place orders.

## Python strategy protocol

Every trusted strategy plugin defines:

- Stable ID, semantic version, and description.
- Validated parameter schema and defaults.
- Required bar intervals and fields.
- Warm-up length.
- Eligibility/liquidity rules.
- Feature calculation.
- Setup-state calculation.
- Entry-order intent.
- Stop/invalidation rule.
- Profit-taking, trailing, time, and event-risk exits.
- Position-sizing intent expressed as risk constraints rather than brokerage actions.
- Explanation fields suitable for the dashboard.

Strategy plugins are registered explicitly from version-controlled source. The dashboard cannot upload or execute arbitrary Python.

## Initial research families

The framework should support at least two simple baselines before combining rules:

### Trend pullback

- Establish a daily uptrend using moving-average structure and positive SPY-relative strength.
- Detect a controlled retracement toward a configured daily trend reference.
- Confirm recovery using a completed daily or intraday bar and volume/volatility criteria.
- Invalidate below a recent structural low or ATR-based boundary.
- Exit by reward multiple, trailing stop, trend failure, or maximum holding period.

### Breakout

- Establish trend and minimum liquidity.
- Define a prior range/resistance window without future data.
- Require a completed breakout bar and configured volume confirmation.
- Invalidate on failed breakout or ATR/structural stop.
- Exit by reward multiple, trailing stop, trend failure, or time limit.

Parameters are hypotheses. No default is labeled profitable until it passes the acceptance process.

## Intraday behavior

- Daily bars establish the primary regime and trend.
- Completed 15-minute bars are the leading candidate for intraday confirmation.
- Never calculate a confirmed signal from an unfinished bar.
- Stream or poll only the active shortlist at higher frequency.
- Maintain alert states: `forming`, `confirmed`, `invalidated`, `expired`, and `closed`.
- If price data becomes stale, preserve the last state and block new confirmations.
- Store entry zones rather than claiming false cent-level precision.

## Backtest simulation

Use an event-driven simulated clock and the same strategy implementation used by current research. A baseline portfolio simulation uses configurable starting capital, long-only positions, no leverage, fractional shares, and explicit costs.

For each simulated decision:

1. Construct the eligible universe known at that timestamp.
2. Expose only bars and metadata already available.
3. Calculate features and setup state.
4. Submit simulated intent after the signal timestamp.
5. Fill using the next permitted bar price plus modeled slippage.
6. Apply stops and exits using documented bar-resolution assumptions.
7. Record every order, fill, position change, cost, and rejection.

Where daily signals are used, a close-based signal cannot fill at that same close. For intraday rules, the trigger and fill sequencing must state how gaps through stops/targets are handled.

## Required metrics

Win rate is reported but never used alone. At minimum calculate:

- Expectancy: `(win rate × average win) − (loss rate × average loss) − average costs`.
- Total return and CAGR.
- Average win, average loss, and payoff ratio.
- Profit factor.
- Maximum drawdown and recovery time.
- Annualized volatility and Sharpe ratio with the risk-free assumption shown.
- Exposure, turnover, trade count, and holding time.
- Performance by year, sector, setup type, and market regime.
- SPY buy-and-hold comparison over the identical period.

## Validation process

- Maintain separate development, validation, and final out-of-sample periods.
- Use rolling or expanding walk-forward tests.
- Include slippage and transaction costs.
- Test nearby parameter values; reject isolated optimums surrounded by poor results.
- Report results across bullish, bearish, high-volatility, and sideways regimes.
- Require a meaningful sample size before interpreting a win rate.
- Control multiple-testing/data-mining risk and preserve every experiment, not only winners.
- Test on a point-in-time universe where possible; label current-constituent tests as survivorship-biased.
- Do not include AI decisions in historical claims unless the exact historical assessment was captured then.

## Publication gate

A strategy may appear in the current-opportunity dashboard only after:

- Its implementation and parameters are versioned.
- Unit and look-ahead tests pass.
- The defined out-of-sample and stability checks pass.
- Costs and execution assumptions are documented.
- Maximum observed drawdown and failure regimes are displayed.

Even then, a strategy is a research result, not a guarantee of future performance.
