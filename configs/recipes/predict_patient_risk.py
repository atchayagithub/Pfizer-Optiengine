"""Score clinical features with the generated patient-risk model."""
import pickle

import dataiku
import pandas as pd

features = dataiku.Dataset("patient_features").get_dataframe()
folder = dataiku.Folder("${folder_id:models}")
with folder.get_download_stream("patient_risk_model.pkl") as stream:
    artifact = pickle.load(stream)

features["age"] = pd.to_numeric(features.get("age"), errors="coerce").fillna(0)
if "length_of_stay" not in features:
    admitted = pd.to_datetime(features.get("admission_date"), errors="coerce")
    discharged = pd.to_datetime(features.get("discharge_date"), errors="coerce")
    features["length_of_stay"] = (discharged - admitted).dt.days
features["length_of_stay"] = pd.to_numeric(features["length_of_stay"], errors="coerce").fillna(0)
severity = features.get("severity", pd.Series("Low", index=features.index)).astype(str).str.lower()
features["severity_score"] = severity.map({"low": 1, "moderate": 2, "high": 3}).fillna(1)

model_features = artifact["features"]
probability = artifact["model"].predict_proba(features[model_features].astype(float))[:, 1]
features["risk_probability"] = probability
features["risk_prediction"] = (probability >= 0.5).astype(int)
dataiku.Dataset("patient_risk_predictions").write_with_schema(features)
