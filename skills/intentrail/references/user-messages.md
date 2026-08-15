# User message patterns

First persistence:

> IntentRail 已开始跟踪本任务的目标变化；状态仅保存在当前项目，可随时让我暂停或查看。

Material update:

> 已同步：数据库由 MySQL 改为 PostgreSQL；其他已确认要求不变。接下来会相应调整数据模型和部署配置。

Conflict:

> 当前有两项不能同时满足的要求：A 与 B。你希望保留哪一项？我建议 A，因为它与已确认的验收标准一致。

Resume:

> 已恢复：目标是 X，已完成 Y；最近将 Z 改为 W。下一步是 Q，不会重复执行 R。

Verification:

> 已通过：…  未通过：…  未纳入：…  当前阻止完成：…

Keep messages compact unless the user explicitly asks for the full contract or history.
