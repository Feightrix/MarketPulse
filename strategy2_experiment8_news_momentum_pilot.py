import strategy2_experiment8_news_momentum_long as base
import strategy2_experiment8_news_momentum_runner as fast

# Data-scope reduction only. All strategy thresholds, sizing, exits, costs, and gates remain unchanged.
base.UNIVERSE = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "NFLX", "GOOGL"]
base.collect_events = fast.collect_events_fast

if __name__ == "__main__":
    base.main()
