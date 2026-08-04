"""Score merged employee records with the generated salary model."""
import pickle

import dataiku

merged = dataiku.Dataset("dataset_merged").get_dataframe()
folder = dataiku.Folder("${folder_id:models}")
with folder.get_download_stream("linear_regression.pkl") as stream:
    artifact = pickle.load(stream)

features = artifact["features"]
for column in features:
    merged[column] = merged[column].fillna(0).astype(float)
merged["predicted_salary"] = artifact["model"].predict(merged[features])
dataiku.Dataset("employee_predictions").write_with_schema(merged)
