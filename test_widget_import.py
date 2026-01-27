"""Quick test of strategy config widgets."""

from coding.gui.components.strategy_config_widgets import create_config_widget, SingleLegConfigWidget, SpreadConfigWidget

# Test factory
long_call_widget = create_config_widget("Long Call")
print(f"Long Call widget: {type(long_call_widget).__name__}")
assert isinstance(long_call_widget, SingleLegConfigWidget)

spread_widget = create_config_widget("Bull Call Spread")
print(f"Bull Call Spread widget: {type(spread_widget).__name__}")
assert isinstance(spread_widget, SpreadConfigWidget)

print("✓ All imports and widget creation successful")
