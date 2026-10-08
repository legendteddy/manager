from __future__ import annotations

import unittest

from manager_runtime.mcp.base import MCPBoundaryError
from manager_runtime.mcp.official import OfficialMCPClient
from manager_runtime.security import NetworkSecurityPolicy


class MCPNetworkSecurityTests(unittest.TestCase):
    def test_production_policy_rejects_cleartext_mcp_url_before_sdk_use(self) -> None:
        with self.assertRaisesRegex(MCPBoundaryError, "network_cleartext_rejected"):
            OfficialMCPClient(
                "synthetic-server",
                "http://mcp.example.test",
                network_policy=NetworkSecurityPolicy(production=True),
            )

    def test_url_embedded_credentials_are_rejected_before_sdk_use(self) -> None:
        with self.assertRaisesRegex(MCPBoundaryError, "network_url_credentials_rejected"):
            OfficialMCPClient(
                "synthetic-server",
                "https://user:secret@mcp.example.test",
                network_policy=NetworkSecurityPolicy(production=True),
            )

    def test_https_target_is_accepted_by_production_network_policy(self) -> None:
        client = OfficialMCPClient(
            "synthetic-server",
            "https://mcp.example.test",
            network_policy=NetworkSecurityPolicy(production=True),
        )
        self.assertEqual("synthetic-server", client.server_id)

    def test_target_mutation_is_rechecked_before_operation(self) -> None:
        client = OfficialMCPClient(
            "synthetic-server",
            "https://mcp.example.test",
            network_policy=NetworkSecurityPolicy(production=True),
        )
        client.target = "http://mcp.example.test"
        with self.assertRaisesRegex(MCPBoundaryError, "network_cleartext_rejected"):
            client.list_tools()


if __name__ == "__main__":
    unittest.main()
