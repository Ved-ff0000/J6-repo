from pydantic import BaseModel, ConfigDict, Field, model_validator


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group: str
    field: str
    value: str = Field(min_length=1, max_length=300)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    source: str = Field(max_length=8000)
    labels: tuple[Label, ...] = Field(max_length=24)

    @model_validator(mode="after")
    def links(self) -> "GoldCase":
        if any(l.end <= l.start or self.source[l.start : l.end] != l.value for l in self.labels):
            raise ValueError("Gold span must match exact source text")
        if len({(l.group, l.field, l.value) for l in self.labels}) != len(self.labels):
            raise ValueError("Duplicate gold label")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    synthetic: bool = True
    cases: tuple[GoldCase, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique(self) -> "Dataset":
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate gold case ID")
        return self


class Predicted(Label):
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class PredictionCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    fields: tuple[Predicted, ...] = Field(max_length=24)


class PredictionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: tuple[PredictionCase, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique(self) -> "PredictionSet":
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate prediction case ID")
        return self


def score(gold: Dataset, predictions: PredictionSet) -> dict[str, object]:
    by_id = {c.id: c for c in predictions.cases}
    if set(by_id) - {c.id for c in gold.cases}:
        raise ValueError("Unknown prediction case")
    correct = total = gold_total = citations = 0
    calibration = []
    for case in gold.cases:
        labels = {(l.group, l.field, l.value) for l in case.labels}
        remaining = set(labels)
        gold_total += len(labels)
        predicted = by_id.get(case.id)
        for field in predicted.fields if predicted else ():
            total += 1
            key = (field.group, field.field, field.value)
            hit = key in remaining
            if hit:
                correct += 1
                remaining.remove(key)
            linked = field.end > field.start and case.source[field.start : field.end] == field.value
            citations += int(linked)
            if field.confidence is not None:
                calibration.append((field.confidence, int(hit)))
    precision = correct / total if total else None
    recall = correct / gold_total if gold_total else None
    f1 = 2 * correct / (total + gold_total) if total + gold_total else None
    bins = []
    if len(calibration) >= 20:
        for index in range(10):
            values = [(p, y) for p, y in calibration if min(int(p * 10), 9) == index]
            if values:
                bins.append(
                    {
                        "lower": index / 10,
                        "n": len(values),
                        "mean_confidence": sum(p for p, _ in values) / len(values),
                        "observed_exact_match": sum(y for _, y in values) / len(values),
                    }
                )
    return {
        "gold_cases": len(gold.cases),
        "prediction_cases": len(predictions.cases),
        "predicted_fields": total,
        "gold_fields": gold_total,
        "correct_exact_fields": correct,
        "exact_precision": precision,
        "exact_recall": recall,
        "exact_f1": f1,
        "citation_span_precision": citations / total if total else None,
        "missing_case_count": len(gold.cases) - len(predictions.cases),
        "calibration_status": "EXACT_MATCH_DIAGNOSTIC_ONLY"
        if bins
        else "INSUFFICIENT_OR_NO_CONFIDENCE_SAMPLES",
        "brier_score": sum((p - y) ** 2 for p, y in calibration) / len(calibration)
        if bins
        else None,
        "reliability_bins": bins,
        "limitation": "Synthetic literal exact-match and source-span metrics, not semantic/legal correctness or production generalization. Calibration is a diagnostic, not a claim that deployed confidence is calibrated.",
    }
