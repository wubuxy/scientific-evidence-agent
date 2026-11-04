# Synthetic examples

Both CSVs are programmatically generated with a fixed random seed of 42.
They are demonstration inputs, not results from any paper and not a scientific benchmark.

- `experiment-runs.csv`: 40 illustrative runs; paired scores and example categorical outcomes.
- `time-series.csv`: 180 ordered points; a trend, periodic terms and small synthetic noise.

The UI only imports an example when the user selects it. Imported metadata explicitly marks it as synthetic.
LSTM results include a chronological holdout and a persistence baseline; the model is not guaranteed to outperform it.
