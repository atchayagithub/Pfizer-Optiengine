import pickle

import dataiku
import pandas as pd

prepared = dataiku.Dataset("flight_data_prepared").get_dataframe()
# The project creator substitutes the actual DSS managed-folder ID here.
folder = dataiku.Folder("${folder_id:models}")
with folder.get_download_stream("linear_regression.pkl") as stream:
    artifact = pickle.load(stream)

features = artifact["features"]
prepared["predicted_air_time"] = artifact["model"].predict(prepared[features])
dataiku.Dataset("predictions").write_with_schema(prepared)
