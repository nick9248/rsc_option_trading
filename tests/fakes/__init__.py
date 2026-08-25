"""
Fixture replayers for the on-chain golden-master characterization test.

FakeDeribitApiService and FakeDatabaseRepository replay a recorded fixture
(tests/fixtures/onchain/{CURRENCY}_{TIMESTAMP}/) instead of hitting the network
or the database, so tests/characterization/test_onchain_golden_master.py runs
fully offline and deterministically. See docs/superpowers/plans/onchain-overhaul/
refactor_design_spec.md section 7.2.
"""
