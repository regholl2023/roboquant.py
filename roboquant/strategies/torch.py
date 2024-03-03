import logging

import numpy as np
import torch
from torch.utils.data import DataLoader

from roboquant import Signal, BUY, SELL
from roboquant.strategies.features import FeatureStrategy
from roboquant.strategies.rnnstrategy import _RNNDataset, Normalize

logger = logging.getLogger(__name__)


class RNNStrategy(FeatureStrategy):

    def __init__(self, model, symbol, sequences: int = 20, pct: float = 0.01):
        super().__init__(sequences)
        self.model = None
        self.sequences = sequences
        self.model = model
        self.pct = pct
        self.symbol = symbol
        self._norm_x = None
        self._norm_y = None
        self._results = []

    def predict(self, x) -> dict[str, Signal]:
        x = (x - self._norm_x[0]) / self._norm_x[1]
        x = torch.asarray(x)
        x = torch.unsqueeze(x, dim=0)  # add the batch dimension

        self.model.eval()
        with torch.no_grad():
            output = self.model(x)
            p = output.item()
            p = self._norm_y[1] * p + self._norm_y[0]
            p = p[0]
            print(p)
            self._results.append(p)
            if p > self.pct:
                print("BUY")
                return {self.symbol: BUY}
            if p < -self.pct:
                return {self.symbol: SELL}

        return {}

    @staticmethod
    def calc_norm(data):
        return data.mean(axis=0), data.std(axis=0)

    def _get_dataloaders(self, x, y, prediction: int, validation_split: float, batch_size: int):
        # what is the border between train- and validation-data
        border = round(len(y) * (1.0 - validation_split))

        x_train = x[:border - prediction]
        y_train = y[prediction:border]

        self._norm_x = self.calc_norm(x_train)
        self._norm_y = self.calc_norm(y_train)

        transform_x = Normalize(self._norm_x)
        transform_y = Normalize(self._norm_y)

        train_dataset = _RNNDataset(x_train, y_train, self.sequences, transform_x, transform_y)
        train_dataloader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        valid_dataloader = None
        if validation_split > 0.0:
            x_valid = x[border - prediction:-prediction]
            y_valid = y[border:]
            valid_dataset = _RNNDataset(x_valid, y_valid, self.sequences, transform_x, transform_y)
            valid_dataloader = DataLoader(valid_dataset, batch_size=batch_size, shuffle=False)

        return train_dataloader, valid_dataloader

    @staticmethod
    def describe(x):
        print("shape=", x.shape, "min=", np.min(x, axis=0), "max=", np.max(x, axis=0), "mean=", np.mean(x, axis=0))

    def fit(
            self,
            feed,
            optimizer=None,
            criterion=None,
            prediction=1,
            timeframe=None,
            epochs: int = 10,
            batch_size: int = 32,
            validation_split: float = 0.2,
            writer=None,
    ):
        """
        Train the model for a fixed number of epochs (dataset iterations).
        """
        optimizer = optimizer or torch.optim.Adam(self.model.parameters(), lr=0.001)
        criterion = criterion or torch.nn.MSELoss()

        x, y = self._get_xy(feed, timeframe, warmup=50)
        print()
        self.describe(x)
        self.describe(y)

        train_dataloader, valid_dataloader = self._get_dataloaders(x, y, prediction, validation_split, batch_size)

        for epoch in range(epochs):
            train_loss = self._train_epoch(train_dataloader, optimizer, criterion)
            if writer:
                writer.add_scalar("Loss/train", train_loss, epoch)
            logger.info("phase=train epoch=%s/%s loss=%s", epoch + 1, epochs, train_loss)

            if valid_dataloader:
                valid_loss = self._valid_epoch(valid_dataloader, criterion)
                if writer:
                    writer.add_scalar("Loss/valid", valid_loss, epoch)
                logger.info("phase=valid epoch=%s/%s loss=%s", epoch + 1, epochs, valid_loss)

        if writer:
            writer.flush()

    def _train_epoch(self, data_loader, opt, crit):
        model = self.model
        model.train()
        b, total_loss = 0, torch.tensor([0.0])
        for inputs, labels in data_loader:
            opt.zero_grad()
            output = model(inputs)
            loss = crit(output, labels)
            loss.backward()
            opt.step()
            total_loss += loss.detach()
            b += 1

        return (total_loss / b).item()

    def _valid_epoch(self, data_loader, crit):
        model = self.model
        model.eval()
        b, total_loss = 0, torch.tensor([0.0])
        with torch.no_grad():
            for inputs, labels in data_loader:
                output = model(inputs)
                loss = crit(output, labels)
                total_loss += loss.detach()
                b += 1

        return (total_loss / b).item()