"""Create genuine scikit-learn model artifacts declared by a template."""
from __future__ import annotations

import csv
import pickle
from pathlib import Path
from typing import Any

from .config import ConfigurationError


class ModelGenerator:
    def __init__(self, specs: list[dict[str, Any]], sample_files: dict[str, Path], output_dir: Path):
        self.specs, self.sample_files, self.output_dir = specs, sample_files, output_dir

    def generate(self) -> list[tuple[str, str, Path]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output: list[tuple[str, str, Path]] = []
        for spec in self.specs:
            generator = spec.get("generator", "linear_regression")
            if generator not in {"linear_regression", "logistic_regression"}:
                raise ConfigurationError(f"Unsupported model generator {spec.get('generator')!r}")
            source_name = spec.get("sample_data")
            if source_name not in self.sample_files:
                raise ConfigurationError(f"Model references unknown sample_data {source_name!r}")
            model_path = self.output_dir / spec.get("filename", "linear_regression.pkl")
            if generator == "linear_regression":
                self._linear_regression(self.sample_files[source_name], model_path, spec)
            else:
                self._logistic_regression(self.sample_files[source_name], model_path, spec)
            output.append((spec["folder"], spec.get("destination", "/" + model_path.name), model_path))
        return output

    @staticmethod
    def _linear_regression(csv_path: Path, model_path: Path, spec: dict[str, Any]) -> None:
        try:
            from sklearn.linear_model import LinearRegression
        except ImportError as exc:
            raise ConfigurationError("Model generation requires scikit-learn") from exc
        features = spec.get("features", ["distance", "delay", "cancelled"])
        target = spec.get("target", "air_time")
        with csv_path.open(newline="", encoding="utf-8") as stream:
            records = list(csv.DictReader(stream))
        x = [[float(row[column]) for column in features] for row in records]
        y = [float(row[target]) for row in records]
        model = LinearRegression().fit(x, y)
        # Keep the feature contract beside the estimator for recipe consumers.
        with model_path.open("wb") as stream:
            pickle.dump({"model": model, "features": features, "target": target}, stream)

    @staticmethod
    def _logistic_regression(csv_path: Path, model_path: Path, spec: dict[str, Any]) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError as exc:
            raise ConfigurationError("Model generation requires scikit-learn") from exc
        features, target = spec.get("features", []), spec.get("target", "risk_label")
        if not features:
            raise ConfigurationError("logistic_regression requires at least one feature")
        with csv_path.open(newline="", encoding="utf-8") as stream:
            records = list(csv.DictReader(stream))
        x = [[float(row[column]) for column in features] for row in records]
        y = [int(float(row[target])) for row in records]
        if len(set(y)) < 2:
            raise ConfigurationError("logistic_regression training data needs two target classes")
        model = LogisticRegression(max_iter=1000, random_state=spec.get("random_state", 42)).fit(x, y)
        # DSS installations can run an older scikit-learn release whose
        # predict_proba implementation still reads this attribute. Newer
        # releases may no longer materialize it on the fitted estimator, so
        # persist the compatible default explicitly in the portable artifact.
        if not hasattr(model, "multi_class"):
            model.multi_class = "auto"
        with model_path.open("wb") as stream:
            pickle.dump({"model": model, "features": features, "target": target}, stream)
