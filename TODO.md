# TODO

- [ ] 优化服务管理：完善按实例生成、启停、启用、禁用和状态管理的 systemd 流程。
- [ ] 为 orchestrator 增加完整日志：启动、token 刷新检查、队列领取、处理成功、失败、重试和退出都应可追踪。
- [ ] 修复队列重试上限行为：attempts 达到上限后应标记为 failed（或提供手动 retry 命令），不能永久停留在 pending 并阻塞 aggregate-day；event 2026-09-30 queue_id=525 曾复现。
