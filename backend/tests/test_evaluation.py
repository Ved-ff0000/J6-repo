from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.scoring import Dataset, PredictionSet, score


def gold():
    return Dataset.model_validate_json(
        (Path(__file__).parents[2] / "evaluation" / "golden.json").read_text()
    )


def test_no_predictions_is_not_fake_accuracy_or_zero_cost_success():
    result = score(gold(), PredictionSet(cases=()))
    assert (
        result["exact_precision"] is None
        and result["exact_recall"] == 0
        and result["brier_score"] is None
    )
    assert result["missing_case_count"] == 8


def test_supplied_predictions_penalize_duplicates_and_invalid_spans():
    data = gold()
    case = data.cases[0]
    label = case.labels[0].model_dump()
    predictions = PredictionSet.model_validate(
        {"cases": [{"id": case.id, "fields": [label, {**label, "start": 0, "end": 3}]}]}
    )
    result = score(data, predictions)
    assert (
        result["correct_exact_fields"] == 1
        and result["exact_precision"] == 0.5
        and result["citation_span_precision"] == 0.5
    )
    assert result["calibration_status"] == "INSUFFICIENT_OR_NO_CONFIDENCE_SAMPLES"


def test_unknown_case_and_forged_gold_spans_rejected():
    with pytest.raises(ValueError):
        score(gold(), PredictionSet.model_validate({"cases": [{"id": "unknown", "fields": []}]}))
    with pytest.raises(ValidationError):
        Dataset.model_validate(
            {
                "cases": [
                    {
                        "id": "x",
                        "source": "abc",
                        "labels": [
                            {
                                "group": "dates",
                                "field": "expiration",
                                "value": "fake",
                                "start": 0,
                                "end": 4,
                            }
                        ],
                    }
                ]
            }
        )
