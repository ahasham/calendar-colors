"""gcal — Google Calendar styling engine for calendar-colors.

Deliberately lightweight: it exposes the submodules (config, rest,
calendar_maintenance, frequency) without importing any of them at package-import
time, so `from gcal import config` (etc.) stays cheap and dependency-free.
"""
