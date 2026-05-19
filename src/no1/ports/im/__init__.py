"""
OneColleague IM Bridge Port

Provides IM platform integration for OneColleague groups.
Supported platforms: Telegram, Slack, Discord, Feishu/Lark, DingTalk, WeCom.
Each group can bind to one IM bot for remote control and notifications.

Architecture:
- Bridge runs as independent process per group
- Inbound: IM messages → daemon API (send) → ledger
- Outbound: ledger events → filter → IM platform

Usage:
    onecolleague im set telegram --group <group_id>
    onecolleague im start --group <group_id>
    onecolleague im stop --group <group_id>
    onecolleague im status --group <group_id>
"""

__all__: list[str] = []
