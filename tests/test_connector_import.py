from __future__ import annotations

import unittest


class ConnectorImportTests(unittest.TestCase):
    def test_import_connector_without_launch(self) -> None:
        from shellpilot.copilot_connector import CopilotConnector

        connector = CopilotConnector()
        self.assertIsNotNone(connector)


if __name__ == "__main__":
    unittest.main()

