import logging
import time
import unittest
from decimal import Decimal

from roboquant import Order
from roboquant.brokers.ibkr import IBKRBroker


class TestIBKR(unittest.TestCase):

    def test_ibkr_order(self):
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("ibapi").setLevel(logging.WARNING)
        symbol = "JPM"

        broker = IBKRBroker()

        account = broker.sync()
        self.assertGreater(account.equity(), 0)
        self.assertEqual(len(account.orders), 0)

        # Place an order
        order = Order(symbol, 10, 150.0)
        broker.place_orders([order])
        time.sleep(5)
        self.assertEqual(len(account.orders), 0)
        account = broker.sync()
        self.assertEqual(len(account.orders), 1)
        self.assertEqual(account.orders[0].size, Decimal(10))
        self.assertEqual(symbol, account.orders[0].symbol)

        # Update an order
        update_order = order.modify(size=5, limit=160.0)
        broker.place_orders([update_order])
        time.sleep(5)
        account = broker.sync()
        self.assertEqual(len(account.orders), 1)
        self.assertEqual(account.orders[0].size, Decimal(5))
        self.assertEqual(account.orders[0].limit, 160.0)

        # Cancel an order
        cancel_order = update_order.cancel()
        broker.place_orders([cancel_order])
        time.sleep(5)
        account = broker.sync()
        self.assertEqual(len(account.orders), 0)
        print(account)
        broker.disconnect()


if __name__ == "__main__":
    unittest.main()
