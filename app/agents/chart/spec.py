"""The chart format the UI draws (`renderChart` in app/static/app.js, with Chart.js).

Kept small on purpose: a few chart types, labelled x values and up to 8 numeric series, so
specs are easy to validate here and to render there. The model never writes one by hand for
database data: `rows_to_spec` (tools.py) builds it from a query result.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

ChartType = Literal["line", "bar", "area", "pie", "scatter"]
# The UI colours series with 8 fixed palette slots (never cycled), hence the caps below; past
# the first 3 slots, colours that may all sit next to each other (scatter dots) get confusable.
MAX_SERIES = 8
MAX_SCATTER_SERIES = 3
MAX_POINTS = 500  # more are downsampled; a chart in a chat bubble can't show them anyway
MAX_PIE_SLICES = 8  # more: the 7 largest plus "Other"
OTHER = "Other"


class Series(BaseModel):
    name: str
    values: list[float | None]


class ChartSpec(BaseModel):
    id: str = Field(default_factory=lambda: f"c{uuid.uuid4().hex[:8]}")
    type: ChartType
    title: str
    x_label: str = ""
    y_label: str = ""
    labels: list[str | float]
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)
    downsampled_from: int | None = None  # number of points before downsampling, if any
    table_id: str | None = None  # the query result it was drawn from, if any

    @model_validator(mode="after")
    def _check(self) -> "ChartSpec":
        n = len(self.labels)
        if not n:
            raise ValueError("a chart needs at least one data point")
        for series in self.series:
            if len(series.values) != n:
                raise ValueError(f"series {series.name!r} has {len(series.values)} values for {n} labels")
        if self.type == "pie" and (len(self.series) != 1 or n > MAX_PIE_SLICES):
            raise ValueError(f"a pie chart has exactly one series and at most {MAX_PIE_SLICES} slices")
        if self.type == "scatter" and not all(isinstance(label, (int, float)) for label in self.labels):
            raise ValueError("a scatter chart needs numeric x values")
        if self.type == "scatter" and len(self.series) > MAX_SCATTER_SERIES:
            raise ValueError(f"a scatter chart shows at most {MAX_SCATTER_SERIES} series")
        return self


def evenly(n: int, keep: int) -> list[int]:
    """`keep` indexes spread evenly over range(n), always including the first and the last."""
    if n <= keep:
        return list(range(n))
    return sorted({round(i * (n - 1) / (keep - 1)) for i in range(keep)})


def fold_slices(labels: list, values: list) -> tuple[list, list]:
    """The largest MAX_PIE_SLICES - 1 slices, plus the rest summed as "Other"."""
    ranked = sorted(zip(labels, values), key=lambda pair: pair[1] or 0, reverse=True)
    kept, rest = ranked[:MAX_PIE_SLICES - 1], ranked[MAX_PIE_SLICES - 1:]
    return [x for x, _ in kept] + [OTHER], [v for _, v in kept] + [sum(v or 0 for _, v in rest)]


def make_spec(**fields) -> ChartSpec | str:
    """A validated spec, downsampled to MAX_POINTS (pies: folded to MAX_PIE_SLICES), or an
    "Error: ..." the model can act on."""
    labels, series = fields.get("labels") or [], fields.get("series") or []
    if fields.get("type") == "pie" and len(series) == 1 and len(labels) > MAX_PIE_SLICES:
        only = dict(series[0])
        labels, values = fold_slices(labels, only["values"])
        fields = {**fields, "labels": labels, "series": [{**only, "values": values}]}
    if len(labels) > MAX_POINTS:
        keep = evenly(len(labels), MAX_POINTS)
        fields = {
            **fields,
            "labels": [labels[i] for i in keep],
            "series": [{**dict(s), "values": [dict(s)["values"][i] for i in keep]} for s in series],
            "downsampled_from": len(labels),
        }
    try:
        return ChartSpec(**fields)
    except ValidationError as exc:
        reasons = "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        return f"Error: can't draw this chart: {reasons}."


def short_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= size:
            return f"{value / size:.2f}{suffix}"
    return f"{value:,.2f}"


def summary(spec: ChartSpec) -> str:
    """What the model learns about a chart it drew: enough for a short takeaway, no raw data."""
    n = len(spec.labels)
    points = f"{n} points" + (f" (downsampled from {spec.downsampled_from})" if spec.downsampled_from else "")
    lines = [f"Chart drawn for the user ({spec.type}, \"{spec.title}\"): {points}, "
             f"x from {spec.labels[0]} to {spec.labels[-1]}."]
    for series in spec.series:
        values = [v for v in series.values if v is not None]
        if not values:
            lines.append(f"- {series.name}: no values")
            continue
        first, last = series.values[0], series.values[-1]
        lines.append(f"- {series.name}: first {short_number(first)}, last {short_number(last)}, "
                     f"min {short_number(min(values))}, max {short_number(max(values))}")
    return "\n".join(lines)
