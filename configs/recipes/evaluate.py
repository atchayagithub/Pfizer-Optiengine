import dataiku
import pandas as pd

predictions = dataiku.Dataset("predictions").get_dataframe()
if "air_time" in predictions and "predicted_air_time" in predictions:
    actual = predictions["air_time"].astype(float)
    predicted = predictions["predicted_air_time"].astype(float)
    metrics = pd.DataFrame([{
        "rows": len(predictions),
        "mean_absolute_error": (actual - predicted).abs().mean(),
        "root_mean_squared_error": (((actual - predicted) ** 2).mean()) ** 0.5,
    }])
else:
    metrics = pd.DataFrame([{"rows": len(predictions)}])
dataiku.Dataset("evaluation").write_with_schema(metrics)
