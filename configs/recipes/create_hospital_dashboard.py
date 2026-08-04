"""Create department-level dashboard rows with hospital-wide patient KPIs."""
import dataiku
import pandas as pd

predictions = dataiku.Dataset("patient_risk_predictions").get_dataframe()
if predictions.empty:
    dashboard = pd.DataFrame(columns=["department", "department_patient_count", "total_patients",
                                      "high_risk_patients", "average_length_of_stay", "readmission_count"])
else:
    predictions["length_of_stay"] = pd.to_numeric(predictions.get("length_of_stay"), errors="coerce").fillna(0)
    predictions["risk_prediction"] = pd.to_numeric(predictions.get("risk_prediction"), errors="coerce").fillna(0)
    total_patients = predictions["patient_id"].nunique()
    high_risk = predictions.loc[predictions["risk_prediction"] == 1, "patient_id"].nunique()
    readmissions = int((predictions.groupby("patient_id").size() > 1).sum())
    dashboard = (predictions.groupby("department", dropna=False)["patient_id"].nunique()
                 .rename("department_patient_count").reset_index())
    dashboard["department"] = dashboard["department"].fillna("Unknown")
    dashboard["total_patients"] = total_patients
    dashboard["high_risk_patients"] = high_risk
    dashboard["average_length_of_stay"] = round(float(predictions["length_of_stay"].mean()), 2)
    dashboard["readmission_count"] = readmissions
dataiku.Dataset("hospital_dashboard").write_with_schema(dashboard)
