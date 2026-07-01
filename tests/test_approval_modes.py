from __future__ import annotations

import unittest

from shellpilot.agent_loop import approval_required_for_risk
from shellpilot.models import ApprovalMode, RiskLevel


class ApprovalModeTests(unittest.TestCase):
    def test_ask_mode_requires_all_non_read_only(self) -> None:
        self.assertFalse(approval_required_for_risk(ApprovalMode.ASK, RiskLevel.READ_ONLY))
        self.assertTrue(approval_required_for_risk(ApprovalMode.ASK, RiskLevel.WRITE_FILE))
        self.assertTrue(approval_required_for_risk(ApprovalMode.ASK, RiskLevel.NETWORK))
        self.assertTrue(approval_required_for_risk(ApprovalMode.ASK, RiskLevel.DANGEROUS))

    def test_approve_for_me_only_requires_dangerous(self) -> None:
        self.assertFalse(approval_required_for_risk(ApprovalMode.APPROVE_FOR_ME, RiskLevel.READ_ONLY))
        self.assertFalse(approval_required_for_risk(ApprovalMode.APPROVE_FOR_ME, RiskLevel.WRITE_FILE))
        self.assertFalse(approval_required_for_risk(ApprovalMode.APPROVE_FOR_ME, RiskLevel.NETWORK))
        self.assertTrue(approval_required_for_risk(ApprovalMode.APPROVE_FOR_ME, RiskLevel.DANGEROUS))

    def test_full_access_requires_no_approval(self) -> None:
        for risk in RiskLevel:
            self.assertFalse(approval_required_for_risk(ApprovalMode.FULL_ACCESS, risk))


if __name__ == "__main__":
    unittest.main()
