"""
MenthorQ-style level monitor inside the collector: hourly computation and
storage, trend comparison, Telegram brief and urgent alerts, bot commands
(/brief /levels /changes /status /mute /help; see documentation/menthorq_monitor.md).
Numbers only: nothing here imports the chart renderer (matplotlib).
"""
