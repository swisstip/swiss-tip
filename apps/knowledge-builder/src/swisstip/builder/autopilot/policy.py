"""Queries over the immutable workflow routing policy."""

from .models import DecisionAuthority, DecisionClass, WorkflowPolicy


def authority_for(policy: WorkflowPolicy, decision_class: DecisionClass) -> DecisionAuthority:
    return policy.routing[decision_class]


def can_delegate(policy: WorkflowPolicy, decision_class: DecisionClass) -> bool:
    return authority_for(policy, decision_class) == DecisionAuthority.FRONTIER_MODEL
