"""Named model architecture used by the saved, locally trained checkpoint."""
from torch import nn


class ForecastLSTM(nn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.lstm = nn.LSTM(1, hidden_size, batch_first=True)
        self.output = nn.Linear(hidden_size, 1)

    def forward(self, values):
        output, _ = self.lstm(values)
        return self.output(output[:, -1, :])
