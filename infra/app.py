#!/usr/bin/env python3
"""CDK entrypoint: cdk deploy MarketPilotStack."""
import aws_cdk as cdk

from marketpilot.stack import MarketPilotStack

app = cdk.App()
MarketPilotStack(
    app,
    "MarketPilotStack",
    region=app.node.try_get_context("region") or "us-east-1",
    account=app.node.try_get_context("account") or None,
    enable_nat=bool(app.node.try_get_context("enable-nat")),
    self_destruct=bool(app.node.try_get_context("self-destruct")),
    env={
        "region": app.node.try_get_context("region") or "us-east-1",
        "account": app.node.try_get_context("account") or None,
    },
)
app.synth()