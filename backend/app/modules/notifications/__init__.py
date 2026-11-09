"""站内通知中心（T-D03，F09 封闭清单 12 类 / event_type 14 枚举）。

- models：notifications 表（幂等键唯一约束）+ 事件枚举
- service：emit 幂等投递 / 失败重发 / 收件人按 AccountScope 读取隔离 / 已读
- producers：12 类事件的生产方函数（幂等键按契约 §3 逐类规则）
- router：/api/notifications 列表 / 详情 / 已读（A 注册进 main.py）
契约：docs/modules/D/通知契约.md。
"""
