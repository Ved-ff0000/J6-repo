import argparse
import json
from pathlib import Path

from app.evaluation.scoring import Dataset, PredictionSet, score


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate synthetic labels or score supplied predictions; makes no external calls"
    )
    parser.add_argument("--gold", default="../evaluation/golden.json")
    parser.add_argument("--predictions")
    args = parser.parse_args()
    gold = Dataset.model_validate_json(Path(args.gold).read_text())
    if args.predictions:
        print(
            json.dumps(
                score(gold, PredictionSet.model_validate_json(Path(args.predictions).read_text())),
                indent=2,
            )
        )
    else:
        print(
            json.dumps(
                {
                    "validated_cases": len(gold.cases),
                    "synthetic": gold.synthetic,
                    "predictions_scored": False,
                    "model_accuracy": None,
                    "confidence_calibrated": False,
                }
            )
        )


if __name__ == "__main__":
    main()
