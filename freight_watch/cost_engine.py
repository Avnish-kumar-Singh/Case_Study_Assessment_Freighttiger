"""Core numeric logic: weekly grouping, cost per tonne-km, and the two baselines."""
import pandas as pd

from .config import HISTORY_WEEKS


def load_weekly(shipments_csv: str) -> pd.DataFrame:
    """One row per (route, route_type, week_of) with cost_per_tonne_km = sum(cost) / sum(qty * km)."""
    d = pd.read_csv(shipments_csv, parse_dates=["shipment_date"])
    d["route"] = d["origin"] + "-" + d["destination"]
    # Monday-start week: subtract weekday() (Mon=0 ... Sun=6) days
    d["week_of"] = d["shipment_date"] - pd.to_timedelta(d["shipment_date"].dt.weekday, unit="D")
    d["tonne_km"] = d["quantity_tonnes"] * d["distance_km"]

    # Each route must map to exactly one route_type, otherwise grouping is ambiguous
    assert d.groupby("route")["route_type"].nunique().max() == 1, "route has >1 route_type"

    g = (d.groupby(["route", "route_type", "week_of"], as_index=False)
           .agg(freight_cost=("freight_cost_inr", "sum"), tonne_km=("tonne_km", "sum"),
                n_shipments=("shipment_id", "count")))
    g["cost_per_tonne_km"] = g["freight_cost"] / g["tonne_km"]
    return g.sort_values(["route", "week_of"]).reset_index(drop=True)


def add_baselines(g: pd.DataFrame) -> pd.DataFrame:
    """Adds own-history and similar-route baselines plus % differences.

    own history : mean of the route's previous <=8 weekly values (strictly before this week)
    similar     : mean of the other routes' values with the same route_type in the same week
    """
    g = g.copy()

    # Guard: the "previous N rows" logic equals "previous N weeks" only if there are no calendar gaps
    gaps = g.groupby("route")["week_of"].diff().dropna()
    assert (gaps == pd.Timedelta(days=7)).all(), "calendar gap in weekly series; rolling window would be wrong"

    g["own_baseline"] = g.groupby("route")["cost_per_tonne_km"].transform(
        lambda s: s.shift(1).rolling(HISTORY_WEEKS, min_periods=1).mean())
    g["prior_weeks_used"] = g.groupby("route").cumcount().clip(upper=HISTORY_WEEKS)

    grp = g.groupby(["route_type", "week_of"])["cost_per_tonne_km"]
    n, total = grp.transform("count"), grp.transform("sum")
    g["peer_baseline"] = (total - g["cost_per_tonne_km"]) / (n - 1)   # excludes the route itself

    g["vs_own_pct"] = (g["cost_per_tonne_km"] / g["own_baseline"] - 1) * 100
    g["vs_peer_pct"] = (g["cost_per_tonne_km"] / g["peer_baseline"] - 1) * 100
    return g
