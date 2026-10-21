# 统一应用发布

本入口适配当前服务器 `/opt/crm-sales-agent/app`、`crm-sales.service`、8010 端口、nginx 的 `app/web-dist`。不是通用部署平台。不需要在服务器运行目录初始化 Git。

用户于 2026-10-21 批准沿用原目录、接受后端重启期间短暂中断；随后明确授权完成上线。本入口已用于生产发布0ea0b41，两端490文件和健康核验通过，详情见 QUALITY_PROGRESS.md。后续发布仍须获得当次授权。

首次生产预检发现10个历史迁移源码落后，按用户单独批准，备份后仅同步源码再重新预检；没有执行数据库迁移，也没有修改manifest绕过保护。遇到类似差异必须先调查，不可直接放宽guard。

## 发布约束

- 前后端只能从同一明确 Git SHA 构建；即使工作区有未提交文件，构建输入也全部来自 `git archive` 的指定提交。构建在临时目录执行，不复用工作区 dist 或 node_modules。
- 标准库 Python 3.10+，本地另需 Git、Node 和 pnpm。锁文件冻结安装；默认仅离线缓存，显式 `--allow-downloads` 才下载锁定依赖。不会自动升级依赖。
- 只携带 `backend/app`、原 requirements/alembic 文件及前端 dist。包的整体 SHA256 由操作者另行核对，manifest 的 SHA256 用于查漏/查篡改，**不提供数字签名或来源认证**。
- 当前只支持无依赖变更、无迁移变更的发布。新旧 requirements、alembic.ini、alembic 全目录必须相同，且线上文件匹配；实际数据库必须已到安装的 migration head。变化时停止，另行设计并批准升级/回滚步骤。
- 以基线提交的全部后端 app 文件核对服务器内容，拒绝覆盖未知热修或额外文件。历史备份文件若放在 app 源码内也会触发拒绝，不能为了通过检查直接删文件。先调查并决定如何保存/迁出。
- 不修改 `.env`、虚拟环境、上传文件、业务数据或服务配置。服务器没有 Git 也可以通过源码哈希证明后端与 Git 提交一致。

## 本地构建

先核对 CI、实际服务器基线及业务发布范围。下面基线 c329c71 是 2026-10-21 的记录，后续发布必须使用当时已核实的真实基线，不能永久照抄。

```sh
python3 -m unittest discover -s ops/deploy -p test_release.py -v
python3 ops/deploy/release.py build --ref HEAD --baseline c329c71 --output /tmp/crm-release.tar.gz
# 缓存缺包时可在同样输入下加 --allow-downloads；输出路径必须尚不存在。
```

输出包含完整 commit 和包 SHA256。保留这两个值，核对远端 CI 对应同一提交；打包成功不代表业务测试或 CI 通过。脚本只从 Git 提取源码，不会自动执行全部业务回归。

## 服务器预检与正式执行

将本仓库已审阅版本的 `release.py` 和发布包上传至服务器的明确临时路径（例如 `/tmp/crm-release-<commit>/`），同时核对工具文件和包的 SHA256。上传本身不更新服务。

在服务器执行（路径、SHA256 必须替换为本次实际值）：

```sh
# 默认只预检：会创建互斥锁、临时解包目录，但不会停服务、改运行代码或版本记录。
python3 /tmp/crm-release-<commit>/release.py deploy --bundle /tmp/crm-release-<commit>/bundle.tar.gz --sha256 <完整包SHA256>

# 仅在本次正式发布已获授权时添加 --apply。
python3 /tmp/crm-release-<commit>/release.py deploy --bundle /tmp/crm-release-<commit>/bundle.tar.gz --sha256 <完整包SHA256> --apply
```

执行人需要运行 systemctl/nginx 和写发布目录的权限。脚本不内置 SSH 连接、凭据或自动提权。

## 成功与失败判据

预检检查实际 systemd 启动路径、单 worker/8010、nginx root、服务状态、数据库健康、迁移 head、基线源码、包文件完整性和备份空间；发布锁拒绝并发发布。解包拒绝路径穿越、软链接、设备、重复条目与字节码。

正式执行先备份后端 app、完整前端目录、两个旧版本记录及 RELEASE.json，然后停止服务、替换 app、启动服务并等待数据库健康，再复制前端资源、最后替换首页。旧哈希资源保留，兼容已有标签页。

完成全部新文件哈希检查、后端服务/数据库健康检查、公网首页及其直接引用的入口脚本/CSS/图标逐字节检查后，才把两端版本写为同一 SHA，并写 `RELEASE.json` 的 `status=verified`。公网 `/health` 可能是 SPA 回退页，绝不用于后端健康判断。

失败会恢复后端、完整前端、两端原版本记录，再启动服务，核对恢复文件、数据库健康及旧公网首页/入口。返回非零退出码，输出 `rollback verified`。若回滚也失败，返回非零并输出 `RELEASE FAILED AND ROLLBACK FAILED` 及备份路径，必须人工处理，不得报告发布成功。备份和失败目录都保留，不自动清理。

## 边界与人工恢复

- 这是带短暂中断的发布，不保证跨前后端原子切换或零中断；正在使用系统的浏览器可能在切换窗口失败，需要刷新/重试。
- SIGINT/SIGTERM 或普通异常在正式切换阶段触发回滚；断电、SIGKILL、磁盘/文件系统损坏无法由进程自行回滚。此时读取最新备份的 manifest.json、records.json、backend-app 和 web；先核实服务/目录当前状态，由操作者停止服务、恢复这些备份、恢复记录并重新启动与核验。不要直接把失败目录删掉。
- 未提供自动数据库迁移、依赖安装、数据库回滚、双实例/多 worker 或历史备份清理；不要用修改 manifest 的方式绕过这些限制。
- 只核对公网入口资源和本地所有新文件，不宣称通过真实角色的端到端业务验证；钉钉/企微连接状态及已授权业务场景应在发布后继续只读核对。
- 锁只能约束这个入口；手工 rsync、其他会话直接写服务器仍可破坏一致性。团队后续应用发布统一使用本入口；若有紧急人工修复，必须先登记、入 Git 并重新核对基线。
