import unittest
from datetime import UTC, datetime

from swisstip.builder.autopilot.models import DecisionClass, ReviewMode, default_policy
from swisstip.builder.autopilot.policy import can_delegate


class DelegationPolicyTests(unittest.TestCase):
    def test_fast_track_delegates_only_support_work(self):
        policy = default_policy(ReviewMode.FAST_TRACK, datetime.now(UTC),
                                "frontier-review", "claude-review", "a" * 64, "b" * 64)
        delegated = {DecisionClass.SOURCE_METADATA, DecisionClass.NON_BLOCKING_VARIANT}
        for item in DecisionClass:
            self.assertEqual(can_delegate(policy, item), item in delegated)


if __name__ == "__main__":
    unittest.main()
