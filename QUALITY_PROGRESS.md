# CRM 质量审查进度

## 2026-10-26 Q-032 用户批准后修复成本导入互斥（本地完成，未部署）

- 用户明确选择“修复并验证并发等待（推荐）”，授权同SKU成本导入排队、后到重查，范围本地验证且不部署。承接下方服务器真实PG反例，不改人工新增允许保留成本版本的既有规则，不擅自清理历史重复项。
- 实现：pricing/io_router.py新增_lock_cost_import_skus，成本导入初读SKU映射后先按产品ID升序锁全部父产品，再按SKU ID升序锁SKU并populate_existing。同日成本的原查询/更新/跳过/歧义拒绝逻辑保持；首次空查询前已有稳定父锁，后到导入等待并看到已提交版本。锁后排除已删除产品/SKU及已改码的旧映射，沿用产品→SKU→成本的锁序，与产品级删除和手工新增成本互斥。没有新依赖、表结构、唯一约束或权限改动；Q-030信息保留决定照常，不实施隐藏。
- 永久回归：新增backend/scripts/check_cost_import_race.py，登记ops/check_suites.txt（147套件登记检查通过）。修前加载原服务器快照，明确失败于“后到导入未等待，首次空行没有互斥”（`/tmp/crm-cost-import-lock-before.log`）；原始双新增/随后导入歧义日志仍保留。
- 真PG最终7组全部通过（`/tmp/crm-cost-import-lock-final.log`），两条真实AsyncSession，并由独立PG观察pg_blocking_pids证明后到请求实际等待：同文件首次并发created1/skipped1且成本1；不同内容created1/updated1且仅一版新值20；多产品相反CSV顺序正常完成、后到跳过2；相同新增预览token同时确认第一成功、第二结论变化409并回滚；手工新增成本先持产品/SKU锁时导入等待后跳过、不额外新增；产品级联软删在旧映射读完后先提交，导入刷新后failed1且成本0；SKU改码先提交亦不按旧编码写入。仅真实查询返回处控制时序，不替换SQL/锁/业务写入；不是生产HTTP或浏览器验证。
- 验证：原导入相关单元先通过，最终完整后端1306项单元通过（`/tmp/crm-cost-import-lock-full.log`），修改应用/脚本pyflakes与git diff检查通过；没有增加重复镜像单元，真实PG回归负责锁行为。永久脚本在app导入前拒绝缺DATABASE_URL、正式库名、远端host三种配置（`/tmp/crm-cost-import-lock-db-guards.log`），仅允许明确本机一次性PG，真实推送/调度关闭。
- 清理与证据：套件按本次明确ID清理；独立psql确认users/products/skus/product_costs/audit_logs全0，临时PG已停止。脚本修前/修后/最终日志、全量与安全检查及修后源码哈希归档ops/backups/cost-import-race-review-20261026/。未提交/推送/部署，现有其它本地修复与原文档删除状态保留，下方“待决定”历史状态由本条覆盖。
- 实际限制：为了与级联删除保持同一锁序，同产品的不同SKU相关写操作也可能等待，锁持有至整批预览回滚/确认提交；本轮小夹具验证了所列交错，不声称全系统所有锁图无死锁或大批导入性能已充分验证。手工新增两条同日起成本的既有允许行为不变，也不自动修复历史歧义；未来若要限制须另行决定。下一步可回归独立价格规则/客户专属价导入，或继续其它高风险业务链，不能把此锁回归推定为全项目审查完成。

## 2026-10-26 Q-032 首次并发成本导入创建同日歧义版本（中，已确认，待决定锁策略）

- 在完成Q-030人工决定落盘后转向价格导入。直接SSH读取服务器import_costs，部署仍d6e147e，pricing/io_router.py SHA256 `d732bcdb932840944fce87b13516045181b41f09cd5ff222f7dbcb2734a7e71b` 与隔离服务器快照一致；本地该文件未修改。现有单元已覆盖预览指纹防止覆盖后来更改，本轮选择此前未覆盖的首次同日并发。
- 代码证据：成本导入文档约定按SKU/生效日起始日幂等；已有ProductCost用FOR UPDATE锁行，但没有记录时SELECT空结果不锁缺口。与价格规则两个导入不同，import_costs未调用批次SKU互斥。ProductCost同日只有普通索引没有唯一约束，现有明确规则是遇到多条同日起成本时导入拒绝猜测。不能仅添加唯一约束替代授权，因为页面新增目前允许成本版本，由人工处理歧义。
- 真PG复现：全新本机crm_iso_cost_import_review（127.0.0.1:55439），服务器快照迁移；合成用户/产品/SKU，两个实际AsyncSession同时调用原import_costs，真实ProductCost空结果SELECT完成后用事件屏障控制交错，不替换数据库SQL/锁/写入。完全相同的CNY CSV均created1成功，独立查询同日成本2条；随后第三次实际导入failed1，文案明确“同一天已有2条成本版本…无法确定该更新哪一条”。`/tmp/crm-server-cost-import-race-review.py`及同名.log。无生产数据、外部服务或真实推送。
- 推荐：导入开始按统一产品→SKU顺序为涉及对象取得稳定互斥，再在锁内重读同日成本；多产品/SKU需固定升序，避免文件行顺序相反时死锁。后到请求按原幂等规则重查，不新增多余版本；不强制改动人工新增成本规则、不新增数据库约束、不自动删除历史重复成本。代价为同一产品/SKU相关写操作及大批导入可能短暂等待，需要回归与产品删除/普通成本新增/相反文件顺序的交错。仅复用价格规则的advisory锁可避免两个CSV相撞，但不与现有产品级删除/页面新增的行锁互斥，方案范围须明确。
- 决定状态：已用“两个人同时导入同一份表，两边都成功却留下两条，以后再导入反而失败”的例子说明，并按AGENTS事务策略规则询问是否允许同一SKU成本导入排队、本地验证且暂不部署。尚未收到决定，未实施锁调整或清洗；不存在审批答复超时自动同意。
- 清理：按本次明确ID删除所有cost/SKU/product/audit/user，独立psql五表均0，临时PG已停止；脚本/日志/迁移及哈希归档ops/backups/cost-import-race-review-20261026/。无本轮应用/永久测试修改、提交/推送/部署；Q-026/Q-027/Q-029/Q-031已有修复及原文档删除状态保留。后续批准后先做永久修前失败及并发矩阵；未批准则继续其它独立风险链，不能宣称价格导入全面完成。

## 2026-10-26 Q-030 人工决定：保留线索导入查重信息

- 用户在解释“有权看”后被明确问及“无权查看的是否隐藏名称/编号”，回答“不用隐藏”，随后要求继续。按该决定保留线索导入的原有查重提示，不实施Q-030候选方案；不再将本项列为等待批准的修复。复现证据保留作已知行为记录。
- 此决定仅覆盖线索导入查重提示，不授予其它接口查看范围外资料、不改变lead:view或数据范围、不修改客户/价格导入的权限。未修改应用代码、未部署；历史条目中的“待决定”状态由本条覆盖。继续独立价格导入的事务与预览确认检查。

## 2026-10-26 Q-031 客户导入失败行被误报“已进待裁定”（低，已确认；本地修复未部署）

- 与待决定Q-030独立推进，直接SSH核对服务器customer/io_router.py（db168fd30b7a840ce872104e7ed63c19cb561a8bd670a4c5207c4596444241dd）、duplicates.py、core/importing.py哈希，均与服务器快照相同；部署仍d6e147e。代码证据：导入在客户创建/裁定单生成之前就report.disputed_row；任一写入失败虽回滚行保存点，内存报告不会回滚，finalize仍计入disputed并声称已进待裁定。
- 真PG复现：全新本机crm_iso_customer_report_review（127.0.0.1:55439）从服务器快照迁移；合成已有同名客户＋CSV同名行的客户简称101字符，实际触发PG varchar(100)写入错误。原服务器实际路由返回created0/failed1/disputed1及“1条疑似撞单已进待裁定”，独立查询只有原客户1条、裁定单0条。脚本 `/tmp/crm-server-customer-report-review.py`，修前日志 `/tmp/crm-server-customer-report-before.log`。不是只根据静态执行顺序推测，没有真实客户资料/生产请求/外部推送。
- 自主修复依据：已失败且没有裁定单的行不能报告裁定成功，正确行为唯一，不改变撞单识别/权限/归属/数据库/界面交互规则。io_router仅把本行dispute_info暂存为局部字典，开裁定仍在原保存点内；整行成功后再追加report.disputed_row。没有绕过失败行校验、自动清洗或重试数据，没有实施Q-030的信息回显修改。
- 永久回归新增test_customer_import_dispute_report.py 3项：客户插入失败、客户已flush后裁定生成失败、成功对照，验证保存点回滚后客户行数、created/failed/disputed统计、消息及审计一致，保留原受限范围候选遮蔽。修前2个失败分支明确失败、成功对照通过；修后完整后端1306项通过（`/tmp/crm-customer-report-unit-full.log`），修改文件pyflakes、git diff检查通过。单元的受控错误是回归方法，不声称SQLite能执行PG长度约束。
- 真PG修后（`/tmp/crm-server-customer-report-final.log`）：原超长简称CSV现在created0/failed1/disputed0，无错误裁定成功消息、数据库仍原客户1/裁定0；合法同名CSV预览仍created1/disputed1且裁定回滚为0，携预览token确认后created1/disputed1、实际新增客户1/裁定1。证明不将合法撞单一概隐藏或改变既有流程。未执行真实HTTP/浏览器，前端无改动。
- 收尾：精确ID清理后独立psql确认users/customers/customer_duplicate_cases/audit_logs均0，临时PG已停止。脚本/日志/哈希归档ops/backups/customer-dispute-report-review-20261026/。未提交/推送/部署，保留Q-026/Q-027/Q-029及12个原文档删除状态；本轮高价值报告一致性验证完成，长期全项目审查仍未完成。下一独立方向可查价格导入的更新行预览指纹/失败报告或其他未充分覆盖业务链。
- Q-030决定状态补充：用户回复“啥意思”，已用张三/李四例子解释范围外线索名称、编号泄露与只给普通重复提示的建议；这不是批准，Q-030仍待决定，相关应用源码未改。

## 2026-10-26 Q-030 线索导入查重提示泄露范围外记录身份（中，已确认，待批准修复）

- 恢复质量目标/进度，直接SSH读取服务器customer/io_router、lead/io_router与可见性服务；版本仍d6e147e。lead/io_router修前SHA256 `2f44b5cd48e1022c9751bd015a56dfcd21998a6ccda62927ff43c633c8654fde`，service/model/core/data_scope哈希同服务器快照，归档ops/backups/lead-import-scope-review-20261026/snapshot-hashes.txt。
- 代码证据：lead/io_router::import_leads为全局手机号与公司/联系人查重；匹配手机号后RowSkipped文案拼入dup_mobile.name和id，公司+联系人匹配也拼入id，没有按lead:view及scoped_owner_ids控制回显。线索详情get_visible_lead却要求当前范围，owner为空可见、范围外owner应403；客户导入已经对范围不足者抑制候选身份。本轮没有将仅存在重复项的普通提示等同于完整资料泄露，确认泄露的是用户未提供的已有名称/内部编号。
- 隔离复现：`/tmp/crm-server-lead-import-scope-review.py`加载服务器快照，实际线索导入路由与get_visible_lead，在本机内存SQLite构造self范围、lead:create/lead:view用户101及归用户102的敏感线索987654。直接详情因DATA_SCOPE_DENIED被拒，同手机号CSV预览却返回已有线索名称SENSITIVE-OTHER-LEAD与id987654；同公司/同联系人CSV预览返回id987654。两次都skipped1，未新增记录。日志同名.log；没有真实用户资料或生产访问，DATABASE_URL固定localhost:1不可连接隔离地址。此信息回显不依赖PG并发，SQLite足以复现调用链，尚未做真实HTTP/浏览器。
- 影响与方案：拥有导入创建权的受限业务员可用手机号或公司/联系人组合试探范围外线索身份。推荐保持全局查重及跳过规则，仅在有lead:view且记录满足现有可见范围时回显名称/编号；范围外匹配只给普通重复提示，同公司多联系人提示不暴露范围外精确数量。替代为导入整体要求全量查看权会阻断正常创建，或取消全局查重会改变重复录入规则，均不推荐。
- 人工决定：按AGENTS“涉及…权限…先记录并请求人工决定”，已通过异步问题请用户批准上述最小回显修复并暂不部署；截至本条未收到决定，没有修改应用或提前实施候选。收到批准后需补受限/本范围/公海/全量/无lead:view及同公司计数矩阵，修前失败、修后通过，保留既有去重与分配行为；不把该待决定项当全部审查的阻塞。

## 2026-10-26 客户/线索混合CSV指定负责人越权与回滚（真PG通过）

- 与Q-030独立推进：现有测试覆盖单行分配权限，本轮新增“首行合法空负责人＋第二行明确指定他人且无assign权限”的真实PG回滚链。全新本机crm_iso_owner_import_review（127.0.0.1:55439），从服务器快照迁移，合成两账号，导入用户只有lead:create/customer:create；真实推送/调度关闭。
- `/tmp/crm-server-owner-import-review.py`实际调用服务器客户/线索导入路由：客户入口第二行ensure_permission抛FORBIDDEN，经真实get_db.athrow走异常回滚后，前一行客户、联系人、归属历史及审计全0；线索入口在逐行savepoint捕获越权，created1/failed1，唯一合法线索仍归导入人，LeadAssignment0。证明分配闸门与事务清理在这两种既有返回语义下均阻止越权，没有擅自统一它们的错误体验。
- 独立psql核对users/customers/contacts/customer_owner_history/leads/lead_assignments/audit_logs均0，临时PG已停止；脚本/结果/迁移归档Q-030同一目录。Q-030复现及本轮验证均未改应用/永久测试，没有提交/推送/部署；Q-026/Q-027/Q-029现有修复及文档删除状态保留。下一独立方向可继续客户撞单裁定附属数据的失败行回滚，或其他高风险链；Q-030等待人工回显规则决定。

## 2026-10-26 交接后的订单业绩归属：正常路径通过，NULL历史边界留作数据依赖风险

- 恢复现有质量目标/进度/Git状态，直接SSH读取服务器user停用/管理员保护、wecom离职交接首跑/重试、customer/documents与analytics签单归属实现。仍d6e147e；documents/analytics/service/wecom/service哈希与隔离服务器快照一致，归档ops/backups/handover-attribution-review-20261026/snapshot-hashes.txt。现有check_customer_handover_documents已覆盖带明确sales_owner_id的转移与同事抢先接手，不重复当新问题。
- 新交叉链：当前负责人改派只更新SalesOrder.owner_id；人员业绩统计_sales_owner_col使用coalesce(sales_owner_id,owner_id)。因此“字段没有改动”不等于所有历史数据下统计归属不变，需要区分明确签单人与NULL兜底。迁移e8c3a6b9d2f5已将当时NULL签单人回填为当时owner_id；现有手工建单/正常报价建单均记录签单人，不能把手造NULL反例扩大为正常新建必然存在该缺陷。
- 本机内存SQLite验证：`/tmp/crm-server-handover-attribution-review.py`加载服务器快照，实际reassign_customer_documents与analytics.sales_user_stats，用合成A/B用户、两张人民币订单（明确签单人A的100元对照、NULL签单人的200元边界）。真实改派后订单当前负责人均B，原签单字段分别保持A/NULL；再停用合成A账号，业绩统计从A300/B0变为A100/B200。证明明确签单人的业绩不因交接或停用丢失；NULL记录的统计确实跟着现负责人变化。日志 `/tmp/crm-server-handover-attribution-review.log`，脚本/日志归档上述目录。
- 状态/证据等级：正常已记录签单人的路径通过；NULL边界为已验证的条件行为、待调查的数据依赖风险，未确认生产有此类记录或当前公开入口可制造这种NULL。本轮坚持代码审查范围，未读取生产订单或员工资料；不把合成异常数据说成生产事实，不新增已确认Bug编号。
- 取舍：没有可靠签单人的历史记录不能擅自猜人、用当前负责人回填或重算业绩；去掉兜底也可能让原本计入的历史金额消失。后续若实际发现NULL记录，需依据签署/报价历史人工确定处理规则，再决定补录、单列待核实或维持现状。本轮不申请泛化的数据清洗授权，也不修改相关统计逻辑。
- 范围与收尾：只测试实际文档改派帮助函数与人员统计，不宣称完整企微离职交接/HTTP/浏览器或PG并发验证；没有启动PG、外部服务或生产写入，DATABASE_URL明确指向localhost:1不可用隔离地址。无应用改动/提交/推送/部署，原Q-026/Q-027/Q-029修复保留。下一步可转向客户/线索导入的指定负责人权限与撞单裁定回滚，避免重复已充分验证的交接对照。

## 2026-10-26 服务器SKU CSV坏行回滚与预览后数据变化（真PG通过，无代码修改）

- 恢复QUALITY_GOAL/QUALITY_PROGRESS/Git现状后转向导入链。直接SSH读取服务器import_skus、_lock_products_for_import、row_savepoint、finalize，并与现有无编码导入/逐行保存点测试去重。本轮新场景为“坏行真实SQL溢出→紧接同产品同规格合法行→预览后另一请求提交重复对象→确认整体回滚→重新预览成功”，不是只检查解析函数。
- 服务器仍d6e147e；core/importing、product/io_router、product/io、product/service四文件哈希与隔离加载server-snapshot一致，记录在ops/backups/sku-import-review-20261026/snapshot-hashes.txt。没有使用本地未部署改动推断生产行为。
- 新建本机crm_iso_sku_import_review（127.0.0.1:55439）从服务器快照迁移。`/tmp/crm-server-sku-import-review.py` 构造合成用户/产品与4行真实CSV，直接调用原import_skus及实际PG保存点；第3行长=1000000000实际超过Numeric(12,3)，不是替换flush抛异常；第4行同规格长=1验证坏行不污染身份查重。仅本机测试库，真实推送/调度关闭，未调用生产接口。
- 结果 `/tmp/crm-server-sku-import-review.log` 全部通过：首次预览created3/failed1、失败行精确第3行，整次预览SKU0；人为模拟另一请求提交first规格后，旧token确认抛VERSION_CONFLICT，导入尝试新增行整体回滚，只保留另一请求已提交的1条，无import审计；重新预览created2/failed1/skipped1且无落库；新token确认同结果，最终3条SKU，失败规格只保留其后合法行的长1，审计created2/failed_rows=[3]与报告一致。
- 边界：输入数值超出存储范围已按失败行反馈，本轮不将数据库方言报错文案当作必须自主修改的业务缺陷；没有验证客户/线索/全部价格导入、真实HTTP/浏览器、文件规模或所有并发导入组合。产品锁有序及逐行PG保存点在本场景阻止了半成品/后续行污染，不能推定整个导入系统充分验证。
- 清理：按本次明确ID删除SKU/产品/审计/用户，独立psql验证四表全0；临时PG已停止。脚本、日志与迁移/哈希归档ops/backups/sku-import-review-20261026/。本轮无新增应用/永久测试修改，无提交/推送/部署；Q-026/Q-027/Q-029原修复和文档删除状态保留。下一独立方向可查客户/线索指定负责人及重复裁定的导入回滚，或账户离职交接链。

## 2026-10-26 业务事件通知的真实PG写入异常与保存点恢复（通过，无代码修改）

- 读取当前目标/进度/Git状态后承接通知事务边界方向。直接SSH检查服务器materialize_business_notifications与record_and_notify/record_progress_event/queue_manager_notification。部署仍d6e147e；通知service/model与followup service/model四文件哈希再次与隔离加载的服务器快照一致，记录于ops/backups/notification-savepoint-review-20261026/snapshot-hashes.txt。
- 去重与新证据：现有check_process_notifications已覆盖模拟RuntimeError时的保存点回滚，本轮不重复将其当新问题；补验真正PG SQL写入失败后同事务能否继续以及失败事件并发重试。扫描提醒catch异常但无保存点的独立疑点，尚缺实际可达的非数据库异常触发，不能仅替换notify_roles抛异常就报告为已确认重复通知Bug。
- 隔离验证：新建本机crm_iso_notify_savepoint_review（127.0.0.1:55439），从服务器快照迁移；脚本 `/tmp/crm-server-notify-savepoint-review.py` 构造合成主管/权限/客户，调用真实record_progress_event保留两条业务事实，再对第一个事件的通知payload人为写入201字title，触发真实PG notifications.title长度错误。这是故障注入，不是生产入口会生成超长title的证据，没有把它登记为输入校验缺陷。
- 结果（`/tmp/crm-server-notify-savepoint-review.log`）：同轮processed=1/failed=1/notifications=2；失败事件无任何部分通知、processed_at为空并保留SQL错误，成功事件给2名接收人各1条；客户及2条FollowUp在外层事务提交后保留。仅修正本次合成错误payload后两会话并发物化，只有一方processed=1，新增通知恰好2条，旧成功事件不重复；失败标记清空，FollowUp仍2条，再物化返回全0。没有模拟业务函数、数据库锁或保存点，没有真实外部调用。
- 清理：按本次明确ID移除用户/角色/测试权限/客户/留痕/事件/通知。独立psql验证users/roles/customers/followups/business_events/notifications全0；permissions总数3，与空库迁移中的3项权限插入相符，测试创建的customer:view已按ID删除。临时PG已停止。脚本、迁移日志和结果归档ops/backups/notification-savepoint-review-20261026/。
- 状态：保存点边界针对本次SQL错误成立，未确认新Bug；未新增永久测试或应用修改，原Q-026/Q-027/Q-029本地修复保留，未提交/推送/部署。限制为进程内服务＋真实本机PG，不能证明所有异常类型/进程崩溃/真实外部投递或全系统事务；订单直接提醒异常分支仍只列调查线索。下一步转向尚未充分覆盖的导入/批量业务异常回滚或账户交接链，不在缺少新证据时反复扫描此保存点。

## 2026-10-26 Q-029 订单取消后并发扫描仍新增逾期提醒（中，已确认；批准后本地修复，未部署）

- 直接SSH读取服务器order/milestones.py、router.py、service.py、model.py，部署仍d6e147e；4文件哈希与本轮加载的服务器快照一致。milestones修前SHA256 `c1e0ffbb9cc8a4de0c91a56dd85477120ec82240a48f2ba58f52d4c2070caf79`。未将本地未发布修改当成线上行为；Q-028部分回复按用户选择仍不改。
- 证据链：原notify_overdue_milestones在初始JOIN中排除cancelled，但只锁节点；后续另查订单不锁、不复核状态。取消入口先锁订单，取消无批次订单时无需碰该节点，故能在扫描选中后先提交；扫描继续仍写催办通知。批次扫描同样是子行锁先于订单状态使用，必须一并保持锁序，不能在子锁后简单追加父锁，否则与取消/发货的父→子顺序相反。
- 真PG复现：全新本机crm_iso_milestone_review（127.0.0.1:55439）从服务器快照迁移；合成用户/客户/订单/逾期签约节点，两条真实AsyncSession，扫描节点SELECT返回处暂停，另一会话实际调用cancel_order提交，扫描继续。最终cancelled，新增跟单逾期通知1、提醒凭证1，通知created_at与notified_at均晚于cancelled_at；日志 `/tmp/crm-server-milestone-cancel-review.log`，脚本同名.py。顺序控制：订单已取消后再启动新扫描返回0。没有生产写入、真实推送或模型调用。
- 人工决定：说明数据库锁序、同单操作可能短暂等待与反序死锁风险后，用户明确批准“修复并验证等待与死锁风险（推荐）”，范围暂不部署。没有将先前批准泛化为生产发布。
- 实现：两个扫描共用_lock_reminder_orders，先按订单ID升序FOR UPDATE SKIP LOCKED锁有效候选父订单（populate_existing），再重查并锁其仍逾期子行；跳过忙碌订单留给下轮。提醒内容、收件人、到期口径、仅一次凭证、外部发送机制均不变；不改表、依赖、历史数据，不撤回已生成通知。扫描批次/节点继续独立计数。
- 永久PG回归：新增backend/scripts/check_overdue_reminder_race.py，登记ops/check_suites.txt（146套件登记检查通过）。原服务器快照先跑明确失败：取消持有父锁时旧扫描仍返回1；新代码全部通过（`/tmp/crm-overdue-race-final.log`）。覆盖两类对象已取消/父锁被取消持有时跳过；扫描先行时通过pg_blocking_pids实际证明取消正在等待扫描父锁，释放后双事务完成无死锁；同种扫描并发跳过；不同种扫描交错时其它订单仍推进；跳过对象后续补扫、重复扫描不重发。首次新代码矩阵因合成批次漏created_at失败，补齐夹具必填字段后保持业务断言完整重跑。
- 永久单元：新增test_overdue_reminder_state.py 16项，真实内存SQLite验证节点/批次逾期正常提醒、已完成/未来/无计划/已提醒/取消订单/不适用或取消批次过滤，以及父候选读取后子状态再次变化要重查。SQLite不证明锁行为。完整后端1303项通过（`/tmp/crm-overdue-lock-full-final.log`），修改文件pyflakes、git diff检查通过。没有前端改动，未宣称浏览器或生产HTTP验证。
- 安全与清理：套件导入app前拒绝缺DATABASE_URL、正式库名、远端数据库3种配置（`/tmp/crm-overdue-race-db-guards.log`）；不允许库名逃生开关绕过，真实推送/调度关闭。保护验证初稿解析了venv解释器符号链接而误用基解释器，改为venv原路径后通过，未修改应用。夹具按明确ID清理，独立psql确认users/customers/orders/milestones/batches/notifications/audits/order_status_history均0；临时PG已停止。
- 状态与限制：证据、哈希、修前失败及修后日志归档ops/backups/overdue-reminder-review-20261026/。本地修复与回归完成，未提交/推送/部署；Q-026/Q-027及12个既有文档删除状态保留。当前仅证明测试交错不死锁，不保证全系统所有锁图或生产大规模性能；忙碌订单的提醒可能延至后续扫描，属于本次明确批准的方案。下一方向可检查通知生成部分失败时的事务一致性，或其他尚未充分覆盖业务链，不重复将已验证交错作为新问题。

## 2026-10-26 服务器通知并发投递/日报/人工重排队（真PG通过，无代码修改）

- 恢复QUALITY_GOAL、当前进度及Git状态后转向调度链。直接SSH读取服务器scheduler.py、通知service.py的dispatch_pending/send_digest/requeue_for_redispatch与task/scanning.py。当前仍d6e147e；scheduler/通知service/通知model哈希分别b3a413b562f7d6efdd40221e5618d474a8a41134e55d511503c84b09017ee22c、ee116c51cf92b946796a25c9fc108e6700da04092522328ac7d9ddad6dff4634、c4914662cfd24874fca88f2adac87484a39793542eb8c30455cc7e521e9198e8，与本次加载的服务器快照逐字节一致。
- 调用链证据：调度器各任务max_instances=1/coalesce；通知投递与日报在独立事务内FOR UPDATE SKIP LOCKED抢占通知行，直到发送后写状态并提交；人工重排队同样拿行锁，只选failed/skipped；only_ids投递只选pending，防止自动worker已发送后人工再发。没有只依赖调度器进程锁，也没有凭静态带锁就判定充分验证。
- 新建本机一次性crm_iso_notify_concurrency_review（127.0.0.1:55439），服务器快照从空库迁移。临时脚本 `/tmp/crm-server-notify-concurrency-review.py` 使用真实AsyncSession/PG锁与实际服务函数，合成用户/通知；仅send_text_card出口替换为有事件屏障的假客户端，其他查询/锁/提交保持真实。导入app前将企微凭据覆盖成合成值、端点固定localhost:1，关闭其它真实外部通道，不调用真实企微。
- 四组交错通过（`/tmp/crm-server-notify-concurrency-review.log`）：两投递worker只有一次发送/attempt；日报先拿锁时指定ID的即时投递跳过；指定ID即时投递先拿锁时日报跳过；投递途中及发送成功后人工重排队均不重置/重发。每组核对最终sent、attempts=1，日报标记仅日报发送路径存在，成功后再次指定ID投递attempted=0。没有新增业务事件、任务或跟进。
- 独立psql核对users/notifications/business_events均0，临时PG已停止。脚本/日志/迁移及哈希持久归档 `ops/backups/notification-concurrency-review-20261026/`。原Q-026/Q-027修复和既有文档删除状态保留，未新增应用代码修改、未部署、重启、提交或推送。
- 限制：本轮只证明活跃并发事务下互斥，不证明外部已接受消息而进程在提交前崩溃时的恰好一次投递；也未模拟真实企微超时、跨主机故障、万级日报性能或所有定时扫描。该类外部副作用/提交间隙需要端到端幂等与运维取舍，不能以本轮全绿宣称根治。下一独立高价值方向可查订单节点/批次逾期提醒与业务完成并发的状态复核；未证实时不作修改。

## 2026-10-26 服务器失败工具重试链路（针对性验证通过，无新增代码修改）

- 承接流式生命周期审查，转向失败执行重试，未重复处理Q-026/Q-027或Q-028。通过SSH读取服务器retry_execution、retry_tool_call、_record_execution；部署仍d6e147e。router/runtime/tools/model四文件哈希与隔离加载的server-snapshot一致，归档 `ops/backups/agent-retry-review-20261026/snapshot-hashes.txt`。
- 排除“重试导致重复业务写入”疑点：runtime按实际TOOLS注册风险等级，仅允许L1重试；6种非L1工具（跟进、任务、商机下一动作、报价审批、报价草稿、报价版本）均在handler之前拒绝，管理员亦然。未因入口没有并发锁就确认写入幂等缺陷。只读调用重复记录执行历史是现有行为，不能凭记录增加判断业务重复。
- 新证据：`/tmp/crm-server-agent-retry-review.py`加载服务器快照，在本机内存SQLite调用真实retry路由/runtime与search_customers handler。DATABASE_URL指向localhost:1不可连接隔离地址，没有连接生产/演示库，也不创建模型客户端。验证全部非L1重试拒绝且不新增执行；success/running拒绝；他人会话403；收回customer:view后403；失败记录原snapshot=all而当前self时，两次重试均只返回本人客户，新增成功记录均记self/当前用户，原失败历史不覆盖，客户总行数仍2。
- 首次脚本把预置success状态夹具也计入“新成功执行”而失败；修正为明确baseline_max之后新增行计数，不修改应用或放宽预期。最终日志 `/tmp/crm-server-agent-retry-review.log` 全部通过，脚本与日志持久归档上述目录。
- 边界：内存SQLite不能证明PG并发性能、事务等待或外部服务行为；当前只对search_customers真实读链完成重试范围验证，其余L1只检查风险注册/调用链，不宣称每种L1底层都已完整验证。没有新增永久测试或应用修改，没有部署、重启、提交或推送；原两处本地动作锁修复保留。下一步可转向调度任务失败重试与后台事务边界，Q-028部分回复仍按用户选择暂不改动。

## 2026-10-26 流式断线资源审查：真实连接泄漏疑点排除；部分回复保存待决策

- 恢复当前质量目标/进度/Git状态，承接下一独立方向，没有重报Q-026/Q-027。通过SSH直接读取服务器runtime/router/database哈希，仍d6e147e，runtime为 `fe902adb59643e7cf8508818f6d6209dc6646520e566d4a02215830d53d44d37`。确认systemd实际使用backend/.venv；不能用系统Python的依赖版本代替生产虚拟环境。
- 实际服务器虚拟环境：FastAPI0.143.0、Starlette1.8.0、AnyIO4.15.1、Uvicorn0.54.0；本机分别0.141.1/1.7.0/4.15.1/0.53.0。服务器h11与httptools均声明ASGI spec2.3。直接比对StreamingResponse.__call__/stream_response/listen_for_disconnect源代码SHA256，服务器与本机逐项一致；不把本机验证说成生产HTTP验证。
- 初始替身反例：加载服务器快照，内存SQLite＋在close中await sleep(0)的FakeModel。显式gen.aclose保存部分回复并完成close；直接Task.cancel未保存部分回复；实际StreamingResponse的http.disconnect取消作用域下，FakeModel关闭开始但被取消打断。新建本机专用crm_iso_agent_stream_review，实际FastAPI进程内ASGI＋真实AsyncSession/get_db进一步重现替身现象；断线后数据库池checkedout=0，只有用户消息，夹具独立清理后users/sessions/messages均0。不能把替身未关闭推断为真实HTTPX连接泄漏。
- 用户先表示没理解，解释后明确选择“修复连接关闭问题”，范围仅连接关闭、保持半段回复现状且不部署。曾制作临时候选：anyio.move_on_after(5,shield=True)保护_close_client；6个候选单元、真实PG/ASGI及全量1293单元通过。但测试通过不足以证明修复必要，随后继续检查真实依赖保护。
- 排除关键证据：直接读取服务器OpenAI.close→HTTPX.aclose→httpcore.AsyncConnectionPool._close_connections，底层已有AsyncShieldCancellation。用真实AsyncOpenAI/HTTPX客户端连接仅127.0.0.1临时SSE模型服务，运行原服务器快照并在实际ASGI2.3分支断线；结果close_finished=True、http_client_closed=True、pool_connections=0，模拟服务端实际观测TCP关闭。日志 `/tmp/crm-real-http-stream-before.log`。没有真实密钥/外部模型/生产请求。该证据排除当前默认客户端在此断线时点泄漏的判断，不扩大为所有代理/背压/发送异常场景均无风险。
- 处理：已明确向用户纠正“连接没关完”的早期判断；撤回本轮runtime候选及仅验证该候选的新增测试，不保留无真实收益证据的补丁。runtime最终字节与服务器快照一致，Q-026/Q-027已有修复原样保留。候选、反例、真实客户端验证及日志归档 `ops/backups/agent-stream-review-20261026/`，README明确标为撤回实验，不能当成有效修复。
- Q-028（中，已复现、待决策）：runtime只在GeneratorExit保存半段回复；正常网络断线的CancelledError路径不进入该分支。已显示的文本刷新后消失，记录只保留用户问题。已有check_agent_stream的断线用例手动gen.aclose，不能证明ASGI取消也能保存。不能直接捕获CancelledError后commit，因为取消也可发生于工具写入中途，可能提交未完成业务。用户本次选择只处理连接，故本项未修改；后续需要单独设计仅保存已发出文本、隔离未完成业务事务的方案，再请求决定。
- 最终验证：撤回候选后的活动代码完整1287项单元回归通过（退出0，`/tmp/crm-stream-audit-final-baseline.log`），runtime无Git差异，git diff检查通过。临时PG已停止；无生产写入、部署、重启、提交或推送。没有新增已确认的真实连接泄漏，没有将候选全绿等同于修复成立。下一步转向后台任务/重试的会话和事务生命周期，不继续重复同一替身误报。

## 2026-10-26 Q-027 独立 cancel 接口覆盖已执行状态（高，服务器已复现；本地修复，未部署）

- 承接上一轮服务器直接读取的新线索，检查独立 `/agent/actions/{action_id}/cancel`，不把 Q-026 的 reject 修复当作该接口已覆盖。原服务器 router.py:299 经 `_get_action` 完成归属查询后，使用旧对象判断状态并写 cancelled；runtime.execute_action 已锁动作行，cancel 未共享该锁。服务器基线仍 d6e147e，router 哈希与刚才直接 SSH 读取一致。隔离复现加载 server-snapshot/backend，而非本地修改过的 app。
- 真PG反例：新建本机 localhost:55439 的 crm_iso_agent_cancel_review，从空库用服务器快照迁移；合成用户/会话/动作，使用两条真实 AsyncSession。取消先读 pending，在真实归属查询返回时暂停；确认实际执行 create_task 并提交；取消继续。两个响应 code0，最终动作 cancelled，但任务1条、成功执行1条，result 仍有 task_id，审计同时有 confirm_agent_action 与 cancel。触发为同一动作并发确认/取消且确认先提交；正常单请求不声称必现。日志 `/tmp/crm-server-cancel-review.log`，脚本 `/tmp/crm-server-cancel-review.py`。
- 修复依据与实现：服务器函数文档已经明确已执行动作不得取消；沿用确认与 Q-026 的互斥机制，不选择新业务规则。cancel_action 在本人归属检查后、状态判断前增加 `await session.refresh(action, with_for_update=True)` 及注释；锁后重新判断，先成功提交的一方决定终态。未改允许取消的状态集合、权限、审计语义、接口字段、数据库结构或依赖，没有撤销已创建任务或清洗历史记录。
- 修前单元反例：扩展 test_agent_action_rejection.py 8项取消测试，纠正初稿 audit.after 为真实模型 after_data 后，只有陈旧状态反例失败，其余15项通过；日志 `/tmp/crm-cancel-unit-before-corrected.log`。新增覆盖两种待确认态、四种终态、非本人404、陈旧状态不能覆盖执行结果；SQLite只证明状态重读，不代替PG锁验证。
- 修后：完整后端1287项单元回归通过（退出0，`/tmp/crm-cancel-unit-full-final.log`，收集清单 `/tmp/crm-cancel-collected.log`）。首次全量发现旧 test_agent_ai_safety.py 的 SimpleNamespace 替身缺 refresh，已加入 AsyncMock 与 refresh(with_for_update=True) 精确断言，原失败动作不得取消断言保留；未放宽状态断言。pyflakes修改文件、登记项检查（145套件）及 git diff检查均通过。
- 真PG永久回归：既有 check_agent_action_race.py 扩至10组，全部通过（`/tmp/crm-cancel-pg-after.log`）：原确认/拒绝回归保留，并增加并发双取消、确认先提交则迟到取消拒绝、取消先提交则任务0、拒绝与取消并发只一次决策、非本人取消404。直接调用实际路由/工具，没有模拟SQL锁，没有模型或外部调用；未声称覆盖完整HTTP/浏览器或所有业务工具副作用。
- 收尾：独立psql确认users/tasks/agent_actions/audit_logs均0，临时PG已停止。证据持久备份 `ops/backups/agent-cancel-review-20261026/`（已有忽略目录）含复现、日志、哈希与README。当前未提交、推送或部署；原Q-026修复及12个既有文档删除状态保留，人民币归档修改未恢复。服务器仍未包含Q-026/Q-027修复。
- 本轮高价值确认/拒绝/取消链路验证已完成；长期全项目审查未完成。下一独立方向为流式响应实际断线的异步生成器关闭与数据库生命周期，当前只有源码追踪，仍需在隔离环境验证，不报告为已确认缺陷。

## 2026-10-26 再次直接读取服务器代码（只读）

- 按用户“就看服务器的代码”，通过 SSH 直接读取 `/opt/crm-sales-agent/app/backend/app/modules/agent/{router.py,runtime.py}` 与 `core/database.py`；前后端部署记录均为 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16`。没有把本地 Q-026 修复当作线上代码，也没有部署或重启。
- 服务器 router/runtime/database SHA256 分别为 `ce6f6dbd61f597bdcb2ad626ecbe9b0a94d8b7f46a26b21b3d68c0a46f37e421`、`fe902adb59643e7cf8508818f6d6209dc6646520e566d4a02215830d53d44d37`、`94612060fdaa93840ef62386429064050380933febc9ef1cbc98ac3a97fe560a`。
- 新调用链线索：独立 `/agent/actions/{action_id}/cancel` 使用 `_get_action` 后直接判断并写 cancelled，没有确认路径的动作行锁；它与已复现的 reject 入口不同，尚未独立并发复现，记为待调查，不声称 Q-026 已覆盖该入口。
- 流式生命周期仅完成源码追踪：runtime 存在 finally 关闭模型客户端及 GeneratorExit 保存部分文本；数据库依赖使用 AsyncSession 上下文。不能由此推断实际客户端断线、任务取消与嵌套异步生成器关闭时序均已验证。下一步在本机服务器同源快照隔离验证这两个方向，禁止在生产制造并发写入或调用模型。此次没有新增应用代码修改或运行生产测试，保留现有工作区。

## 2026-10-26 Q-026 用户批准后正式应用修复（本地完成，先不部署）

- 用户明确补充“确定是服务器的代码bug,可以直接修复”，承接上一轮已确认的AI动作确认/拒绝竞态，批准应用候选。应用前再次只读核对服务器仍d6e147e，agent/router.py SHA256 `ce6f6dbd61f597bdcb2ad626ecbe9b0a94d8b7f46a26b21b3d68c0a46f37e421` 与本地修前一致。沿用刚才询问中的“先不部署”边界，没有把该批准扩大为生产发布或恢复人民币归档改动。
- 实现：`backend/app/modules/agent/router.py::reject_action` 在本人会话归属检查之后、状态判断之前新增注释及 `await session.refresh(action, with_for_update=True)`，与确认路径共享同一动作行锁；锁后重读状态防止陈旧rejected覆盖executed。先处理成功的一方决定原有终态，后到者仍使用现有“该动作已经处理过了”错误；不撤销已创建业务，不变更权限/风险等级/业务规则/数据库结构/依赖。修后router SHA256 `0018fb2fd15ab4981bfc93101eb56fb2cffcf5ce3f74cdf4e93bdf79982f8607`。
- 永久单元回归：新增 `backend/tests/test_agent_action_rejection.py` 8项，含归属检查期间另一提交改变动作后不能被旧状态覆盖、两种待确认态正常取消、4种终态拒绝再次取消、非本人拒绝。修前陈旧状态反例明确失败，7项既有行为对照通过（`/tmp/crm-agent-rejection-unit-before.log`）；修后与AI安全用例及后端完整1279项测试均通过（`/tmp/crm-agent-rejection-unit-after.log`、`/tmp/crm-agent-rejection-full.log`）。SQLite只验证重读状态，不声称能验证行锁。
- 永久真PG回归：新增 `backend/scripts/check_agent_action_race.py` 并登记 `ops/check_suites.txt`。只允许显式本机一次性PG库，库保护先于app导入；无DATABASE_URL、正式库名、远端隔离库地址3种环境前置拒绝均通过，且不允许逃生开关绕过库名检查。全新 `crm_iso_agent_fix`（localhost:55439）从空库迁移后，实际应用代码6场景全过：确认优先而旧拒绝不能覆盖；拒绝优先则任务0；并发双拒绝只有一次决策；并发双确认任务/成功执行各1条；非本人404；撤销工具权限403。使用两条真实AsyncSession及服务器原工具，不调用模型/真实外部服务，边界事件只控制真实归属查询的返回时序。最终日志 `/tmp/crm-agent-fix-pg-final.log`。
- 清理与检查：套件按本次明确ID清理，独立psql验证users/tasks/agent_actions/audit_logs均0，临时PG已停止；CI登记项检查通过（145个套件），修改文件pyflakes与git diff检查通过。没有跳过或放宽断言；尚未推送，所以不宣称新修复的远端CI已通过。只有Agent路由两行应用变化，人民币归档修改未恢复，12个既有文档删除状态未动。
- 当前状态：Q-026本地实现与回归完成，未提交/推送/部署；服务器仍旧版本。此条覆盖上文“候选未应用”的历史状态，服务器基线与本地唯一新增应用差异即这处已批准修复。按当前“先不部署”的范围无需操作生产，后续若要求上线，需固定提交并走统一发布预检/备份/核验流程。

## 2026-10-26 Q-026 服务器AI动作确认/拒绝并发终态冲突（高，已确认；候选修复未应用）

- 直接SSH读取服务器 agent/router.py、runtime.py、tools.py、model.py；4文件SHA256与隔离验证所加载的服务器快照完全匹配（`/tmp/crm-server-agent-race-hashes.txt`），部署仍d6e147e。本轮没有恢复已归档人民币修改。
- 证据链：确认入口调用execute_action，其runtime.py:782刷新动作行并FOR UPDATE，再判待确认状态；拒绝入口router.py:496只get动作，归属检查后:500用未锁定的内存状态判定，:502起直接写rejected。缺少与确认共享的锁及锁后重读，可能用旧状态覆盖刚完成的动作。
- 真库复现：新建crm_iso_agent_race（localhost:55439），用服务器快照迁移从空库初始化，仅构造合成用户/AgentSession/AgentAction。两条独立AsyncSession：拒绝请求已读awaiting_confirmation后在归属查询处暂停；另一连接实际确认并执行create_task工具、提交；拒绝继续。结果两个响应code均0，任务1条、成功AgentExecution1条，动作最终rejected，且同时出现confirm_agent_action/reject_agent_action审计。`/tmp/crm-server-agent-race.py`、`/tmp/crm-server-agent-race.log`。这不是生产请求、模拟业务工具或SQLite伪并发；仅在get返回边界控制时序，没有替换业务写入或锁实现。
- 触发与影响：同一动作多页/重复网络请求并发确认与取消，取消先读、确认先提交时可发生；界面说取消而真实任务已创建，动作result也被取消原因覆盖。仅create_task已做真库写入验证，其它工具的具体副作用未逐个复现，不把条件竞态说成正常单次操作必现。
- 推荐候选：拒绝先完成会话归属校验，然后session.refresh(action, with_for_update=True)，再按锁后状态决定是否允许取消。沿用确认入口已有互斥规则，不引入新状态/数据库结构/依赖，不自动撤销已执行业务。独立副本`/tmp/crm-agent-race-candidate/app`两行补丁，未改当前应用目录或服务器。
- 候选验证：同一交错下确认code0、迟到拒绝code40002，动作保持executed、任务/成功执行各1条、仅确认审计（`/tmp/crm-server-agent-race-candidate.log`）。另5场景真库通过：拒绝先提交则确认拒绝且任务0；并发双拒绝只有一次成功与一条拒绝审计；并发双确认只有一次执行与一条任务；非本人拒绝404；撤掉task:manage后确认403且动作待确认。`/tmp/crm-agent-candidate-matrix.log`。不声称候选完整CI或生产HTTP/浏览器测试已通过。
- 可审查产物：`ops/backups/agent-action-race-review-20261026/`（项目既有忽略目录）含reject-action-lock.patch、两份复现/矩阵脚本、服务器哈希与修复前后日志和README。原基线及候选均未调用模型、支付、钉钉或企微；临时PostgreSQL现已停止。
- 当前授权边界：用户最近要求“就看服务器的代码”且刚将本地同步为服务器，本轮保持只读审查；候选补丁等待用户决定是否应用，不擅自改变当前已同步基线或上线。之前确认缺陷的历史数据不改写。流式取消资源生命周期尚未完成独立验证，下一轮可继续该独立方向，不能因本问题待批准而停止所有审查。

## 2026-10-26 直接读取服务器：旧令牌跨请求撤权/吊销边界（通过，无代码改动）

- 直接只读 SSH 检查服务器 core/deps.py、core/security.py、auth/session.py、auth/router.py、user/service.py 的普通鉴权、续期、登录与改密调用链。auth/service.py并不存在，正确实现分布在router/session；没有据读取路径不存在误判服务失败。服务器仍d6e147e，无生产账号/令牌/密钥读取或业务写入。
- 当前调用链：get_current_user每请求读账号status、load_active_session、启用角色、当前角色权限和数据范围；refresh复用同一会话有效性入口；用户角色的停用状态同时作用于权限和范围。普通密码登录在读取密码前持有账号行锁，源码已有登录/改密互斥保护，本轮未重复报为新竞态。
- 新隔离验证角度：在保持同一枚JWT不变的前提下，每次请求使用新的SQLAlchemy Session读取刚提交的角色/权限/账号/会话状态，而非在同一对象上静态调用has()。脚本`/tmp/crm-server-auth-audit.py`运行实际服务器快照的鉴权函数；7个关键逻辑与模型文件再次与服务器SHA256逐一匹配（`/tmp/crm-server-auth-audit-hashes.txt`）。仅本机内存SQLite，JWT由本机随机配置密钥签发、用户和会话为合成夹具，DATABASE_URL指向localhost:1不可用隔离地址，无真实外部调用。
- 运行结果`/tmp/crm-server-auth-audit.log`全部通过：JWT额外admin/权限/范围claims不能覆盖数据库；同令牌删除客户查看权后权限守卫403；all范围收为self下一请求生效；角色停用后不保留角色特例、权限或all范围；账号停用普通鉴权/刷新均401；退出后两入口均401；空sid和sid用户不匹配均401；按改密路径吊销某人的全部会话后两枚旧令牌均不能访问/续期，其他用户会话不受影响。首次隔离脚本误用AppError.status_code，按真实异常属性http_status修正后通过，未改服务器实现。
- 证据边界：证明提交后下一请求/续期生效，不证明已经开始的长请求或流式响应会在撤权瞬间中断；SQLite不能证明真实数据库并发、跨进程缓存或密码登录锁竞争，本轮没有执行生产HTTP或修改真实用户。本轮没有新增已确认Bug或扩大权限策略，不做无依据修复。
- 状态：仅更新本地质量记录，backend/frontend/ops应用源码仍无差异；没有部署、重启、同步或恢复人民币归档修复。下一步直接看服务器流式响应/后台任务的权限复核和资源生命周期，区别正常请求开始时授权与实际可证实的越权动作。

## 2026-10-26 直接读取服务器：文件访问与产品/SKU附件链路（已排除疑点，无代码改动）

- 依据用户“就看服务器的代码”：通过只读 SSH 直接读取部署目录的 file/router.py、file/access.py、file/storage.py、product/router.py、product/service.py，以及委托可见性代码；没有将本地未发布修改视为服务器行为。服务器仍 d6e147ea0f9fcfac204398636e42c0a8bb50bc16。服务器无 rg，改用只读 Python 查找，不安装程序或修改服务器。
- 与历史去重：此前“有file:manage但无quote:manage可改文件名/删除”已被明确设计排除，本轮没有重复报漏洞。新追踪父产品删除/停用→SKU→单条/批量文件访问，检查是否存在父子级联造成的可见性遗漏。
- 已完成调用链分析：下载和预览先要求file:view，再经can_access_file反查任一可见挂载对象，模块权限和数据范围在visible_object统一处理；无挂载文件仅上传者可见。产品删除在同一事务将其未删除SKU一起软删；单条与批量SKU附件可见性都检查SKU的deleted_at。产品/SKU停用针对新业务，历史资料可读是已有明确设计，不能当成需要收紧的漏洞。
- 隔离运行证据：`/tmp/crm-server-file-audit.py` 加载已从服务器拉取的快照模块（日志打印真实__file__，不加载当前活动app目录）；4个关键模块file/access、router、storage、product/service的SHA256与本轮再次直接读取的服务器文件均相同（`/tmp/crm-server-file-audit-hashes.txt`）。仅本机内存SQLite与临时合成文件；DATABASE_URL指向localhost:1不可用隔离地址，未连接生产或本地真实数据库，未调用外部服务。
- 实际通过（`/tmp/crm-server-file-audit.log`）：正常SKU附件可见；撤销product:view后拒绝；产品停用仍可读历史附件；实际调用lock_product/delete_product使SKU级联软删后单条/批量附件访问均拒绝；无挂载文件非上传者拒绝、上传者放行；同前缀兄弟目录穿越、绝对根外路径、符号链接越界及空路径均拒绝，正常路径放行。首次正常路径断言因macOS路径别名解析差异失败，按真实resolve路径修正隔离脚本后全部通过，没有改产品代码。
- 验证限制：SQLite不能证明并发锁与事务隔离，本轮不将其描述为真实生产HTTP/浏览器或PostgreSQL并发验证；预览MIME、共享文件任一可见及其他业务对象委托路径只有调用链复核，未新增完整逐角色端到端覆盖。没有发现足以确认的新Bug，不为制造改动修改权限或产品行为。
- 状态与后续：仅更新本地质量记录，应用源码仍与服务器一致；没有部署、生产写入或恢复归档的人民币修改。后续转向未充分验证的会话/权限撤销或异步任务资源边界，以新证据继续推进。

## 2026-10-25 用户指定以服务器为准，本地代码同步完成（当前基线）

- 用户明确要求“直接看服务器的代码，把本地同步为服务器的，以服务器为准”。本轮据此将当前应用与配套测试同步至实际服务器版本；没有把本地未发布修复上传服务器，也未重启或修改生产环境。后续审查以此实际服务器基线为准，不将归档修复当成当前本地/线上行为。
- 先备份：`ops/backups/server-sync-20261025-01/`（已被项目既有忽略规则排除），包含 `working-tree.patch`、11个修改/新增文件原件及 SHA256、12个既有文档删除状态清单、各轮隔离验证日志、之前本地 frontend/dist，以及实际服务器快照。备份逐文件校验通过，未丢弃有效修改或审查证据。
- 拉取：从 `/opt/crm-sales-agent/app` 只读打包实际 backend/app、backend/alembic、requirements.txt、alembic.ini、web-dist 和两个版本记录。不含 .env、虚拟环境、密钥、数据库、上传文件或正式配置。服务器前后端均 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16`。
- 本地替换与校验：backend/app 241个文件与服务器全部相同；alembic及依赖声明匹配；frontend/dist同步了服务器全部1306个文件（包括1187个旧哈希资源），文件集合与哈希都相同。服务器没有部署前端源码，frontend/src/public/package.json/pnpm-lock.yaml 则经 Git 对应发布 SHA 核实一致，不伪称从服务器取得了这些源码。本地 HEAD 6d930ac 只比发布版多文档记录，未重写 Git 历史。
- 把本轮新增的 `test_order_quote_currency.py` 保存备份后移出活动测试目录，其余五个本轮改过的测试/脚本恢复为发布提交对应版本，避免旧服务代码配新规则断言。既有12个文档删除状态保持不变；应用、测试及脚本已无未提交差异。质量记录继续保留，不把服务器没有部署的本地资料误当成需删除的应用文件。
- 下载后再只读核对服务器，1679个同步文件的总 SHA256 指纹 `81fffc53f2597985b50354843e29599d6fdb2799a59349a7414f768e1c73325e` 与所拉快照相同；结果 `ops/backups/server-sync-20261025-01/sync-result.json`。当前基线完整后端测试1271项通过（`/tmp/crm-server-sync-baseline-tests.log`），`git diff --check`通过。测试通过仅证明基线回归，不表示此前已复现的问题不存在。
- 状态优先说明：下文Q-024/Q-025及编辑/重分配补充的修复现在均为“已备份归档，未在当前本地应用、未部署”。已复现证据不撤销，但不能再描述成当前已修复；不自动恢复归档修改。
- 被用户指令中断的报价转单审查也已保留：成本完整、已审批/已接受的非 CNY 版本，默认全款/分期6场景均可转单；本地临时修复与2项规范化CNY正向共8场景通过，源码/测试已归档。ERP六类采集事实实际调用采集函数均拒绝USD并接受规范化CNY，未改其业务代码。中断前新修复完整单元1312项通过，但尚未完成报价转单真实HTTP验证，不能报此链端到端完成。临时API及PostgreSQL已停止，无生产写操作。
- 下一步：以服务器基线继续审查，归档问题如需实施或部署，应另行明确所选方案与范围；本次同步不包含对归档修复的部署授权。

## 2026-10-25 用户要求的服务器/本地完整文件比对（只读）

- 实际服务器版本：DEPLOYED_COMMIT、WEB_DEPLOYED_COMMIT 均为 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16`。本地 HEAD `6d930ac05fcaf370d15af1f39213d98587a4071b` 只比该发布提交多发布记录；backend/app、frontend/src、frontend/public 在两提交之间无差异。
- 逐文件 SHA256：服务器 backend/app 241 文件与 Git d6e147e 全部一致，没有额外或缺失文件；本地当前 backend/app 也 241 文件，其中只有 `modules/order/service.py`、`modules/payment/router.py`、`modules/payment/schema.py`、`modules/payment/service.py` 四处不同，均为本轮未提交、未部署的人民币校验修复。其它 237 个后端文件相同。未把生产 .env、配置、数据库、用户上传文件纳入或输出。
- 本地 frontend/dist 的119个文件与服务器 web-dist 对应路径全部同哈希；服务器另有1187个额外文件，核对其路径并结合发布工具保留旧哈希资源的既有机制单独记录，不把额外旧资源算作当前前端缺发。原始只读哈希结果 `/tmp/crm-server-current-hashes.txt`。
- 结论：服务器与本地已提交的应用基线一致；与本地含新修复的工作区不完全相同。当前没有因这次比较而进行上传、部署、重启或业务数据修改。四处新修复仍待本地验证收尾及另行发布授权。

## 2026-10-25 Q-024/Q-025 补充：编辑与自动重分配链路（已本地修复）

- 本轮有新增调用链与反例：应收编辑调用 `rebalance_order_allocations`，它会处理同单全部已确认回款；新回款确认时，余额也会扣除同单历史已确认回款。前两轮只校验当前记录及期数，不能保证历史已确认回款全为 CNY。另 `update_payment` 仅在显式传 currency 时校验，省略币种只改金额时仍可修改非 CNY 来源记录。
- 修前证据：新增服务/路由 4 项（订单、后续期、已有已确认回款三种重分配来源＋新确认扣除历史非 CNY 回款）全部未拒绝（`/tmp/crm-cny-rebalance-before.log`）。补完重分配后再验证编辑路径，非 CNY 订单/节点/回款三种待确认编辑仍全部未拒绝；应收编辑三种来源已被前一步新守卫拒绝（`/tmp/crm-cny-edit-before.log` 的 3 failed/3 passed）。不将已被守卫覆盖的三个场景重复报成新缺陷。
- 修复：自动重分配在删除自动分配/修改分配模式前核对所属订单、全部有效期及本次处理的已确认回款币种；沿用原查询中的记录集合，新增批量节点币种查询。确认入口在改 status 前检查历史已确认回款币种；待确认回款编辑在金额赋值前校验记录/节点/所属订单；应收编辑先拒绝当前非 CNY 节点。统一复用既有人民币拒绝函数，不变更人民币金额规则、先到账先抵扣顺序、人工分配保护、锁序或权限。拒绝时不清洗、不折算历史数据。
- 单元：相关分配/回款/重分配 66 项通过（`/tmp/crm-cny-rebalance-final.log`）；后端完整 tests 1298 项通过（`/tmp/crm-cny-rebalance-full-final.log`）；修改的应用/单元文件 pyflakes、`git diff --check` 通过。新增 10 项边界回归，包含原分配保留以及模拟 get_db 异常回滚后原应收金额不变。仅内存 SQLite 不能证明 PostgreSQL 锁行为，另做下面真实 HTTP。
- PostgreSQL：另建全新 `crm_iso_cny_rebalance` 隔离库（localhost:55439），迁移/种子后 API 8019，关闭调度、Stream 与真实外部推送。临时脚本 `/tmp/crm-cny-pg/check_edit_currency.py` 测 7 条真实 HTTP：三种 pending 编辑拒绝原金额不变、三种应收编辑拒绝后独立会话看到原金额及原分配不变、确认拒绝历史非 CNY 来源且没有新分配。首次因脚本引入已有工具时 Python 模块路径缺 scripts 失败，补 `PYTHONPATH=.:scripts` 后全部通过（`/tmp/crm-cny-pg/rebalance-edit-http.log`）。随后完整 `check_payment_consistency.py` 通过（`/tmp/crm-cny-pg/rebalance-full-http.log`），含之前所有人民币幂等/并发/人工分配场景；没有弱化断言。
- 限制：应收编辑在公共重分配校验前可能已在请求事务中赋值/flush；真实 get_db 异常分支回滚已由独立 HTTP 会话验证，不能把这个入口描述为“在任何赋值前拒绝”。重分配服务本身的拒绝发生在释放旧分配之前。
- 收尾：临时 API 与 PostgreSQL 均已停止。没有生产写入、部署、推送或新提交；线上仍是最近只读核对的 d6e147e，本轮改动与 Q-024/Q-025 一并保留工作区。原有文档删除未动，未改数据库结构/配置/依赖。前端没有变更，本轮不声称已做新的浏览器验证。
- 下一步：转向报价转订单、ERP 源单继承以及独立权限/文件链路；历史存量统计/导出口径尚需独立核对，不把本轮确认/编辑/重分配的覆盖推定为全项目资金风险清零。

## 2026-10-25 Q-025 应收生成与人工分配缺失人民币校验（高，已本地修复）

- 承接 Q-024 的剩余入口核查，检查当前文件与上一轮测试进程（会话 12109 已终止且退出 0，针对性 49 项通过），不是无证据重复扫描。没有其他子 Agent 同时修改。
- 服务器只读证据：前后端记录均为 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16`，服务 active；线上 `payment/router.py` SHA256 `3e998aa7f6f44c334258fcc8872666d72a4875d15caa13871971f71f200e79d2`、`payment/service.py` SHA256 `a72f012c9656b1ad3f2b549665a05c74a101b6f6ca0f082dfe265856f7e75749`，与本地 Git 该发布提交逐字节一致。因此本次审查源码基线和这两个线上文件一致；未在生产制造非 CNY 记录或请求业务写接口，不能证明生产真实数据中已有这种异常。
- 缺口 A：`_create_plan` 从订单直接继承币种，两个新增应收入口共用它；`generate_receivables/_generate_receivables` 的公共前置检查只看取消状态，预览也直接计算源单金额。非 CNY 订单可继续生成应收，缺少既定人民币规则。缺口 B：`update_payment_allocations` 未检查回款/订单/新目标/旧归属期币种，替换旧分配后还会重算这些期，非 CNY 金额会继续参与人工核销。
- 修前复现：隔离 SQLite 真实路由新增 8 场景全部未抛异常（`/tmp/crm-cny-entry-before.log`）：全局建应收、订单内建应收、批量生成、预览四入口，以及回款、订单、新目标、旧归属期四种非 CNY 来源人工分配。不是仅根据静态缺少函数就判定。
- 最小修复：共享 `assert_order_can_add_receivable` 追加既有币种拒绝，覆盖生成前及拿锁后检查、两个手工应收入口；预览在计算前同样拒绝。人工分配在删旧分配/改 allocation_mode 之前核对记录、订单、新目标及旧分配期币种，旧归属批量 JOIN 查询检查，不改变权限、锁序、分配优先级或人民币金额规则。执行已明确的 CNY 唯一约束，不新增外币能力，不擅自覆盖历史金额或币种。
- 验证：相关回款一致性与分配链 49 项通过（`/tmp/crm-cny-entry-after.log`）；完整后端单元 1288 项通过（`/tmp/crm-cny-entry-full.log`）。修改的应用和单元文件 pyflakes、`git diff --check` 通过。前端没有变更，未新增页面交互或跑浏览器套件。
- PostgreSQL 真库：启动本轮已有临时集群，另建全新 `crm_iso_cny_entries`（127.0.0.1:55439），从空库迁移/种子，本机 8019 API 明确关闭定时任务、OA Stream、钉钉/企微推送。扩展现有 HTTP 回归，首跑因测试夹具订单编号超过数据库 32 字符上限失败，缩短夹具编号后不改业务限制/断言，完整复跑通过（`/tmp/crm-cny-pg/entry-http-recheck.log`）。人民币幂等、并发确认、生成及终态竞态检查全部保持；新增四个应收入口拒绝且零新增、四种人工分配拒绝且原金额/模式保持。临时 API 和数据库现已停止。
- 状态：本轮及 Q-024 仍未提交/推送/部署；生产双端仍 d6e147e。原有文档删除未动，无生产数据库/配置/服务写入。涉及历史数据处理或扩大权限的旧待决策项仍未实施。
- 下一步：继续其他来源继承路径（报价转订单、ERP 等）和独立高风险链路；回款编辑、应收调整触发的自动重分配及历史金额统计尚未在本轮逐入口验证，不把上述 8 个入口的通过扩展为所有资金链完成。

## 2026-10-25 Q-024 回款登记与确认绕过人民币唯一规则（高，已本地修复）

- 恢复：读取用户长期审查说明、AGENTS.md、QUALITY_GOAL.md、现有进度与 Git 状态；当前无其他活动子 Agent。本轮承接之前人民币继承路径待调查项，新增隔离复现证据；保留已有 CI 复跑记录及 12 个文档删除状态。长期审查目标保持进行中，不把单轮资金链验证等同于全项目完成。
- 代码及触发：`payment/service.py::resolve_payment_currency` 对省略币种直接返回节点币种，显式同币种也放行；`assert_plan_accepts_payment` 仅检查生命周期；`payment/router.py::confirm_payment` 未核对回款、订单、节点币种。因此历史/异常非 CNY 订单或应收可继续登记与财务确认，自动分配还可能把人民币回款抵到同订单的外币后续期。金额被当同单位参与核销，并可发出人民币符号的确认通知，与用户已多次确认的人民币唯一规则冲突。
- 修复前证据：内存 SQLite 调用真实路由，三种非 CNY 来源登记、三种非 CNY 记录/来源确认均成功而未拒绝；显式 USD 对 CNY 原已拒绝，但旧文案仍指引按节点外币登记。最初新增 7 场景测试均失败，其中该显式不同币种场景仅因拒绝文案不符合人民币规则，不能记成成功落库（`/tmp/crm-cny-before.log`）。另补 CNY 回款/当前期＋USD 后续期确认，修前未拒绝（`/tmp/crm-cny-period-before.log`）。
- 自主修复依据：执行既有明确 CNY 约束，不引入币种、换汇、折算或历史数据清洗；使用既有 `reject_foreign_currency`。登记校验来源订单、节点和请求币种；确认在任何状态变更、分配、审计、通知前校验回款、所属订单与所有参与分配/重算的有效应收期。将原有有效期查询前移并复用结果，锁序、人民币分配规则、金额/日期校验与权限不变。显式修改币种通过公共解析函数同样只允许 CNY；空节点币种原有拒绝继续保留。
- 修改：`backend/app/modules/payment/{router.py,service.py,schema.py}`；相关单元回归与 `scripts/check_payment_consistency.py`。旧测试中 USD 正向继承规则与既定人民币规则冲突，改为合法 CNY 规范化正向与外币拒绝断言，保留人民币幂等/锁序/并发检查；新增 9 项登记/确认/后续期/无节点订单边界用例。
- 单元验证：回款一致性 26 项通过（`/tmp/crm-cny-payment-final.log`）；完整后端 tests 1280 项通过（`/tmp/crm-cny-full-final.log`）。修改的应用及单元文件 pyflakes 与 `git diff --check` 通过。HTTP 脚本保留既有模型注册类 unused-import 静态警告，不把该脚本 pyflakes 描述为全绿。
- 真库验证：新建 `/tmp/crm-cny-pg/data` PostgreSQL 15、localhost:55439 的一次性 `crm_iso_cny_audit`，从空库迁移、种子，API localhost:8019；关闭钉钉/企微推送、定时任务与 OA Stream。首轮真实回款并发套件通过；扩展确认测试时隔离夹具遗漏应收 created_at，触发 NOT NULL，补齐测试必填时间后未放宽断言，最终整套通过（`/tmp/crm-cny-pg/http-final-recheck.log`）。覆盖同键重试/并发只一条、不同键同额同日保留两条、50+50 并发确认结清、并发生成只一套、确认/驳回唯一终态，以及非 CNY 来源三种登记拒绝零行、记录/订单/当前期/后续期四种确认拒绝且 pending/零分配。
- 收尾与边界：临时 API 已正常退出、临时 PostgreSQL 已停止，日志保留；未连接生产或演示库、未执行真实外部发送、未部署或同步提交。现有非 CNY 数据保持原样，不擅自改为人民币。没有前端代码变动，本轮未新增前端浏览器验证。幂等旧响应回放不产生新登记，不把它等同于本次新写入路径。
- 下一步：继续核查手工分配、应收创建/生成、报价转订单及 ERP 等源单继承路径；这些尚未完成新的逐入口复现，暂不宣称 CNY 全入口已封闭。历史非 CNY 数据处理、统计/导出口径及企业微信可见范围仍需独立方案和人工决定，不能由本次拦截推定获准。

## 2026-10-24 CI 浏览器启动超时复核

- 用户报告 CI 报错。运行 40074160606（提交 `6d930ac05fcaf370d15af1f39213d98587a4071b`，仅增加发布记录）首次 UI 检查在 `ops/confirmation_browser_smoke.mjs` 等待 Chrome `DevToolsActivePort` 时超过约 10 秒，报 `Error: browser timeout`；业务页面断言尚未执行。后端回归成功。失败日志 `/tmp/crm-ci-40074160606.log`。
- 未修改代码或放宽断言，重跑同一运行的失败任务；attempt 2 最终 status=completed、conclusion=success，UI 冒烟与后端回归均 success。链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40074160606 。已上线功能提交 d6e147e 对应前次 CI 亦通过。
- 证据仅确认首次浏览器启动未在等待期限内就绪、同提交复跑通过。启动 stderr 被现有脚本忽略，无法区分首次启动慢、进程退出等具体原因；不声称已根治偶发问题。若重复发生，下一步保留启动 stderr/退出码再定位，不修改业务断言掩盖失败。
- 本轮只重跑隔离 CI 并记录结果，未修改应用代码、生产服务、数据库或部署；无需重新发布。此记录保留工作区，避免为记录本身再触发一次完整 CI。

## 2026-10-24 任务关联客户名称部署完成

- 用户明确要求“部署”。发布提交 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16` 已同步 GitHub/Gitee pending-acceptance；只纳入任务客户名称实现、测试及相关记录，未纳入既有 12 个文档删除状态。企业微信权限及业务关联数据未改动。
- GitHub CI run 40072471799 后端回归与 UI 冒烟均 success；发布工具 21 项隔离测试通过。以线上实际 `c32ed650ee9d9537dfac0b197244963eaa323e3e` 为基线，从固定新提交隔离构建，包 `/tmp/crm-release-d6e147e.tar.gz`，SHA256 `1d8b5719c63a647d35a315418ab2148f1f6372980521e579212d8ccecd62b429`。实际包完整浏览器回归通过（`/tmp/crm-release-d6e147e-browser.log`），含任务名称/详情链接、不可见客户无链接、报价关联优先级。
- 上传 `/tmp/crm-release-d6e147e.oGM0Xt`，工具及包 SHA256 均匹配。统一发布入口预检返回 `preflight_only`，正式 `--apply` 返回 `verified`；备份 `/opt/crm-sales-agent/backup/unified-d6e147ea0f9f-u1q792a1`。未执行数据库迁移、依赖升级、权限配置或业务数据写入。
- 发布后独立核验：前后端版本均为完整 `d6e147ea0f9fcfac204398636e42c0a8bb50bc16`；服务 active/running、NRestarts=0；8010/health 为 up/database ok，钉钉同步/Stream 正常，公网首页 HTTP 200。统一入口已核验全部新文件与公网直接入口资源哈希。未用生产账号做任务写入或客户修改测试。
- 用户刷新任务列表即可查看新展示；记录提交无需重复部署。

## 2026-10-23 任务关联客户名称（用户批准后已实现）

- 用户回复“做，改”，批准用客户名称替代任务关联列的内部客户编号，并保留详情跳转。本次只修改客户关联展示，不根据任务标题重新绑定客户或订单，不改原有关联优先级。
- 修复前新增隔离内存 SQLite 回归，真实调用 `list_tasks`，6 个场景均因缺少 `customer_name` 返回字段失败（`/tmp/crm-task-name-before.log`）。后端现在批量查询本页任务的关联客户名称，复用客户列表的数据范围与软删除过滤，且必须具有 `customer:view` 或 admin 身份才补名称；不逐行查库。接口新增可空字段，原 ID 保留用于跳转，不改模型、迁移或业务数据。
- 前端关联仅为客户且有可见名称时显示客户名称、链接仍指向原 `/customers/{id}`；无名称时显示“客户不可查看”且不提供客户链接，不退回内部编号。报价、订单、打样、合同、商机的原有优先级不变。
- 验证：新增 6 个权限/范围/软删场景与既有任务筛选 4 项共 10 项通过（`/tmp/crm-task-name-after.log`）；后端完整 tests 套件通过（`/tmp/crm-task-name-full.log`，1271 项），均使用隔离测试夹具。前端类型检查/生产构建通过（`/tmp/crm-task-name-build.log`）；完整隔离浏览器回归通过（`/tmp/crm-task-name-browser.log`），新增验证客户名称与精确详情 href、不可查看客户无链接、已有报价关联优先显示报价。pyflakes、node 语法及 `git diff --check` 通过。
- 边界：未查询截图真实客户名、未修改生产数据、未提交/同步/部署。线上需后续发布前后端后才变化；商机内部编号展示不在此次已批准的客户名称修改范围。原有 12 个文档删除状态未动。

## 2026-10-23 任务关联显示内部客户编号（待展示方案确认）

- 用户截图任务“敲定订单”的关联列显示“客户编号 2”，询问原因。代码确认 `primaryTaskLink` 在无打样/报价/订单/合同/商机关联时使用 `customer_id`，拼接“客户编号 {id}”并跳转 `/customers/{id}`；任务 API 序列化只有 customer_id，没有客户名称。因此任务关联客户 ID=2 且没有更高优先级关联时，可确定性显示该文案。截图不能证明该任务实际绑定了某张订单，任务标题不用于判断关联类型。
- 这是内部 ID 直接面向用户的展示，不是另行生成的业务客户编号。尚未查询生产任务/客户记录，不能凭截图判断客户具体名称或是否应重新绑定订单。
- 建议待确认后改为客户名称并保留详情跳转，名称查询需沿用任务与客户可见范围；不依据任务标题自动修改关联。本轮仅核查和记录，未改代码、数据库或部署。

## 2026-10-23 个人页与客户导出工具栏发布完成

- 用户明确要求“把改动部署吧”，并在远端同步单独确认中选择“允许同步到两个现有仓库”。业务修复提交 `c32ed650ee9d9537dfac0b197244963eaa323e3e` 已推送 GitHub/Gitee 的 pending-acceptance。企业微信权限仅核查、未改授权或数据范围；工作区既有 12 个文档删除状态未纳入提交。
- 本地发布工具 21 项测试通过；从固定提交、以线上实际版本 `72775c355de9203322e90bb6e9b6841d42f8982f` 为基线隔离构建，包 `/tmp/crm-release-c32ed65.tar.gz`，SHA256 `34a28948a2ef6429377bcfbe1dbfdeb075fc3968398ab99d9e70c9da76c88d50`。实际包完整隔离浏览器回归通过，含个人页/管理深链边界及三种宽度导出提示验证；日志 `/tmp/crm-release-c32ed65-browser.log`。本次未等待新提交的远端 CI，不将本地通过表述为远端 CI 通过。
- 上传目录 `/tmp/crm-release-c32ed65.fXathb`，工具 SHA256 `8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab` 和包哈希均匹配；统一发布入口预检 `preflight_only` 后正式执行 `--apply` 返回 `verified`。备份 `/opt/crm-sales-agent/backup/unified-c32ed650ee9d-gbxp_5uw`。没有执行数据库迁移、依赖升级或业务数据修改。
- 发布后独立核验：两个版本记录均为完整 `c32ed650ee9d9537dfac0b197244963eaa323e3e`，crm-sales.service active/running、NRestarts=0；实际 8010/health 返回 up/database ok，钉钉同步运行且 Stream 已连接。统一发布工具已验证全部新文件、公网首页及直接入口资源哈希。没有用生产业务数据做改密码或客户导出测试。
- 后续：用户刷新浏览器后查看“我的”和客户列表；企业微信模块若要向业务员开放仍需明确本人联系人数据范围。发布后文档提交不需要重复部署应用。

## 2026-10-23 企业微信入口权限核查（未变更）

- 用户询问业务员为何访问企业微信仍提示无权限。代码确认该入口要求 `wecom:view`，后端读取接口同样要求该权限；`backend/scripts/seed.py` 的业务员默认权限不含此项，主管默认包含 `wecom:view/wecom:manage`。这与个人页前后端本已向全部登录人开放、仅路由误拦截的情况不同。
- 页面实际承担外部联系人同步、客户建档确认、离职继承等管理工作；待归一列表接口没有传入当前用户数据范围，服务查询未按业务员本人过滤。因此直接给普通业务员增加查看权限，可能扩大联系人可见范围，需要先明确业务边界。
- 本轮仅做只读调用链核查与记录；未读取该线上账号实际角色权限，未在线上复现或修改授权，不将默认种子配置等同于已确认的生产账号配置。未改应用代码或生产环境，无需运行数据库测试。
- 待决策：若用户要业务员使用该模块，应先确定是查看本人企微联系人还是获得现有管理视图，再设计权限与数据范围；本轮不擅自扩大权限。

## 2026-10-23 业务员「我的」误拦截与客户导出工具栏

- 用户连续反馈业务员访问 `/settings?tab=profile` 被提示无权限、客户列表导出按钮与提示错位，随后要求“做完”，据此完成两处本地修复与验证；没有新增导出授权或修改生产环境。
- 已确认个人页误拦截：菜单说明与 `SettingsPage` 均明确个人账号向所有登录人开放，后端 `/auth/permissions` 仅要求登录；但 `RoutePermissionGuard` 用设置菜单的 `settings:manage/customer:pool_review` 拦截了整个设置页。修复前使用独立浏览器、内存 API 和普通业务员身份，回归在个人登录名输入框等待失败，页面实际显示“没有访问「我的」的权限”，日志 `/tmp/crm-personal-export-before.log`。
- 修复：设置页在已登录外层内放行，由页面既有页签和查询权限限制管理区域；普通业务员进入个人页，管理深链回落个人页，不请求用户、角色、部门等管理接口。未修改角色权限、后端鉴权或数据库。
- 已确认导出提示重复：截图出现两段相同授权说明；`CustomerListPage` 在 `GuardedButton` 自带提示之外又渲染手写说明，长文字在横排 flex 中挤高按钮列并导致错位。删除重复说明，使用现有 `hintBelow`，仅在客户工具栏内让提示独占末行。按钮仍要求 `customer:export`，无权限时保留禁用及点击解释，有权限时仍打开用途弹窗。
- 验证：前端类型检查与生产构建通过（`/tmp/crm-personal-export-build.log`）；完整既有隔离浏览器确认回归通过（`/tmp/crm-personal-export-after.log`），新增验证普通业务员 profile/roles/rules 深链、管理页签与接口边界、1440/1280/1024 宽度下授权说明仅一条且位于操作按钮下方、无权限点击零导出请求，以及明确获授权者可打开用途弹窗且打开不下载。`node --check`、禁用提示静态检查（未覆盖 0）、`git diff --check` 通过。
- 环境说明：首次浏览器运行因沙箱禁止本机监听失败，获准后在本机隔离环境完成复现与回归；本机缺前端依赖，按现有锁文件补装，未改依赖声明/锁文件。构建保留既有依赖警告。
- 残余边界与下一步：本轮未连接生产数据库、修改真实用户或试导出客户；未提交、推送或部署，线上截图所示版本尚不会自动变化。生产发布需用户明确授权后走统一发布流程；工作区既有 12 个文档删除状态保持原样。

## 2026-10-21 工作台“本月成交冲刺机会”快捷提问

- 用户质疑“本月成交冲刺机会”的含义，并明确选择删除该快捷提问。证据：工作台 AI 卡片有固定按钮，点击才以预设文字打开 Copilot；全局抽屉的工作台常用问题列表也有同一句。它不是后端计算出的指标，页面加载时不自动发送。登录后默认进工作台来自登录页及路由的 `/workbench` 导航；Copilot 全局状态初始 `open:false`，没有默认弹出行为。
- 按已确认范围删除工作台按钮和抽屉中的相同快捷提问，保留其他 AI 入口、风险商机链接和工作台默认路由。将既有 UI 冒烟的双击只建一条会话、创建失败可重试、无写动作等断言改为通过工作台顶栏 AI 助手和仍存在的常用问题验证，未删除验证链路。
- 前端生产构建、`node --check ops/smoke_ui.mjs`、`git diff --check` 通过；尚未运行需隔离后端夹具的完整 UI 冒烟，未部署。用户将补图说明“默认打开这个”具体所指；该问题待图确认后再定是否需要改默认展示。

## 2026-10-20 工作台订单金额拆分与 AI Sales Agent 入口

- 2026-10-21 发布：用户明确要求部署，并明确此次不等待 GitHub CI。最终提交 `a8cab4b2d5cf6c662009c93c9c68b7bc53c1f230` 已同步 Gitee/GitHub；由该 SHA 构建的发布包 SHA256 为 `cbdf40e34e740891d2aebc837ef18979215b9b8017e18bac789ef08e9fa76ad8`。发布工具 21 项测试通过，服务器预检 `preflight_only`，正式发布 `verified`，备份 `/opt/crm-sales-agent/backup/unified-a8cab4b2d5cf-go5wz94s`。发布后前后端版本均为该 SHA；服务 active、重启次数 0、本地 `/health` 为 up/database ok、公网首页 HTTP 200。无数据库迁移、依赖或业务数据操作。尚未在线上以真实角色完成 AI 对话与金额拆分交互验证。
- 发布准备补充：提交 `4c14c4c` 的 GitHub CI 后端全量接口回归通过、前端类型检查与构建通过。本机隔离 `confirmation_browser_smoke.mjs` 通过。GitHub UI 首次运行在 Chrome 启动时超时；第二次浏览器正常，`/agent` 已渲染新页面，但通用冒烟脚本仍断言旧按钮文案“新建会话”，导致假失败。已将断言改为新页面的“开始新对话”。用户明确表示本次不等待 GitHub CI 再发布；仍需对更新后的固定提交构建、服务器预检和发布后健康进行独立核验。

- 用户确认工作台订单卡片保留客户应付合计为主金额，旁边拆出货款与运费；用户选择 AI Sales Agent 简洁聊天页，入口默认新对话，历史会话保留可选，能力说明收起，多行输入。
- 已确认：`dashboard_summary` 原只汇总 `SalesOrder.total_amount`，工作台卡片只写“按订单金额统计”，无法看出运费组成。隔离 SQLite 回归先新增“100 元合计=90 元货款+10 元运费”断言，修复前因缺少分项字段失败。现同一条订单汇总查询、同一权限与月份条件返回四项金额；仅完整且组成之和等于合计的订单进入分项，否则保留主合计并报告待核实笔数，不把 NULL 当零。前端主金额保留到分，展示货款/运费以及非零的其他费用/优惠。取消订单排除条件不变。
- 已确认：`AgentPage` 在会话列表加载后自动选择首条旧会话，故点击入口会打开旧聊天；“新建会话”立即写入空记录。现默认空白草稿，重复点击导航入口也回到新草稿，首条发送才建会话；历史可手动选择，新对话按钮也只重置草稿。修复新草稿快速连发可在创建期间产生重复会话的问题，并避免删除其他会话时跳走当前会话。页面收起工具清单、放大对话区并改用多行输入；Enter 发送、Shift+Enter 换行，输入法组合期间不发送。
- 验证：金额隔离回归修复前明确失败，修复后相关 15 项通过；前端 TypeScript 与生产构建通过，`git diff --check` 通过。本机隔离浏览器的确认流程冒烟通过，尚未逐角色做线上真实业务交互验证；未操作生产业务数据。构建仍有既存 `lottie-web` eval 警告，本轮未动该依赖。
- 待决策：分析页人员订单金额与目标完成额是否排除运费，属于考核口径，尚未修改。订单趋势图仍以含运费的订单合计为柱值；本轮只按用户明确选择调整工作台主卡片的组成展示。仓库另有 12 个原有文档删除状态，本轮未改动或纳入上述修复。

## 2026-10-21 CI 的 SKU 导入旧断言（已修复并隔离验证）

- 用户报告 CI 报错。GitHub run 39553782837 的 UI 冒烟通过，后端接口回归在 `check_sku_auto_code` 失败：脚本第 73 行仍断言 CSV 空编码必须返回“SKU编码不能为空”；当前已发布的 `/skus/import` 按用户后续批准改为自动生成编码，并按产品＋规格跳过重导。此断言属于旧规则，失败与本次分页发布无因果关系。
- 已把该脚本的无编码 CSV 场景改为验证首次生成编码并创建 1 条、重复导入跳过且不增行；保留原有并发唯一、手填编码、非法字段验证。修正 `QUALITY_GOAL.md` 中已过期的 CSV 固定约束。未修改应用代码、数据库或生产环境。
- 验证：本机 `/tmp` 下新建 PostgreSQL 15 一次性 `crm_iso_test`，从空库迁移到 head、种子初始化，localhost:8001 指向该库运行此脚本，四段均通过：16 并发编码唯一、手填编码及重复拒绝、资料校验、无编码 CSV 首导新增/重导跳过。执行后已停止临时 API 与数据库；语法解析、`git diff --check` 通过。未在生产/演示数据库运行会清理记录的脚本。修正提交 `ab454bde0ae6b6014c0371bbdf28f05de4197606` 已同步 Gitee/GitHub；GitHub CI run 39555888084 后端接口回归、UI 冒烟均 success。仅测试/文档变动，线上应用无需重新部署。

## 2026-10-21 运费分项与统计口径（待业务决策）

- 触发：用户指出报价已单列运费，但工作台等金额统计仍混在一起。代码确认报价版本与订单均有 `goods_amount`、`logistics_amount`、`other_charge_amount`、`discount_amount`，`total_amount` 已包含这些组成；订单详情已分项展示。工作台“本月订单金额”、近 6 个月订单趋势、分析页人员“订单金额”及目标基数/目标完成相关路径汇总 `SalesOrder.total_amount`，因此 100 元货款 + 10 元运费会在这些“订单金额”中显示 110 元。该行为是现有合计口径，是否应作为成交/业绩额需要业务决定，不擅自改统计。
- 另一个独立口径：工作台“本月团队成交”及成员金额取赢单商机的 `expected_amount`，不是订单实际金额，也没有运费拆分。这与工作台“本月订单金额”不能当成同一指标比较。
- 历史订单金额组成列可为 NULL；迁移没有反推旧运费。若展示/统计分项，需单列“组成待核实”的历史金额，不可把 NULL 当成 0 或回查现时报价伪造快照。手工新建订单则明确将总额记为货款、运费为 0。
- 用户补充目前没有真实历史订单，因此历史订单拆分是代码兼容边界，实际迁移成本可能很低；仍需先确认工作台主数字和目标/业绩口径。用户表示第一次两个选项问题不清晰，后续改用具体例子询问。
- 待决策：是只给工作台增加货款/运费拆分并保留应收总额，还是把“成交额/目标完成”主口径改为剔除运费后的金额；是否连分析、目标、团队指标一起统一。变更涉及业绩/考核口径与历史数据兼容，未修改代码、数据库或生产环境。

## 2026-10-21 列表分页条数选择

- 发布：用户明确要求“部署”。发布提交 `d2864939a716e3ffc922b933cd029038a9754527`，隔离构建包 SHA256 `1a2b9f805db87bde2c8eb9957264501b1e03ee466656aafe47e4b7e270342bd8`；21 项发布工具测试通过，服务器预检返回 `preflight_only`，正式发布返回 `verified`。备份位于 `/opt/crm-sales-agent/backup/unified-d2864939a716-u7j3da0h`。未执行数据库迁移、业务数据修改或依赖升级。
- 发布后只读核验：后端与前端版本记录均为该提交，`RELEASE.json` 为 `verified`；`crm-sales.service` active、`NRestarts=0`，本机 8010 `/health` 为 up/database ok，公网首页 HTTP 200。仍未逐角色做真实浏览器分页交互验证；保留下面所列残余风险。
- 用户反馈审计日志不能选择每页条数，并要求类似的翻页列表都能选择。确认后端 `/audit-logs` 等列表接口已有 `page_size`，前端若写死条数，用户只能通过逐页翻找；未分页的详情小表不在此次范围。
- 已将审计日志默认设为 15 条，可选 15/30/50/100。其余列表保持原默认条数，在审批、撞单、案例、知识询价、产品洞察、价格历史、订单草稿、商机关联记录、系统用户、回收候选、合同台账、样品及工作台待跟进客户共 17 处分页控件增加条数选择。参数进入查询缓存键，切换条数回第 1 页；检查选项不超过对应后端接口上限。已有条数选择的列表保持不变。
- 验证：静态盘点全部 `Table` 分页配置与独立 `Pagination`，没有剩余固定条数的可翻页列表；前端生产构建与 `git diff --check` 通过。本轮未运行真实浏览器业务验证；代码不改后端接口、数据或权限。
- 残余风险：尚未通过真实浏览器逐页验证所有选择器的交互与各角色视图；上线前可对审计、工作台及订单草稿抽样做 UI 冒烟。

## 2026-10-21 无编码 SKU CSV 导入发布完成

- 用户明确要求部署服务器；发布提交 `ad1e269040ded177bae8471236bd94b027a1e2fd`。发布前 21 项发布工具测试、SKU 相关后端 62 项、前端生产构建和 `git diff --check` 均通过。
- 统一发布工具预检通过：发布包 SHA256 `44e9ef178be4cf801fa44f1757abc753c14f410463924c5f984a1f849e29fdd9`，数据库迁移 head 已满足要求；没有执行迁移、依赖升级或业务数据清洗。
- 服务器正式发布返回 `status=verified`，应用备份 `/opt/crm-sales-agent/backup/unified-ad1e269040de-8jqr8118`。后端与前端 `DEPLOYED_COMMIT` / `WEB_DEPLOYED_COMMIT` 均为完整 `ad1e269040ded177bae8471236bd94b027a1e2fd`。
- 发布后核验：`crm-sales.service` active、`NRestarts=0`；8010/health 返回 status=up、database=ok、钉钉 sync_running=true、stream_connected=true。未执行真实业务导入，只完成服务和版本健康核验。

## 2026-10-21 正式统一发布完成

- 授权：用户要求“完成这些”，授权本次预检、正式部署与核验；发现历史迁移源码差异后，用户另行明确批准“备份后仅同步脚本，继续发布”，没有执行数据库升级或清洗。
- 发布版本：`0ea0b41772d5c7c1de452684795f835586733d11`。GitHub CI 39526817534 的后端回归和 UI 冒烟均 success。此前 e7bd16c 的 UI 登录页失败未单独查明根因；最新通过不能被写成已证明修复该偶发问题。
- 真实构建：从该提交 Git archive 提取源文件、按已有锁文件构建；发布包 `/tmp/crm-release-0ea0b41.tar.gz`，SHA256 `d44748964c4eff90ea66d28e8aef06112a7f6493e462ea5345932ac4fba16479`。上传服务器 `/tmp/crm-release-0ea0b41.EBv4jR/` 后工具与包哈希均一致。
- 首次生产预检正确拦截：10个历史迁移脚本不同，未停服或修改运行app。逐文件证明线上版本均与 b136ca5 之前的 Git 版本逐字节一致；revision/down_revision及downgrade AST均未变。完整备份 `/opt/crm-sales-agent/backup/migration-sources-0ea0b41-t00gui1e` 后，仅按批准的10个旧/新哈希同步源码，保护脚本哈希7053b49c…、plan.json留在备份目录。未执行 upgrade/downgrade、数据库结构或数据写入。其余依赖、配置、迁移文件无差异，数据库已在安装head。
- 随后完整预检通过，正式运行统一入口 `deploy --apply` 返回 status=verified。应用备份 `/opt/crm-sales-agent/backup/unified-0ea0b41772d5-gtwyjg27`。490个新文件哈希、全部后端app文件集合、公网首页及直接引用入口资源、服务/数据库健康均通过；旧哈希资源保留。两个版本记录均为完整0ea0b41 SHA，RELEASE.json状态verified、清单490项。
- 实际进程核验：crm-sales.service active/running，MainPID=3039608，启动时间2026-10-21 09:37:25 CST，NRestarts=0；8010/health真实JSON status=up、database=ok、钉钉sync_running=true、stream_connected=true。实际运行进程OpenAPI验证SkuCreate的sku_code为可选，草稿cost-refresh路由存在。
- 浏览器验证：将同一发布包解至本机临时目录，独立浏览器profile+内存模拟API运行完整确认回归，退出0，日志 `/tmp/crm-release-0ea0b41-browser.log`。包含成本常驻提示/维护入口、确认后更新草稿、权限受限引导、SKU空编码创建及名称规格优先展示。线上只做只读核验，没有用真实业务数据创建、删除、发送或刷新单据，不将模拟接口回归描述成生产写入测试。
- 本次上线事项已完成。持续质量审查仍按后文范围分轮进行；人民币历史继承路径、旧UI偶发登录页失败及整体界面简化仍是后续调查/方案工作，不声称全项目审查结束。对回款币种继承规则静态复核仍见旧兼容说明及旧USD夹具，本次未据此混入未经完整验证的业务修复或生产数据处理。
- 后续本轮只提交交接/进度/运维文档；生产应用版本保持0ea0b41，文档提交不需要重复重启服务。

## 2026-10-21 统一发布问题与交接

- 用户要求解决前后端发布遗漏。只读 SSH 确认服务器 `/opt/crm-sales-agent`、`/opt/crm-sales-agent/app` 无 `.git`；版本记录后端 c329c71、前端 842e384。本次未逐文件复核，版本记录不等同于内容验证。
- 现有 ops/deploy 仅有备份、监控等能力，缺少统一应用发布入口。已写 HANDOVER-2026-10-21.md，整理未发布的成本校验/SKU改动、既有验证、未确认远端CI及统一发布验收方案。
- 后续用户明确回复“接受”，批准沿用现有目录及短暂重启的发布方案。已实现 ops/deploy/release.py：Git指定SHA隔离构建、完整包/文件哈希、源码基线/依赖/迁移/数据库head/运行路径/空间预检、发布锁、双端备份、后端重启和数据库健康、前端首页最后切换、公网入口验证、两端统一版本记录与失败回滚。默认只预检，--apply才正式执行；新增 RELEASE.md 并从 README 链接。
- 验证：21项标准库隔离测试通过（包含健康/公网/部分版本记录失败回滚与回滚失败告警），登记到GitHub CI；pyflakes及diff检查通过。真实Git快照构建e7bd16c、基线c329c71成功，包490文件哈希及AST校验通过。首次离线缓存缺包后，仅按锁文件补齐缓存并构建，未改依赖版本。
- 只读核对：服务器全部241个后端app文件与c329c71基线逐一哈希比对，差异为空；systemd实际工作目录app/backend、端口8010、单worker，nginx root为app/web-dist。本轮未上传工具至服务器、未运行生产预检、未部署或重启，也未改生产数据/配置。
- 另核实此前业务提交e7bd16c的CI运行39395244839：后端success、UI failure；末尾错误为“商机未显示关联单据入口”，实际URL却是/login，原因待调查，不把它直接认定为某个业务Bug。正式发布前需查明与复验，本轮未擅自改业务或测试断言。
- 剩余边界：未在真实systemd/nginx环境执行切换或回滚；断电/SIGKILL不能自动恢复、手工绕过发布入口不能被锁约束。交接与运维文档列明人工恢复与验证步骤，不宣称零中断或全项目无缺陷。

## 当前轮次

开始于 2026-10-19。工作区：`pending-acceptance`。

## 本轮接手“19优化”未完成项（2026-10-20）

- 接手证据：原会话因所选模型容量不足连续失败；工作区仅有 SKU 导入后端和产品页提示两处未提交修改。原会话曾直接调用系统 `pytest`/`python` 失败，属于环境命令路径问题，不是代码回归结论。
- 修复内容：SKU 批量导入允许编码留空，按现有新建规则自动生成；空编码行按“产品 + 规格”识别重复，已有编码继续按编码跳过；错误、跳过、创建报告保留可识别行标识。前端在 SKU 导入入口提示该规则。
- 验证：使用项目 `backend/.venv` 重跑 `test_sku_optional_code.py`、`test_seventh_round_imports.py`、`test_sku_master_authority.py`，共 62 项通过；`frontend/pnpm run build` 成功；`git diff --check` 通过。构建仅保留已有大 chunk、MDX directive 和 lottie-web eval 警告，未把它们记为本轮缺陷。
- 收尾状态：已按用户“可以/接着做”继续完成本地验证；当前改动仍待提交并同步 Git，尚未连接生产库、执行真实导入或部署。部署前需以提交后的统一发布流程重新预检。

## 已确认并修复

## 确认交互真实浏览器复核扩展（2026-10-19，本轮未新增产品缺陷）

- 本轮恢复既有进度后继续扩展 `ops/confirmation_browser_smoke.mjs`，使用当前生产构建、独立浏览器 profile 和本机内存 API，未访问生产数据库或真实第三方服务。
- 新增已通过场景：线索列表选择第二行领取，取消不写入、确认弹窗显示第二行名称且请求 ID=32（非第一行 31）；公海客户取消无请求、确认仅提交对应客户 ID；审批列表第二行“通过”取消无请求、确认提交对应审批 ID=52；第一行“拒绝”仅一个原因表单，取消无请求，确认提交 ID=51 和实际输入原因。
- 累计浏览器覆盖九类入口：工作台完成、通知补投、回收站线索恢复、任务列表单条/批量完成、线索领取、公海客户领取、审批通过/拒绝。前述全部场景在同一次脚本运行中通过；`node --check` 与 `git diff --check` 通过。
- 本轮仅更新回归脚本和质量记录，没有新增产品代码修改，也没有执行新的服务器部署。未将真实浏览器+模拟接口测试描述成生产后端业务验证。
- 仍未逐一浏览器覆盖：回收站其他对象、系统设置批量动作、企微同步/绑定、样品各状态确认、报价撤回等；它们已有调用链/结构回归及部分后端隔离验证，后续按风险继续补覆盖，不重复给已有表单加确认。

### Q-010 单条普通回收批准遗漏确认（已修复并部署）

- 证据：`SettingsPage.decide` 在 `decision=approve`、无履约保护、非提前回收时 `needNote=false`，直接调用 `decideMutation`。虽然批量批准已有确认、例外分支已有原因输入，普通单条批准仍会一键改变客户归属。
- 修复：仅无原因表单的普通批准分支增加 `Modal.confirm`，展示客户名及回收后进入公海的影响；已有提前回收、履约保护、暂缓/驳回原因输入保持不变，不叠确认框。
- 验证：新增 `ops/recycle_decision_confirmation.test.mjs` 执行真实源码函数，修复前普通批准用例失败、其余 4 个已有原因表单分支通过，修复后 5/5 通过；确认前/取消后无 mutation，确认提交指定 ID，已有原因表单取消无请求且不额外弹窗。累计前端回归 62/62、生产构建及 diff 检查通过。
- 浏览器覆盖扩展：`ops/confirmation_browser_smoke.mjs` 使用真实构建和本机模拟 API，验证工作台完成、通知补投、回收站恢复、任务列表单条及批量共五类入口；确认/取消行为、补投不触发通知父项已读或跳转、任务单条版本及批量任务 ID 均通过。该浏览器回归已接入 CI；不将模拟接口描述为生产业务操作验证。
- 部署：沿用用户“继续补，确认没问题就部署”授权，纯前端发布，无服务重启/生产数据写入。备份 `/opt/crm-sales-agent/backup/confirmations-20261019-L0m14P/`，发布包 `/opt/crm-sales-agent/releases/confirmations-20261019-E1jVCx/`；公网 HTML/入口及 10 个页面 chunk 与新包一致，系统设置为 `SettingsPage-Cl-T2F5i.js`；后端 active、数据库健康 ok。
- 边界：本轮没有增加所有按钮的弹窗；系统提示输入框已覆盖的动作保留原有交互。其余入口尚未全部逐个浏览器操作验证，质量目标仍持续进行。

### Q-009 剩余高风险入口补确认及取消仍停用问题（已修复并部署）

- 授权：用户明确要求“继续补，确认没问题就部署”，沿用分级确认策略；已有编辑/交接/导入预览表单和后端确认拦截不重复加，查询、普通保存、已读、暂不处理等低风险操作不额外加。
- 确认缺失处置：回收站线索/产品/SKU/客户/样品/合同恢复；系统手工扫描回收预告、生成自动任务、同步钉钉人员、账号启停；通知单条/批量补投；企微四类同步和关联已有客户；案例提交审核/开修订稿、新品洞察提交评审、SKU 启停。恢复客户在原负责人不可用时原有选人 Modal 保留，原负责人可用时才补确认，不叠两层。通知补投阻止冒泡，避免误触通知详情。
- 额外真实 Bug：`ProductDetailPage.identityActionMutation` 原写法把 `window.prompt(...)` 的 null 转成 undefined 后继续调用 `stopSkuIdentity`，用户点取消仍然停用。现取消返回显式取消结果，不调用接口、不报成功、不刷新；提交空说明仍允许。
- 验证：`ops/sku_stop_cancel.test.mjs` 直接执行源码中实际 mutationFn/onSuccess，原 HEAD 3 例中取消用例失败，修复后全部通过。确认入口结构回归、工作台组件替身及既有前端回归合计 57/57 通过，生产构建和 lint 通过（既有警告保留），`git diff --check` 通过。
- 真实浏览器验证：临时浏览器 profile + 本机内存模拟接口打开生产构建页面，工作台勾选弹出确认、取消无完成请求、确认只发 1 次请求全部通过；验证中发现 Semi Checkbox 不转发 Popconfirm 注入的 onClick，已增加原生 span 触发包装修正。脚本留存于 `ops/confirmation_browser_smoke.mjs`，不连接真实数据库/外部系统。其他入口仍为源码调用链/结构验证，未声称逐一浏览器实测。
- 部署：本轮纯前端发布到 `/opt/crm-sales-agent/app/web-dist`，旧首页备份 `/opt/crm-sales-agent/backup/confirmations-20261019-bEzIcB/`，发布包 `/opt/crm-sales-agent/releases/confirmations-20261019-38BbCV/`，保留旧资源。公网 HTML/入口及工作台、报价、线索、客户、回收站、系统设置、企微、产品、新品、案例共 10 个页面 chunk 均逐字节匹配新包；服务 active，健康检查数据库 ok。未重启后端、未修改业务数据。
- Q-006/Q-007 的工作台快捷完成、报价撤回、线索/公海领取已随此次发布上线。剩余风险：本轮按分级完成上述入口补齐，但不宣称全项目无遗漏，后续新增或业务流程变化应继续审查。

### Q-008 历史部署重复依赖占满磁盘（运维风险，已授权清理）

- 证据：2026-10-19 只读检查 CRM 目录约 4.2G，其中 `backup` 3.6G；整机根分区仅剩约 167M、df 显示 100%。多份 10 月 14–16 日部署备份各含约 185M 的 Python `.venv`。
- 用户授权：在说明来源和保留策略后，用户“可以清吗”，本轮明确按只清旧程序依赖、保留业务数据和近期回滚版本执行。
- 操作：仅删除 15 份 `pre-deploy-20261014-*` 至 `pre-deploy-20261016-134001` 备份内的 `backend/.venv`；清理前检查当前服务、进程映射/工作目录及 app 符号链接未引用候选目录。保留最新完整 `pre-deploy-20261016-182550`、18/19 日修复备份，以及全部旧源码/配置/上传数据/数据库备份，未清理其他服务。
- 验证：实际可用空间增加 2736 MiB；根分区可用约 2.9G、使用率 96%；`backup` 降至 891M。26,868 个非 `.venv` 备份文件的 SHA256 在清理前后完全一致。`crm-sales.service` active，健康检查数据库 ok，未重启服务。
- 留痕：服务器 `/opt/crm-sales-agent/operations/backup-cleanup-20261019T054919Z.json` 记录删除目标、保留文件校验值和保留完整版本。
- 剩余风险：整机仍为 96%，尚未核查其他项目的磁盘用量；未擅自清理其他服务或配置自动删除规则。已删依赖的旧版本源码仍保留，如回滚到这些较早版本需重建运行环境；近期完整回滚环境保留。

### Q-007 报价撤回及领取归属操作遗漏确认（中，已修复并部署）

- 证据：`QuoteDetailPage` 的撤回直接 `withdrawMutation.mutate()`，对应后端 `/quote-versions/{version_id}/withdraw-approval` 没有二次确认要求，会结束当前审批并将报价回到草稿。`LeadListPage` 领取与 `CustomerDetailPage` 公海领取直接修改负责人，没有表单确认。
- 授权依据：用户已批准对审批撤回、客户归属等高风险操作分级补确认，不给已有表单的操作重复套确认框。
- 修复：三个入口增加一次 `Popconfirm`，明确报价撤回后果或领取对象/负责人变化；确认后才调用原接口，业务权限/流程不变，不新增依赖。
- 验证：新增 `ops/high_risk_confirmation.test.mjs`，修复前 3 例因请求位于 onClick 失败，修复后通过；静态 AST 验证实际 mutation 请求受 Popconfirm.onConfirm 保护（公海领取仅检查 claim，例外释放已有原因 prompt，不误判为裸提交）。连同既有回归合计 40/40 通过，前端生产构建成功，`git diff --check` 通过；新回归已加入 CI。
- 限制：AST 防回归不等同浏览器实际点击验证；本项与 Q-006 已在用户新授权后随 Q-009 发布，详见该项记录。

#### 高风险入口覆盖矩阵（本轮调用链核对）

| 入口 | 已有保护/本轮处置 | 验证层级 |
| --- | --- | --- |
| 工作台快捷完成 | Q-006 补确认，已部署 | 组件替身交互回归 |
| 报价撤回审批 | Q-007 补确认，已部署 | 前后端调用链 + AST 回归 |
| 领取线索/公海客户 | Q-007 补确认，已部署 | 前后端调用链 + AST 回归 |
| 报价审批通过 | 已有 Popconfirm，不重复加 | 前端调用链 |
| 报价审批拒绝/转交 | 已有原因/目标人 Modal，不重复加 | 前端调用链 |
| 审批规则发布 | 后端先返回 CONFIRM_REQUIRED，前端 ConfirmRequiredModal 再携 confirm 重发 | 前后端调用链 |
| 报价转订单 | 已有转单 Modal 和收款计划确认 | 前端调用链 |
| 附件删除 | 已有 Popconfirm，后端仍保护合同原件与引用 | 前端调用链；既有附件隔离回归见历史记录 |

- 主要高风险入口已完成调用链、结构回归和部分隔离浏览器覆盖；剩余逐入口浏览器覆盖属于验证增强，不代表已确认存在遗漏。

### Q-006 工作台快捷完成入口遗漏确认（中，已修复并部署）

- 证据：继续复核 Q-004 同类调用链发现 `WorkbenchPage` 的任务 Checkbox 仍在 `onChange` 直接调用 `completeTask`，此前仅覆盖任务列表、商机详情和批量入口，工作台遗漏。
- 修复：工作台勾选只打开带任务标题及结果说明的 `Popconfirm`，确认回调才发完成请求；保留受控勾选、请求中禁用、错误提示和无论成败都刷新任务/摘要的既有逻辑。
- 验证：新增 `ops/workbench_confirmation.test.mjs`，对真实 TSX 模块做编译和组件/接口替身渲染，修复前两例因缺少确认失败，修复后 2/2 通过；验证点击/取消无提交、确认后仅提交指定 ID、成功/失败都刷新、失败有错误提示。该用例已接入 CI。前端生产构建、lint 和 `git diff --check` 通过（lint 仅有既有警告）。
- 后续验证及发布：Q-009 增加真实浏览器+模拟接口交互验证并修正触发器兼容问题，现已部署；生产环境未点击完成真实任务。
- 同轮排除：待跟进客户手机号直接返回最初疑似绕过脱敏，核对 `contact_util.full_contact_customer_ids` 后确认其完整可见判据与当前私海列表数据范围一致（self 为本人客户、部门为团队、all 为全量），且明细排除公海。没有发现此调用链的额外联系方式权限泄露证据，不擅自收紧既有授权。

### Q-005 待跟进客户阈值展示与 200 条截断（中，已修复并部署）

- 证据：线上 `customer_stale_days` 实际为 30（排除“服务器配置成 3 天”的初始猜测）。抽样客户的跟进/业务进展均为空、建档仅 3–4 天；原筛选直接纳入，展示却回退建档时间计算出 3 天。原明细 `.limit(200)` 且 `total=len(items)`，真实数量超过 200 时后续数据无法访问、总数也错误。
- 用户决定：方案 A，无任何活动记录的新客户超过配置阈值后才进入待跟进，不修改服务器阈值或历史数据。
- 本地修复：共享 SQL 条件增加无活动记录时的建档时间判断，卡片、团队成员统计和明细同步应用；摘要返回实际 `stale_customer_days`，前端去掉写死的 30 天和“系统业务进展不算”的错误说明；明细使用真实 count + SQL 分页，抽屉每页 100 条并显示真实总数。
- 验证：新增内存 SQLite 回归修复前 2 项失败，修复后通过。覆盖新建 3 天不提醒、无记录超过 30 天提醒、精确 30 天边界不提醒、近期有效跟进/业务进展不提醒、205 条可跨页且总数一致、空页、个人数据权限和公海/软删排除。后端全套 `1203 passed in 14.61s`；前端生产构建成功；`git diff --check` 通过。
- 状态与限制：已部署（见下方发布验证）；后续浏览器复核使用隔离内存 API 验证交互，不把模拟接口当作生产业务验证。线上真实数量随时间和跟进动作变化，原查询约 2551 条属于旧口径，不能当作修复后的预期数量。

#### Q-005 / Q-004 发布验证（2026-10-19）

- 经用户“部署”批准，发布 Q-005 后端两个 analytics 文件和前端构建产物（含同批已批准的 Q-004 确认弹窗）。生产两个后端文件在替换前已与本地 HEAD 基线比对一致，未覆盖独有改动；没有数据库迁移或业务数据清洗。
- 实际发布目录 `/opt/crm-sales-agent/app/web-dist`，后端及旧首页备份 `/opt/crm-sales-agent/backup/stale-a-20261019-zWf0hl/`，发布包 `/opt/crm-sales-agent/releases/stale-a-20261019-7olksd/`。资源先复制校验，后端健康后替换首页，失败具备回滚流程。
- 生产构建及待跟进针对性回归再次通过。后端重启后健康检查数据库 ok；公网 HTML/入口 JS 与发布包逐字节一致，工作台 `WorkbenchPage-BCH1WTun.js`、任务 `TaskListPage-DJDl-XnL.js`、商机 `OpportunityDetailPage-Cdrnog_g.js`、打样 `SampleListPage-DEfyBy0Z.js` 均验证为新资源且有对应修复标记。
- 强制 READ ONLY 事务内按账号 5 实际 `all` 数据范围执行真实业务函数：阈值 30 天、摘要待跟进数=明细 total=0，第一页/第二页均空；团队各成员待跟进数与独立 SQL 逐一一致。旧口径约 2551 条的减少来自新客户未满阈值被排除，不代表客户删除或清空。
- Q-004 确认弹窗已随此次发布上线；后续隔离浏览器复核已验证工作台/任务确认交互。超 200 条翻页由内存隔离库用例验证，当前生产新口径为 0，无法以生产现存数据验证非空第二页。

### Q-003 工作台逾期统计与任务列表筛选不一致（中，已修复并部署）

- 证据：工作台 `dashboard_summary` 按当前数据权限汇总 `pending/doing`；点击只跳 `/tasks`，任务页默认 `mine=true,status=pending`。线上只读查询确认当前唯一逾期任务 ID=1，负责人为刘帅帅（1281），状态 pending，导致其他账号在个人列表看不到。
- 修复：工作台待办跳转显式传递 `mine=false,status=active`；逾期数字链接追加 `overdue=true`；列表支持未完成（pending/doing）组合筛选，URL 保存筛选并在切换时重置页码。后端仍执行原有 self/部门/全部数据范围，未扩大权限。
- 自主依据：仅对齐已有统计与明细范围，不改变任务状态、负责人或权限规则，不清洗历史数据。
- 验证：隔离内存 SQLite 回归先失败 4 项、修复后通过，覆盖未完成状态集合、逾期、个人筛选与 self 数据范围；后端全套回归退出 0；前端生产构建通过；`git diff --check` 通过。
- 部署：2026-10-19 已同步后端 `task/router.py`，发布前端产物到 nginx 实际目录 `/opt/crm-sales-agent/app/web-dist`，并重启 `crm-sales.service`；旧后端文件和首页入口备份在 `/opt/crm-sales-agent/backup/20261019-workbench/`。
- 线上验证：公网 HTML、入口 JS、`WorkbenchPage` 和 `TaskListPage` 均与发布目录逐字节一致；公网工作台资源含 `status=active`，任务资源含“未完成”；后端服务 active、健康检查数据库 ok、HTTPS 首页 200。
- 发布当时未执行浏览器端到端点击；后续已用隔离浏览器与模拟 API 补充确认/取消交互验证，不把模拟接口当作生产业务操作。

### Q-004 任务完成缺少二次确认（中，已恢复数据；前端修复已部署）

- 证据：线上 ID 1《敲定订单》在 11:27 被直接完成；`TaskListPage` 和 `OpportunityDetailPage` 的“完成”点击直接调用完成接口，任务列表的批量完成按钮也直接提交，没有确认弹窗。
- 数据处置：经用户明确确认，已将 ID 1 从 `done` 恢复为 `pending`，清空 `completed_at`，重算客户 2 的 `next_followup_at`，并写入 `reopen_manual` 审计记录；恢复前对标题、负责人和原完成时间做了精确条件校验。
- 本地修复：任务列表单条完成、任务批量完成、商机详情完成，以及打样批准/拒绝/重新提交/寄样/签收/开新修订版均增加确认弹窗，确认后才调用原接口；不改变后端状态规则。前端构建和 lint 通过（仅保留既有 warnings）。
- 状态：数据恢复已完成；确认弹窗已随 Q-005 一起部署并做公网资源核对（见 Q-005 / Q-004 发布验证）。
- 同类初步审查：删除、作废、审批、撤回等入口中已有多处 `Popconfirm`/`Modal.confirm`；带原因或完整表单的动作不重复加弹窗。订单取消、合同作废等已有专门确认表单，暂不重复改；仍需按动作风险逐项核对，不能仅凭“直接 mutate”判断为缺陷。

### Q-001 合同删除后残留回签待办（高）

- 证据：`contract_documents` 软删除只改文档；历史服务器查询发现 10 条 `contract:signed-upload:*` 待办仍为 `pending`，对应文档 `deleted_at` 非空。
- 修复：删除合同时取消待办，恢复合同时只恢复由删除动作自动取消的待办；增加历史清理脚本。
- 验证：服务器已清理 10 条，`remaining_stale_pending=0`；后端健康检查通过。
- 提交：`12fe3f4`、`f934af7`。
- 残余：本地业务库当前为空/未启动，未有本地历史数据可核对。

### Q-002 新建线索选择“其他渠道”无法填写具体来源（中）

- 证据：后端 `LeadCreate` 已有 `source_detail` 字段，但前端新建线索表单只提交 `source`，选择“其他渠道”没有输入控件。
- 影响：用户只能保存笼统的“其他渠道”，来源信息丢失，后续无法区分合作伙伴、拜访等具体来源。
- 修复：选择“其他渠道”时显示必填说明输入，提交前校验非空；切换到其他来源时清空说明，避免旧值误提交。
- 验证：前端 `pnpm run build` 成功；提交 `f76f1c9`。

## 已排除

- 合同台账“只有一条”不是分页上限：当前接口已做真分页；本次截图的具体原因是已删除合同对应的待办残留与文档列表软删除口径不一致。

## 初步检查

- 已读取 README、测试入口、Git 状态、模块结构和现有回归说明。
- 后端单元/纯函数回归：`backend/.venv/bin/pytest backend/tests`，1197 passed in 12.69s。
- 前端生产构建：`pnpm run build` 成功；构建输出有依赖包 `lottie-web` 使用 `eval` 的供应链警告，以及两个约 580/638 kB 的大 chunk。这些来自现有依赖/打包配置；用户已决定保持现状，不拆包、不替换依赖。
- API 对账：`PYTHONPATH=. .venv/bin/python ../ops/api_gap.py`，文档去重后 574 条、注册路由 595 条，“文档有、代码没有”0 条；额外路由需人工区分内部/自动/第三方入口，不能据此认定缺功能。
- 纯静态搜索发现的 `pass` / 宽异常捕获暂不视为问题：多处有取消清理、外部集成或兼容性语义注释，需按调用链逐项验证。
- 初步阅读订单取消/回款链：订单取消入口按订单 ID 统计已确认回款、锁定可取消状态、驳回待确认回款并取消未结应收；目前未把静态阅读误报记为 Bug，仍需在隔离数据库验证并发与分配重算边界。
- 隔离库验证：`check_batch15_financial_lifecycle.py` 全部通过，覆盖来源并发核定、255 字凭证取消、月结替代生命周期；`check_data_scope.py` 全部通过，覆盖订单/回款、客户归属、批量接口、文件挂载、Agent、合同幂等和集成日志越权。
- 审批/文件针对性单测：`test_dingtalk_oa_state_machine.py`、`test_contract_dingtalk_oa.py`、`test_contract_oa_notification.py`、`test_file_access_authorization.py` 全部通过（68 tests）。静态核对前端路由与菜单覆盖，未发现已注册用户页面没有入口的明确缺口；通知、钉钉和文件的内部/自动接口不计作用户页面遗漏。
- 第三轮未确认新的修复项：钉钉提交状态区分 `not_sent` / `failed` / `unknown`，结果未知不自动重发；撤回结果未知会保留待核对标记；通知投递使用行锁和退避；现有针对性测试覆盖这些关键分支。
- 通知重试端到端验证：`check_notification_retry_e2e.py` 使用进程内假企微完成“首次失败 → 退避期不重试 → 到期补发成功 → 达到上限停人工 → 人工补投成功”，全部通过；两个隔离库随后已删除。
- 公海/客户归属隔离验证：`check_pool_scope_concurrency.py` 与 `check_pool_recycle_and_merge.py` 全部通过，覆盖私有客户/线索越权、部门范围、领取幂等与真实并发锁、公海回收预告、履约保护、批准前复核、恢复冲突、撞单裁定并发和分页；验证库随后已删除。
- 附件安全隔离验证：`check_attachment_business_auth.py` 全部通过，覆盖不存在/软删对象、模块权限与数据范围、上传/挂载/下载/预览、审计目标、原件保护、改名权限和删除后的附件可见性；验证库随后已删除。
- 前端静态检查：`pnpm run lint` 退出码 0，但报告现有 warnings（Fast Refresh 导出、effect 内同步 setState、`Date.now()` 渲染、一个 useEffect 依赖提示）。目前没有证据表明它们造成业务错误；逐项修复会改变组件生命周期或开发热更新行为，先列为待调查，不把 lint warning 当作已确认 Bug。
- 误报排除：检索输出一度看起来像打样附件锁定提示重复了 `category=supplement`；复读原始文件确认该字符串实际只出现一次，没有修改。

## 已决策

### Q-D01 前端构建体积与依赖 `eval` 警告（低/已决定保持现状）

- 证据：生产构建成功，但 `index`/`options` chunk 分别约 638/580 kB；构建器报告 `lottie-web` 直接使用 `eval`。
- 现状：没有证据表明这已经造成线上故障或安全利用；拆分 chunk、替换依赖或调整构建策略各有缓存、兼容性和维护成本。
- 不处理后果：首屏/相关页面下载体积可能偏大，依赖警告继续存在。
- 用户决定保持现状；本轮不拆包、不替换 `lottie-web`，后续只有出现实际性能、CSP 或安全扫描问题时再重新评估。
- 进一步核查：项目源码没有直接导入 `lottie-web`；它由 `@douyinfe/semi-foundation` 间接带入。对当前 `dist/assets` 构建产物搜索 `eval(` / `expression_function` / `lottie` 均无命中，说明当前警告来自依赖源码扫描，未证明运行时代码或用户下载产物包含这段动态执行逻辑。

## 待审查

- 已完成：确认交互、客户数据范围、公海回收、审批/文件访问、前后端路由闭环和主要并发/幂等回归。
- 待继续：资金/回款状态链、定时任务等需要隔离 PostgreSQL 的真实事务场景；当前机器没有可用本地 PostgreSQL，不能用生产库替代。
- Q-D01 已按用户决定关闭（保持现状）；其他发现先按“已确认 / 已排除 / 待调查”分类，不擅自改变业务口径。

## 下一步

在可用隔离 PostgreSQL 环境继续回款、订单状态和定时任务事务验证；同时保持已部署入口的回归覆盖。

## 同类“其他”选项复核（2026-10-19）

- 已核对客户来源、产品市场来源、报价发送渠道、订单收款方式、跟进方式：均已有对应的自由输入处理，且保存值会沿用用户填写的具体内容；未发现与 Q-002 相同的“选了其他却没有输入位置”遗漏。
- 样品申请的“样品情况”包含“其他”，但前后端都将该字段定义为固定选项（`Literal` / 既有数据库字段），当前只能保存“其他”。增加自由输入会改变接口校验、历史数据和统计口径，属于待人工决策事项，暂不修改。

### Q-D02 样品情况“其他”保持固定枚举（已决定）

- 用户决定：不增加自由输入。
- 原因：钉钉审批会读取该字段，保持固定枚举可以避免审批字段、历史数据和统计口径不一致。
- 状态：关闭决策项，不改代码；后续仅在钉钉模板或业务口径变化时重新评估。

## Q-002 部署记录（2026-10-19）

- 部署前验证：`frontend/pnpm run build` 成功；仍只有已决定保留的 chunk/eval 依赖警告。
- 发布方式：服务器 `/opt/crm-sales-agent/web/dist` 原子切换到 `/opt/crm-sales-agent/app/web-dist-20261019-lead-source`，旧目录 `/opt/crm-sales-agent/app/web-dist-20261018-1552` 保留作回滚。
- 首次验证不足：服务健康和 HTTPS 首页 200 通过，但只在发布目录确认了 `LeadListPage` 包含“其他渠道说明”，没有核对公网实际加载的 JS。后续用户反馈证实该发布未作用于公网（见下方纠正记录）。
- 说明：本次只发布前端 Q-002 修复，没有执行数据库迁移或生产数据写入。

### Q-002 公网发布目录纠正（2026-10-19）

- 根因：nginx 实际 root 为 `/opt/crm-sales-agent/app/web-dist`，此前切换 `/opt/crm-sales-agent/web/dist` 只影响 5173 静态服务，公网仍加载旧入口 `index-DZsN4AV3.js` 和 `LeadListPage-9PaXQHyV.js`；公网请求新线索 chunk 返回 404。
- 纠正：仅使用之前已授权的 Q-002 发布包，将静态资源复制到 nginx 实际 root（保留旧资源），最后原子替换首页入口。旧入口备份在 `/opt/crm-sales-agent/backup/lead-source-public-root-20261019/index.html`。未包含 Q-003 工作台修改，未重启后端或 nginx。
- 验证：公网 `/leads` HTML、入口 `index-lYV7IkRl.js` 和其引用的 `LeadListPage-DA-1vl4j.js` 均与发布包逐字节一致；公网线索 chunk 含“其他渠道说明”，SHA256=`4ef539457e1fa426ad58a1119a766093f8a544822514288fb65814e507b68d42`；后端健康检查数据库 ok。尚未进行浏览器实际操作验证，不将资源核对描述为浏览器端到端测试。
- 运维观察：服务器剩余磁盘约 355 MB（df 显示 100%），本次仅增加少量静态资源；未自行清理业务数据或历史备份，后续容量治理需单独评估。

## 本轮日志整理（2026-10-19）

- 证据：核对 2026-10-16 至 2026-10-19 的 Git 提交，确认本周已处理数据一致性、权限边界、并发幂等、审批闭环、文件安全和查询性能问题。
- 修复：本轮仅整理周报，没有新增代码修复；已确认修复以对应提交和既有回归记录为准。
- 测试：沿用本轮已记录的后端 1197 项回归和前端生产构建结果。
- 残余风险：回款与订单状态链、客户数据范围、审批撤回/定时任务仍需在可用隔离 PostgreSQL 环境继续验证；构建体积与依赖警告按 Q-D01 保持现状。
- 下一步：继续围绕高风险业务链路补最小复现，并把确认结果同步到周报和进度记录。

## 确认交互浏览器复核收尾（2026-10-19）

- 重新执行生产构建：`frontend/pnpm run build` 成功；仍只有已决定保留的 lottie-web `eval` 与大 chunk 构建警告。
- 使用独立 Chromium profile、本机内存 API 和本机静态构建目录复核确认行为；未访问生产数据库、未调用真实外部服务。工作台完成、通知补投、回收站恢复、任务单条/批量、线索领取、公海客户领取、审批通过/拒绝均通过：取消不写入，确认只提交一次且对象 ID 正确。
- 样品 7 个状态动作均通过：批准、拒绝（保留原因输入）、重新提交、寄样、签收、开新修订版、改判批准；每项都验证取消不提交、确认一次提交且 ID/动作/原因正确。
- 本轮没有新增产品代码修改；确认入口此前已部署到 nginx 实际目录 `/opt/crm-sales-agent/app/web-dist`。
- 剩余范围：报价撤回、系统设置批量动作、企微同步/绑定、回收站其他对象当前已有源码调用链和结构回归，但未在本机浏览器逐个点击；真实生产动作不会用测试脚本触发。

## 后续质量回归（2026-10-19）

- 重新读取目标文件与当前工作区后，执行后端隔离回归 `PYTHONPATH=. .venv/bin/pytest -q --disable-warnings`，全部测试通过（退出码 0）；没有使用生产库或演示库。
- 前端 `pnpm run lint` 退出码 0；仅保留已有 Fast Refresh、effect 内同步 setState、渲染期 Date.now 等 warning，当前没有新证据证明它们是业务缺陷。
- 对报价撤回、系统设置同步/启停/回收批量、企微同步/绑定、回收站产品/SKU/客户/样品/合同入口重新核对调用链：均能找到 Popconfirm、Modal.confirm 或已有原因/选人表单；本轮未发现新的裸高风险提交。
- 当前结论仍是“已确认的确认缺口已修复并部署”，不是“全项目不存在任何风险”。剩余事项属于逐入口浏览器覆盖和其他业务链路审查，不据此新增弹窗或改变业务规则。

- 本轮再次执行 `node --test ops/high_risk_confirmation.test.mjs`，17 个高风险入口 AST 回归全部通过，覆盖报价撤回、线索/公海领取、回收站产品与 SKU 恢复、系统同步/自动任务/账号启停、通知补投、企微同步/绑定、案例、新品洞察和 SKU 启停；未发现请求绕过确认回调的入口。

### Q-011 审批规则启用/停用缺少确认（中，已修复并部署）

- 证据：全局 mutation 入口扫描发现 `frontend/src/modules/approval/RulesPanel.tsx` 的规则 `Switch.onChange` 直接调用 `toggleApprovalRule`；该操作会改变后续报价审批是否套用规则，属于高影响状态变更。
- 修复：启用或停用前弹出确认框，显示规则名称及对后续审批的影响；只有确认回调才调用原 mutation，不改变后端规则或权限。
- 验证：新增 `ops/approval_rule_toggle_confirmation.test.mjs`，并与原 17 个高风险入口回归合计 18 项通过；前端生产构建通过；`git diff --check` 通过。
- 部署：已将前端静态资源原子发布到 nginx 实际目录 `/opt/crm-sales-agent/app/web-dist`；公网入口及 ApprovalPage、WorkbenchPage、QuoteDetailPage、SettingsPage、WeComPage 资源逐字节核对通过。备份 `/opt/crm-sales-agent/backup/confirmations-20261019-fTlDaG`，发布目录 `/opt/crm-sales-agent/releases/confirmations-20261019-plA1Xw`；后端服务 active、健康检查 database=ok，未重启后端、未写生产业务数据。

## 全局 mutation 入口复核（2026-10-19）

- 扫描前端模块中直接绑定 `mutation.mutate` 的按钮后，核对了订单推送、订单跟单计划、企微导入/交接、AI 动作、审批规则、客户联系人和样品表单等入口。
- 已排除的直接提交：订单 ERP/MES 推送由后端 `CONFIRM_REQUIRED` 与前端 `ConfirmRequiredModal` 双重保护；订单成交/交期计划在已有完整确认表单或受阻提示中提交；AI 动作按钮本身就是服务端 `awaiting_confirmation` 状态的确认/取消；企微导入先进入预览/确认流程；普通保存、查询、AI 试算和联系人主联系人设置不属于本轮高风险确认范围。
- 新确认问题仅有 Q-011 审批规则启停，已完成修复、回归和部署。本轮没有把静态匹配误报当成缺陷。

### Q-012 字典项启用/停用缺少确认（中，已修复并部署）

- 证据：系统设置字典管理中，启用/停用按钮直接调用 `dictionaryUpdateMutation`，会立即改变后续业务表单的可选项。
- 修复：仅对启用/停用增加确认弹窗，说明对后续表单和历史数据的影响；编辑名称、新建字典项等已有表单提交不重复加确认。
- 验证：新增 `ops/dictionary_toggle_confirmation.test.mjs`；审批规则、字典项和原 17 个高风险入口合计 19 项通过，前端构建和 `git diff --check` 通过。
- 部署：前端已原子发布到 nginx 实际目录；公网 ApprovalPage、WorkbenchPage、QuoteDetailPage、SettingsPage、WeComPage 逐字节核对通过。备份 `/opt/crm-sales-agent/backup/confirmations-20261019-GzdgVI`，发布目录 `/opt/crm-sales-agent/releases/confirmations-20261019-H18u8p`；服务 active、数据库健康，未写生产业务数据。

### Q-013 订单复购开新商机缺少确认（中，已修复并部署）

- 证据：订单详情“复购开新商机”原直接调用 `POST /orders/{id}/repurchase`，后端会创建新的商机和明细；误点可能产生重复业务记录。
- 修复：增加确认弹窗，确认后才调用原接口；不改变复购数据规则和权限。
- 验证：纳入高风险入口回归，审批规则、字典项和原入口合计 20 项通过；前端生产构建、`git diff --check` 通过。
- 部署：公网订单详情资源核对通过。备份 `/opt/crm-sales-agent/backup/confirmations-20261019-72WuJN`，发布目录 `/opt/crm-sales-agent/releases/confirmations-20261019-Figsnc`；服务 active、数据库健康，未写生产业务数据。

- 继续核对客户“撞单检查”、联系人设主联系人、订单草稿保存/生成需求单等直接 mutation：撞单检查是可重复的检测并复用已有裁定记录，联系人和草稿属于普通编辑/表单提交；没有把这些低风险动作强行加成确认，符合分级策略。

## 后端确认契约对账（2026-10-19）

- 对照高风险前端入口核查后端替代路径：三个 ERP 推送接口都统一调用 `ensure_push_confirmed`，未带确认不会写外部系统；审批规则启停保留权限、已发布版本约束并写审计；领取/恢复接口统一复用带行锁与数据范围校验的 service。
- 本轮没有发现可绕过前端确认、直接造成外部副作用或关键归属变更的后端入口。

## CI 回归登记补齐（2026-10-19）

- 发现 Q-011/Q-012/Q-013 新增的确认回归虽在本地通过，但 CI 前端 job 尚未登记审批规则启停和字典项启停两个测试文件。
- 已将 `approval_rule_toggle_confirmation.test.mjs`、`dictionary_toggle_confirmation.test.mjs` 加入 `.github/workflows/ci.yml`。
- 按 CI 前端测试组合执行，65 个 Node 回归全部通过；CI 文件登记自检和 `git diff --check` 通过。

- 最新 lint 退出码 0。新增/复核改动没有产生新的 lint warning；现有 warning（合同自动关联 effect 依赖、草稿/企微 effect 内 setState、Fast Refresh、Date.now 渲染等）已检查调用上下文，当前证据不足以证明业务错误，继续改动可能改变轮询或组件生命周期，列为待调查而未擅自重构。

## 当前审查状态更新（2026-10-19）

- Q-001 至 Q-013 的已确认问题均有修复记录；前端确认相关最新发布包含 Q-011 审批规则启停、Q-012 字典项启停和 Q-013 复购开商机。
- 当前可安全重复验证的本地结果：后端 pytest 退出码 0；前端生产构建、lint、git diff 检查通过；确认相关 Node 回归 65 项通过；公网资源与服务健康已在最近发布中核对。
- 仍不宣称项目整体审查完成：资金/回款、定时任务、复杂数据范围与合并链路需要继续在可用的隔离 PostgreSQL 环境执行；当前机器没有可用本地 PostgreSQL（`pg_isready` 无响应），也没有使用生产库替代。

## 凭据与敏感配置静态扫描（2026-10-19）

- 对 `backend/app`、`frontend/src` 和 `ops` 扫描高风险模式（真实 API key、长 Bearer token、代码内 JWT_SECRET 赋值），未发现命中。
- `.env.example` 和 README 中仅有空值/占位符；设计稿和文档中的示例链接未纳入运行代码。未发现可确认的凭据泄露问题。

## 服务启动与暴露面静态复核（2026-10-19）

- 生产默认 `DEBUG=false`，FastAPI docs 仅在显式 debug 时开放；CORS 使用配置白名单，不是 `*`。
- 高频高风险接口均在路由层使用当前用户/权限依赖；ERP webhook 配置和签名保护、DingTalk/Agent 等入口未发现匿名写入路径。
- 这轮为静态调用链复核，未把它等同于完整渗透测试；未发现可确认的新安全漏洞。

## 最新线上只读复核（2026-10-19）

- 服务器 `crm-sales.service` 当前 active，健康接口返回 `status=up`、`database=ok`，钉钉 OA 同步状态正常。
- 根分区可用约 3.3G、使用率 96%；当前仅做只读检查，没有清理或修改业务数据。
- nginx 实际目录首页当前引用最新入口 `index-Ihx7C4xb.js`。

## 磁盘残余只读复核（2026-10-19）

- CRM 目录约 1.6G：`backup` 891M、旧 `backups` 160M、`releases` 112M、`operations` 52M、`app` 373M，`backend.log` 13M。
- 本轮仅读取目录大小，没有删除。此前授权清理的旧 `.venv` 未被重新生成；当前仍有多份发布/备份目录，继续清理需要按回滚保留策略逐项确认，不能仅因磁盘 96% 就批量删除。

- 进一步列出备份明细：最大完整回滚包是 `backup/pre-deploy-20261016-182550`（235M）；`backups/` 主要是 10 月 15–17 日的功能测试/导入快照（合计 160M）；`releases/` 有多份约 4M 的重复前端发布包。未删除这些候选，等待明确的保留版本策略。

## GitHub 同步（2026-10-19）

- 已将本轮已验证修复、回归测试、CI 登记和质量记录提交为 `400a644`（`fix: harden CRM workflows and confirmation safeguards`）。
- 已成功推送到 GitHub `pending-acceptance` 分支；工作区提交后无未跟踪或未提交文件。
- `git ls-remote` 的额外只读核对受当前沙箱 SSH 网络限制，但 `git push` 已返回远端从 `f934af7` 更新到 `400a644`。

## 后端前端接口闭环复核（2026-10-19）

- 重新执行 `PYTHONPATH=. .venv/bin/python ../ops/api_gap.py`：文档接口去重后 574 条，实际注册路由 595 条，“文档有、代码没有”0 条。
- 额外注册路由仍需按内部接口、自动任务和第三方集成区分，未把数量差异误判为用户功能缺失。

## CI 静态守卫复核（2026-10-19）

- `check_model_attrs.py`：86 个 ORM 模型，未发现访问不存在列。
- `check_permissions.py`：代码引用权限码均存在目录；目录中 4 个未被代码直接引用的权限码已保留为可能冗余/待接线项，不据此认定功能缺失。
- `check_output_criteria.py`：扫描 2165 个函数，三类输出判据全部覆盖，0 个遗漏。
- `check_soft_delete_filters.py`：扫描 2165 个函数，统计查询软删过滤 0 个遗漏。

## 未直接引用权限码复核（2026-10-19）

- 静态权限守卫列出 4 个“目录中定义但代码文本未直接引用”的权限码。进一步调用链核对确认：`customer:contact_full` 在联系方式脱敏、搜索和 Agent 工具中集中使用；`product:review` 在新品洞察和询价评审服务中使用；`quote:approve` / `quote:finance_approve` 在审批前端入口、审批 access/service、规则会签和数据种子中使用。
- 这是集中常量/字符串间接引用导致的静态守卫提示，不是权限功能缺失；未修改权限目录或业务能力。

## 隔离 PostgreSQL 事务验证恢复（2026-10-19）

- 环境误判已纠正：本机 PostgreSQL 15 二进制已安装，系统服务未启动不意味着无法运行隔离测试。使用新建临时目录 `/tmp/crm-pg-audit-v0FOd2`、loopback 55432 和空库 `crm_iso_audit`，迁移到 head、生成专用测试夹具；独立测试后端监听 8073。外部消息推送及调度均关闭，健康接口确认 OA submission/sync/stream 全为 false。
- `check_batch15_financial_lifecycle.py`：退出 0，验证同来源并发核定只保留一笔正确金额、255 字凭证取消、交期计划关闭、月结协议替代/作废后旧提醒恢复。
- `check_scheduler.py`：退出 0，覆盖调度器装配/关闭、重复扫描幂等、报价过期提醒、约定下次跟进豁免及完成任务后的约定清空。
- `check_payment_consistency.py`：退出 0，覆盖修前写偏斜复现、重复登记请求键、应收计划并发只落一套、确认/驳回终态一致、币种继承与跨币种拒绝。
- `check_schedule_confirm_race.py`：13 条通过，真实行锁证明转移负责人和交期确认串行；旧负责人等待中的请求在锁内重判后被拒，交期未被错误改写。
- `check_order_payment_api.py`：退出 0，覆盖订单履约/取消、应收、回款确认和业绩归属，夹具清理完成。
- `check_data_scope.py`：首次 3 个旧合同夹具用例返回 401；查明 `_legacy_contract_fixture` 在测试进程验证实际 API 令牌，但测试进程未传与后端相同的 JWT_SECRET。对齐仅测试配置后原断言全部通过，未放宽断言或修改业务实现。它同时覆盖越权列表/详情/批量写、成本脱敏、合同幂等和文件原件保护。
- 证据日志：`/tmp/crm-audit-{migrate,seed,scheduler,payment,schedule-race,order-payment,data-scope,data-scope-aligned,api}.log`；初次失败日志保留，未隐藏。
- 收尾：已关闭测试后端 PID 62809 和临时 PostgreSQL PID 62333，确认无 postmaster.pid 后删除本轮临时集群。没有访问生产库、演示库或系统开发库，没有部署或修改产品代码。
- 本轮未确认新的产品 Bug，无新增待决策方案；此前“无法进行真实事务验证”的环境阻碍已解除。此结果只证明上述具名场景，不能推出全项目、全部资金/并发边界均已完成；余下逐入口浏览器和其他业务边界按新证据继续审查。

## 企微同步与绑定浏览器覆盖（2026-10-19）

- 选择此前仅有调用链/AST 证据的企微入口，扩展已接入 CI 的 `ops/confirmation_browser_smoke.mjs`。使用真实生产构建、独立浏览器 profile 和 loopback 内存模拟 API，不调用企业微信或生产数据库。
- 新增 5 个浏览器场景通过：同步部门、成员、外部联系人、跟进关系各自打开确认无请求，取消无请求，确认仅一次且命中对应 endpoint。
- 关联已有客户用两个候选验证第二行：弹窗显示客户乙，取消无绑定；确认后唯一请求携联系人 ID=92、客户 ID=94，没有误绑第一候选 93。
- 最新生产构建成功，浏览器整套原有场景与新增场景在同一次脚本执行中全部通过（退出 0）；`node --check` 和 `git diff --check` 通过。浏览器与内存服务在 finally 中关闭，临时 profile 已清理。
- 本轮无新产品缺陷或业务取舍，仅增强回归，故无需部署。仍未逐入口浏览器覆盖报价撤回、系统配置批量动作和回收站其他对象，不把模拟 API 的请求验证当作真实集成执行验证。

## 回收站恢复浏览器覆盖（2026-10-19）

- 在已接入 CI 的隔离浏览器脚本扩展产品、SKU、客户（原负责人在职）、样品、合同共 5 类恢复流程。每项验证打开确认不提交、取消不提交、确认只请求一次且目标路径/ID 正确；客户恢复请求保留 `owner_id:null`，沿用原归属。
- 增加边界夹具：`product_deleted=true` 的 SKU 无独立恢复入口，`removed_via=merged` 的客户无恢复入口，避免把“补确认”错误扩展为开放原本受限的恢复能力。
- `node ops/confirmation_browser_smoke.mjs frontend/dist` 整套旧场景和本轮新场景同次运行退出 0；语法及 diff 检查通过。使用本机静态构建及内存模拟 API，未触发生产恢复、数据库写入或外部调用。
- 本轮仅增强回归，无产品改动/新确认 Bug/待决策事项，无需部署。原负责人停用时的选人表单与恢复后真实数据链另有后端验证，本轮未新增其浏览器覆盖；报价撤回、系统设置批量动作仍待进一步浏览器验证。

### Q-014 CI 禁用状态说明检查失败（中，已本地修复）

- 用户反馈 CI 报错。GitHub 运行 39231186136 日志证实：生产构建、Node 回归和浏览器确认回归均通过，失败位于 `ops/check_disabled_hint.py`，具体是 NotificationBell 和 LeadListPage 的新增 `Popconfirm.disabled={mutation.isPending}` 未登记状态说明。
- 修复：请求进行时同一入口显式显示“补投中…”/“领取中…”；按既有 EXEMPT 契约登记这两处仅请求中暂禁用的原因。未关闭检查、未删断言、未放宽业务权限或确认要求。
- 验证：原检查本地先失败 2 个文件，修复后 39 个文件覆盖、0 遗漏；CI 前端步骤全部 15 个 Python 静态检查通过，生产构建通过，高风险/工作台回归通过。浏览器模拟延迟响应，验证进行中提示出现及完成后消失，并完整重跑原确认/取消与 ID 测试，退出 0。
- 远程验证：推送后跟踪新的 GitHub CI，未将本地通过当作远端通过；无数据库或后端改动。

#### Q-014 远端 CI 验收

- 修复提交 `e36899b` 已推送 GitHub。运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39232741498 最终 `completed/success`。
- UI 冒烟 job 全部通过（3m40s），包含此前失败的前端构建/禁用说明守卫；后端 job 全部通过（7m1s），包含 pytest、静态守卫和完整接口套件。
- 本地同组合 Node 回归 65 项全通过。保留了失败检查的复现与远端成功证据，没有跳过或弱化测试。
- 本轮未部署生产：修复的是 CI 契约及两处在途提示；生产数据库、服务配置均未修改。GitHub actions 自身运行时/runner 镜像有升级提示，但不是这次失败原因，不在本轮擅自升级。

## 报价撤回浏览器覆盖（2026-10-19）

- 扩展已接入 CI 的 `ops/confirmation_browser_smoke.mjs`，加入同一报价两个版本的夹具（当前 131、历史 130）。
- 浏览器验证：当前待审批版本打开撤回确认无请求、取消无请求；确认仅一次调用版本 131 的 `/withdraw-approval`，没有撤回历史版本。直接打开历史版本 130，完整页面加载后无撤回入口，撤回请求数保持不变。
- 整套浏览器脚本本次退出 0，含既有通知在途提示、任务/审批/样品、企微、回收站场景；语法及 diff 检查通过。真实静态构建 + 本机内存 API，未撤回真实审批、未访问生产数据库。
- 无新产品缺陷或需决策取舍，仅测试增强，无需部署。系统设置批量动作与特殊恢复选人分支仍未逐个浏览器覆盖；不能由此推定全项目质量目标已全部完成。

## 原负责人停用时恢复选人分支（2026-10-20，恢复中断前验证记录）

- 扩展 `ops/confirmation_browser_smoke.mjs`：原负责人停用的客户先进入选人表单；未选人点击提交不发请求；选人后取消无请求，重新打开清空已取消的选择；再次选择后只提交客户 106、新负责人 112，没有使用原负责人 2。
- 初次脚本点击选项后立即提交未观察到请求。加入等待下拉框显示已选人员的界面同步条件后，原有所有业务断言不变，两次完整浏览器运行均退出 0（日志 `/tmp/crm-recycle-owner-browser-debug.log`、`/tmp/crm-recycle-owner-browser-repeat.log`）。没有据此认定产品 Bug，没有修改业务代码。
- 额外复跑第一次被自动审批超时阻止，按工具允许仅重试一次，随后成功执行并通过；不是安全判定失败，无仍被阻塞操作。
- 本轮只补测试和记录，未连接真实数据库/企微，未执行生产恢复，无需部署。语法和 diff 检查通过；下一步转向用户选择的分页边界及系统设置动作，避免重复验证已经通过的同一场景。

### Q-015 恢复客户时选人仅覆盖前 200 名员工（中，已复现，用户批准按需加载后修复）

- 触发条件：原负责人停用，恢复客户需要重新指定负责人；在职员工超过 200 人，目标接手人不在第一页。
- 证据：`frontend/src/modules/recycle/RecycleBinPage.tsx:429` 固定请求 `status=active&page=1&page_size=200`；Select 的 `filter` 仅过滤已加载 items，未处理 total、分页或服务端 keyword。后端 `backend/app/modules/user/router.py:45` 支持 keyword 和 page，page_size 上限 200。
- 隔离浏览器复现：模拟 total=201、第一页返回 200 人、第二页含“末页在职员工”。实际请求记录仅 `["?status=active&page=1&page_size=200"]`；输入该姓名后显示“暂无数据”，预期能选到末页人员的断言失败。日志 `/tmp/crm-owner-pagination-repro.log`、复现脚本 `/tmp/crm-owner-pagination-repro.mjs` 保留。生产员工数量未查询，未声称线上当前已触发。
- 影响：无法把恢复客户交给后续人员，用户可能误以为员工不存在；未发现权限越界或数据丢失。
- 方案 A（推荐）：按姓名服务端搜索，保留选中值，限制候选量；可避免全量加载，但引入搜索等待/请求和选中回显处理。
- 方案 B：顺序加载全部分页后本地筛选；沿用现有操作习惯，但员工多时增加请求、下载与 DOM 渲染成本。
- 已向用户提供两种方案并等待明确选择，未改业务界面/查询策略。普通恢复选人验证已两次通过，增强脚本可以提交；此缺陷的失败复现未掩盖或记成通过。
- 同类候选：客户详情/列表、线索、企微等有固定 page_size 的选人查询；目前仅静态线索，未将这些入口全部记为已确认 Bug，后续逐个核查。

#### Q-015 用户决定与修复（2026-10-20）

- 用户明确选择：“按姓名也可以搜别的也搜出来，不加载全部，按需加载”。使用已有后端 keyword 能力（姓名/登录账号），未扩展手机号或部门搜索规则，未一次加载全员。
- 仅修改客户恢复选人入口：打开时请求 20 名在职员工，搜索输入防抖 250ms、按关键词分页，需要时点击“加载更多”；独立查询键隔离关键词结果。保留已选候选，取消/重新打开清空选择；加载失败提供重试。没有新增依赖、修改数据库或权限策略。
- 真实浏览器 + 内存 API 回归用 201 名在职员工、遵循 page/page_size/keyword 的模拟分页：初始仅第一页 20 人；姓名和账号都能查到第 201 人；点击才请求第二页；切换关键词及分页保留已选项；最终唯一恢复请求为客户 106 / owner_id=311。原有未选人拒绝提交、选择后取消、重新打开清空选择仍覆盖。
- 浏览器测试首次用 innerText 匹配高亮姓名被插入换行，后用 textContent 核对；搜索时控件隐藏触发器标签，改为检查下拉候选的已选状态，并用最终提交 ID 验证。未通过改产品代码迁就测试，失败日志保留 `/tmp/crm-owner-demand-browser*.log`。
- 本地生产构建、lint、禁用说明守卫、相关 Node 回归 30 项通过。未部署、未访问生产数据；其他固定页选人入口仍为待调查，不能把此处修复推定为所有入口已完成。

## 系统规则确认浏览器覆盖（2026-10-20）

- 在隔离浏览器回归增加系统设置规则入口 5 项：批量批准/暂缓/驳回各选择候选 141 和 143，打开及取消均不提交，确认只请求一次，携正确 decision 和精确 ID，完成后清空勾选。
- “扫描并生成回收预告”与“立即执行一次”各验证打开不执行、取消不执行、确认仅调用一次正确 endpoint。使用本机内存 API，没有执行真实回收扫描或自动规则。
- 独立设置场景完整运行 `/tmp/crm-settings-confirm-browser.log` 已退出 0；本轮没有由这些入口确认新产品缺陷，没有新增业务取舍。

- 本轮整套浏览器确认回归（含按需选人和上述 5 项设置动作）最终退出 0：`/tmp/crm-owner-demand-browser-selected.log`；语法及 diff 检查通过。此前失败为浏览器断言读取方式问题，未隐藏失败证据。


### Q-016 新建客户及批量转移选人截断（中，用户批准后修复，2026-10-20）

- 触发条件：员工列表超过 100 人，新建客户指定负责人或批量转移负责人时，目标员工在第一页之后。
- 代码证据：`CustomerListPage.tsx` 原两个查询均固定 `page=1&page_size=100`，Select 使用本地 optionMatcher，忽略 total 和后续页。不存在服务端搜索或加载后续页的其他入口。
- 修复前两个独立隔离浏览器案例均实际请求 `[{"page":"1","page_size":"100"}]`，输入“末页在职员工”后显示“暂无数据”，可选择该员工的断言失败。脚本及失败日志：`/tmp/crm-{create,batch}-owner-truncation-repro.{mjs,log}`。使用遵循分页的 201 人内存 API，不曾新建或转移真实客户。
- 用户先要求解释，未视为同意；解释后明确“可以，就这么改”，并重复批准“输入姓名或登录账号后，到服务器查找……每次显示20人，需要时再加载更多，不一次加载全员”。本次授权范围为上述两个客户入口，不扩展线索或其他页面。
- 新增共用 UserSearchSelect 供这两个已确认入口使用：20 人一页、姓名/账号服务端关键词防抖搜索、手动加载下一页、保留已选候选、错误重试；查询键按入口和关键词隔离，两个弹窗不会复用彼此搜索结果。保留员工部门展示并加账号，便于辨认同名人员。
- 保持原有用户状态筛选口径（未擅自新增 active 限制）、权限和提交契约：新建不选仍归自己，批量清空仍传 null 表示公海；原幂等请求键保留。共用控件 disabled 来自调用方，无分配权限的原因仍在新建表单紧邻说明，按既有静态守卫契约登记。
- 验证：生产构建、lint、禁用原因守卫、Node 相关回归25项通过；最终构建专项浏览器两入口通过，均验证姓名/账号找到第201人、点击才取第二页、关键词改变仍保留选择，最终 body.owner_id=311，批量精确 customer_ids=[9]，创建携 request_key。证据 `/tmp/crm-customer-owner-focused.log`。整套浏览器回归另行记录最终结果。
- 测试开发时，新增 GET /customers 模拟器起初用 endsWith 匹配，错误截获 /recycle-bin/customers，导致原回收站场景失败；已改为精确路径。未据此认定产品回归，失败日志保留 `/tmp/crm-customer-owner-browser.log`。没有放宽原业务断言。
- 本次没有部署或改生产数据。其他选人入口和网络失败/重试的逐场景浏览器验证仍未全面覆盖。

### Q-017 线索分配选人权限不匹配（中，已确认，用户选择B后修复，2026-10-20）

- 调用链证据：LeadListPage 的分配入口和选人查询按 lead:assign 开放；分配接口也接受 lead:assign；但实际 GET /users 依赖仅接受 user:manage/customer:assign。普通默认主管可能同时具备多权限，管理员也绕过检查，因此不能声称所有账号都遇到。
- 隔离 FastAPI 实际挂载用户 router，覆盖当前用户为非管理员、仅 lead:view/lead:assign，数据库依赖替换为空以防外部读写；线索分配权限 gate 通过，GET /users 返回40301，消息明确要求 user:manage/customer:assign。脚本及日志 `/tmp/crm-lead-assignee-permission-repro.{py,log}`。
- 页面复现脚本初次误点第一条“领取”链接，断言失败；尚未完成该场景的浏览器验证，不计为完整前后端复现。
- 未修改权限。不能直接给 lead:assign 放开完整 GET /users：该接口还返回手机号、邮箱、钉钉/企微身份、角色等，权限扩展存在数据暴露取舍。后续应先完成浏览器复现，再提交精简候选接口与扩大现有接口授权的方案比较，等待人工决定。

- Q-015 远端验收：提交 `b133a64` 的 GitHub CI https://github.com/ytsbx/crm-sales-agent/actions/runs/39353308854 已为 completed/success（本轮实际查询），不把本地结果替代远端结果。
- Q-016 完整确认浏览器回归 `/tmp/crm-customer-owner-full-final.log` 已退出0，覆盖原有所有确认场景及两个新增入口。后续去除共用控件新引入的 effect 同步 setState lint 警告，搜索/关闭重置统一走可取消定时器；重新构建及 lint 通过，最终构建回归另记日志。

- Q-016 最终构建整套浏览器回归 `/tmp/crm-customer-owner-release-browser.log` 已退出0，两个新增入口及全部原有确认场景通过。没有跳过或放宽断言；修改的共用控件无新增 lint 警告，项目已有其他 lint 警告仍保留。


## 合同客户选择框提示简化（2026-10-20，用户要求）

- 用户截图指出“服务端搜索，不止前200个”的提示多余且与所见候选数量不一致，明确可以不解释实现细节。
- 定位 `DocumentsPage.tsx` 上传合同客户选择框，将 placeholder 简化为“搜索客户名称”。仅修改文案，保留现有 keyword 服务端查询、权限及选择行为。
- 当前源码该查询 page_size=50、page=1；截图无法证明线上实际返回20条，不把用户观察与源码差异直接推断为后端分页缺陷。宽泛关键词超过50个结果时的进一步加载能力未在本次文案修正中擅自扩展。
- 这次是低影响可逆文案调整，不新建镜像实现的单元测试；使用生产构建与 diff 检查验证。服务器尚未部署。


#### Q-017 浏览器验证与待决策方案（2026-10-20）

- 修正隔离复现脚本定位方式：按链接文字精确查找“分配”，不再错误选择行内第一个“领取”。模拟非管理员用户仅有 lead:view/lead:assign，用户候选接口返回前述真实 router 已验证的403响应。浏览器确认分配入口可见、弹窗打开、实际请求 page=1&page_size=100、选人下拉显示暂无数据；日志 `/tmp/crm-lead-assignee-permission-browser-confirmed.log` 退出0。
- 页面没有显示后端拒绝原因：最初等待错误提示的复现超时，实际查询没有 onError/错误提示，而是将 data?.items 缺失映射为空列表。该超时保留于 `/tmp/crm-lead-assignee-permission-browser-fixed.log`，未将它隐瞒或当成测试通过。
- 影响限定：普通非管理员角色有 lead:assign、但没有 customer:assign/user:manage 时，合法分配业务无法在前端选择新的接手人。默认主管通常同时拥有这两类分配权限，管理员绕过 gate；未声称所有角色都受影响，未查询生产角色组合。
- 方案 A（推荐，已向用户提问）：新增受 lead:assign 约束的精简只读候选查询，返回在职员工 id、name、username、department，接入该入口并按姓名/账号搜索、20人一页按需加载，提供请求失败反馈。收益是完成已有分配能力并避免开放联系方式/集成身份等额外信息；代价是维护一个受限接口、前端查询及相应回归。现有 assign_lead 已检查目标必须存在且active，不自行改变可分配范围。
- 方案 B：扩展现有 GET /users 允许 lead:assign，维护改动少，但现有 serializer 同时返回手机号、邮箱、角色、钉钉/企微标识，造成权限可读范围增加，不能直接自主实施。
- 也可暂缓，代价是上述自定义权限组合继续无法通过页面选人。本轮只完成调查和提问，未修改任何权限、接口或用户角色。
- 排除误报：没有认定管理员入口普遍不可用；没有将错误点击“领取”的脚本失败认定为产品分配操作缺陷。UI验证为真实构建+内存API，后端权限为真实router+隔离依赖，尚非真实持久化分配端到端测试。
- 下一步：待用户选方案后才实施该项；其他模块继续独立审查。没有生产写入、部署或外部消息调用。

## 最近提交远端验收（2026-10-20）

- `50b5818`（新建客户/批量转移按需选人）运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39355000590 已 completed/success。
- 文案提交 `0c97a9d` 运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39355155011 查询时仍 in_progress，不声称最新提交已经通过。生产仍未部署本轮选人及文案修改。


## 附件通用读写权限链路调查（2026-10-20）

- 读取 file/router.py、access.py、service.py 和既有附件回归，确认下载/预览均经过 can_access_file → visible_object 的模块查看权及对象数据范围校验；不把“拿到编号即可读取”静态怀疑记为新漏洞。未在本轮声称已逐类业务穷举。
- 新行为证据：自建临时 PostgreSQL（仅临时Unix socket，端口55433，库crm_file_audit）、临时文件目录；使用真实路由函数、真实SQL/事务和文件操作。账号只有 file:manage/file:view/quote:view，没有 quote:manage；报价在本人范围，附件由另一账号上传、普通类别、单一报价关联、无其他持有人。visible_object(write=True)明确False，但 rename_file 和 delete_file 均返回code0，数据库文件记录/关联及临时磁盘文件被删除，审计实际含 rename/delete。
- 证据 `/tmp/crm-file-write-scope-repro.py`、`/tmp/crm-file-write-scope-repro.log`。通用改名/删除仅检查可见性与 file:manage，而上传、挂载、解绑使用业务写入授权；是否应统一，需要先说明权限能力收紧及共享文件的处理方案，不能直接替用户更改权限政策。本轮尚未修改产品代码，也未把业务口径未决的行为直接宣称为已批准修复。
- 初次测试用 Base.metadata.create_all 遇到重复索引，后改用项目 Alembic 升级空库head成功；第一次真实夹具未按用户→客户顺序flush触发外键，修正测试夹具顺序后原行为验证通过。失败日志分别保留 `/tmp/crm-file-write-scope-schema-attempt.log` 和 `/tmp/crm-file-write-scope-fixture-attempt.log`；没有放宽真实数据库约束或业务断言。
- 收尾：pg_ctl停止临时集群，确认无postmaster.pid后移除本轮 `/tmp/crm-file-audit.ywCpgy`；没有连接生产、演示、开发业务数据库，没有启动应用调度器或发送外部消息。
- 用户就Q-017询问为什么不推荐B，已解释现有完整员工列表会额外返回联系方式、角色和集成标识；B在用户明确接受该信息可读范围时可以选择，尚未视为批准任何方案。下一步等待Q-017决定，并另行提交附件读写口径的具体方案供确认。


#### Q-017 用户批准 B 与修复验收（2026-10-20）

- 用户询问B取舍后，明确选择“复用现有接口，维护简单，但允许读取完整员工资料。”据此只扩展 GET /users 的任一授权集合，加入 lead:assign；不提供新的精简接口，不修改完整输出字段，不开放员工创建/修改/角色管理权限。
- 修复前新增真实 FastAPI 路由+内存SQLite回归，lead:view/lead:assign账号GET /users预期200实际403，断言失败；日志 `/tmp/crm-lead-users-before-fixed-fixture.log`。初版夹具遗漏必填password_hash触发建库数据错误，补齐仅隔离夹具后才建立上述有效失败证据；初版失败日志保留。
- 修复后新增7个接口场景通过：lead:assign可读完整员工字段（手机号、邮箱、角色、钉钉/企微标识），姓名/账号搜索及20条分页查询正常；仍不返回password_hash；不能POST /users；无权限/仅lead:view/仅file:manage仍403；原customer:assign、user:manage、admin授权不受影响。新增测试 `backend/tests/test_lead_assignee_user_access.py` 由现有pytest CI自动发现。
- 与客户创建指定负责人权限、附件权限和存储边界合跑33项全部通过，日志 `/tmp/crm-lead-users-after-final.log`。非管理员浏览器模拟获准的员工接口响应，真实构建分配弹窗候选可见、选择112后仅提交一次owner_id=112，关闭弹窗，日志 `/tmp/crm-lead-assignee-permission-browser-after.log` 退出0；后端权限已单独由真实路由测试证明，未将模拟浏览器当成真实持久化分配验证。
- 本次仅按B修权限，保留线索选人现有首100条本地筛选交互；没有把此前对两个客户入口的按需加载授权擅自扩到线索入口。线索大员工量边界仍需另行核查和决策。本次没有部署或改生产角色/业务数据。

## 附件读写口径疑点排除（2026-10-20）

- 后续证据改变判断：`AttachmentPanel.tsx:69` 明确规定上传/挂载跟业务写权限，文件本身删除走文件管理权限；改名/删除按钮均按file:manage显示。`03-API-V1.1-接口设计文档.md:893` 明确改名只要求file:manage及文件可见，与真实后端行为一致。
- 因此上一轮复现的“无quote:manage但有file:manage且文件可见时可改名/删除”，属于当前显式设计口径，不能仅因它与挂载/解绑不同就断言为权限漏洞，也没有发现“前端禁止、后端放行”的差异。本轮将该疑点排除，不向用户要求无必要的权限收紧，不修改现有业务规则。
- 仍保留合同原件、多业务引用、打样制作依据的既有保护；本轮没有对这些判据作改动。已复读通用删除/解绑的保护调用链，未把静态阅读等同于各类别真实并发验证；继续保留未覆盖边界风险。

- Q-017 全量后端 pytest 最终退出0，1210项通过（`/tmp/crm-lead-users-full-pytest.log`），新增接口测试已纳入。没有访问生产数据库；本次前端代码未变，浏览器仅针对现有分配交互验证。


### Q-018 非 ASCII 错误签名触发异常（低，已复现并自主修复，2026-10-20）

- 触发条件：公开询价图片URL的sig、导入快照签名、企微回调签名含中文/重音字符/emoji，或ERP回调密钥header含非ASCII字符。四处 compare_digest 使用str参数，Python拒绝非ASCII str并抛TypeError。
- 修复前验证：新增 `backend/tests/test_signature_input_boundaries.py`，15项初始用例中11项失败；公开图片真实FastAPI请求（隔离ASGI、不连网/数据库）实际返回500而非预期403，其余三处真实校验函数抛未转换的TypeError。日志 `/tmp/crm-signature-boundaries-before.log`，未弱化错误码断言。没有声称鉴权被绕过、文件泄露、整个服务崩溃或线上已被利用。
- 最小修复：四处以UTF-8字节传入常量时间比较，固定十六进制摘要编码为ASCII字节。错误签名继续使用原403/422/WeComCallbackError，签名算法、密钥、权限、有效期及合法输出不变；无依赖、DB结构或额外运行服务。行为唯一明确、没有业务口径取舍，符合自主修复条件。
- 相关验证：与公开图片安全MIME、合法图片、导入快照/导入执行和ERP状态回调既有测试合跑80项通过（`/tmp/crm-signature-boundaries-after.log`）；再补正确签名但已过期的图片拒绝用例。完整后端pytest 1226项通过（`/tmp/crm-signature-boundaries-full.log`），新增16项自动由CI发现；错误签名不得读数据库、合法签名与原错误码均有断言。
- 同类核查：检索backend/app全部compare_digest调用，仅4处，已逐一复现/修复；未引入仅为重构的统一封装。前端、生产配置、真实库/文件和外部回调均未触碰，无需为本次后端异常处理修改运行前端构建。
- 排除与边界：ASCII错误签名原本已正常拒绝，不能笼统说所有错误签名都有问题。现有合法图片/签名测试通过，不推出所有公开文件或第三方回调链路已全部审查。尚未执行外部企微/ERP真实投递，本轮仅本地真实函数及隔离HTTP验证。
- 下一步：推送后核对远端CI；继续其他独立高风险链路，保持质量目标未完成。本次不部署生产。

- Q-017 远端CI运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39357901301 本轮查询仍in_progress，不把本地1210项结果替代远端最终结果。


### Q-019 询价状态索引重复声明阻断模型建表（低，已复现并自主修复，2026-10-20）

- 沿前轮PostgreSQL建库失败证据继续调查：`CustomInquiry.__table_args__`显式Index(ix_custom_inquiries_status,status)，status列又index=True，SQLAlchemy因此注册两份同名同列非唯一索引。不是正常业务查询失败，也未证实当前生产迁移失败。
- 修复前最小回归：独立内存SQLite创建真实CustomInquiry表，报OperationalError index ix_custom_inquiries_status already exists；日志 `/tmp/crm-inquiry-index-before.log`。此前真实PostgreSQL Base.metadata.create_all也报同名DuplicateTableError，见 `/tmp/crm-file-write-scope-schema-attempt.log`。
- 同类核查：加载所有业务model.py，逐一按schema+索引名分组，本轮只发现这1组重复，两份的字段及unique属性完全相同。Alembic历史迁移b8f4a6d2c0e9只创建一次该索引。
- 最小修复：只去掉status列多余的index=True，保留显式Index及名称、字段、非唯一属性。没有删除业务索引或更改数据库实际结构，不新增迁移、不连接现有库、不改查询或状态规则；因此属于重复声明的确定性消除，无额外资源/兼容取舍。
- 回归：新测试实际内存建表成功、目录确认一个status非唯一索引并保留customer_id索引；询价相关19项通过（`/tmp/crm-inquiry-index-after.log`）；完整pytest 1227项通过（`/tmp/crm-inquiry-index-full.log`）。
- 额外真实PostgreSQL验证：新空库crm_index_audit，只监听临时Unix socket/55434；全部104张已注册模型表create_all成功，pg_indexes只返回单份CREATE INDEX ix_custom_inquiries_status ON public.custom_inquiries USING btree(status)。日志 `/tmp/crm-index-metadata-postgres.log`。这不等于验证模型结构与迁移结构全量一致，迁移仍是正式建库入口。
- 收尾：确认临时集群停止、无postmaster.pid后移除本轮临时目录。没有修改生产/演示/开发库，没有部署。本轮前端无改动，不进行无关浏览器重复回归。

## 远端CI发现（2026-10-20）

- 运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39357901301 （dd373b3）已completed/failure，正在下载具体失败日志；不把本地1210项通过表述为远端通过。
- 签名修复7d98c76运行 https://github.com/ytsbx/crm-sales-agent/actions/runs/39359273742 查询时仍in_progress，尚不声称成功。


### Q-020 浏览器等待器读取尚未创建的正文时抛错（低，已复现并本地修复，2026-10-20）

- CI证据：dd373b3运行39357901301的UI job在报价当前版本撤回场景通过后，切换历史版本并执行waitFor(document.body.innerText...)时出现Error:Uncaught，位置ops/confirmation_browser_smoke.mjs:378。原evaluate丢掉exception对象，仅保留text，因此远端日志不足以断言完整具体异常；未把它归因于Q-017业务权限或产品页面Bug。
- 最小复现：浏览器about:blank移除body、200ms后建立body，原等待器立即TypeError Cannot read properties of null(reading innerText)，不能等到就绪。日志 `/tmp/crm-browser-body-wait-before.log`。使用独立浏览器profile、本机内存API，未访问真实业务服务。
- 修复：waitFor正文尚无body时继续既有有界等待，不吞其他异常；evaluate保留实际异常description和表达式以便远端定位；历史报价增加目标URL及document.readyState条件，避免旧页面同名报价标题被误认成新页面就绪。所有取消无写、确认单次请求、精确ID/版本/权限相关断言保留，没有skip或重跑整套来伪造通过。
- 验证：把无body窗口作为同一脚本的前置回归，输出BROWSER_NAVIGATION_BODY_WAIT=true；完整确认浏览器回归包括历史版本、回收站选人、规则批量操作和客户按需选人全部退出0，日志 `/tmp/crm-browser-body-wait-after.log`。Node语法及diff检查通过。业务前端代码没有改动。
- 剩余风险：已证实等待器的一种故障并修复；远端原异常的完整细节缺失，不声称已经证明CI所有失败都由它造成。必须以本次推送后的新CI为准，若仍失败用增强诊断继续调查，不能仅凭本地通过宣布远端修复。


## 人民币唯一业务币种再次确认（2026-10-20）

- 用户明确纠正：“根本就不会有外币这种东西”“之前设计的一个就是不是人民币直接拒绝”“项目只能接受人民币”。将此固化到AGENTS.md及QUALITY_GOAL.md，后续不得主动兼容外币或把外币可显示当作产品改进。
- 本轮起初在回款通知中对手造USD/EUR/HKD夹具验证固定¥文案，误把代码残留的外币字段/继承能力当成业务需求，没有先完整核对trade_mode约束。该判断不符合项目实际规则。外币通知文案及对应4项新增用例均已精确撤回，两个业务/测试文件与HEAD一致，未提交、未部署、未发送真实通知。
- 已复读core/trade_mode.py，ensure_currency_allowed固定白名单CNY，配置为export也不得放行；建报价、手工建订单、草稿更新币种已有调用。test_round4_review_fixes.py通过，ops/check_foreign_currency_rejected.py通过，证明上述已覆盖校验及审批上下文接线未失效，不能以此推出全项目所有入口已充分验证。
- 待调查：trade_mode历史说明仍保留“继承/ERP不拦”等旧例外，resolve_payment_currency及部分源单继承路径也有历史外币兼容。用户此次明确的人民币唯一约束优先于旧说明。后续须逐入口验证是否能实际写入/确认非CNY，确认缺口后按人民币规则补拦截，不通过新增外币展示掩盖；不清洗、换算或覆盖生产历史数据。
- 回款后端现有状态只有pending转confirmed/rejected，未将“已确认款没有随手撤销入口”认定为Bug或补新功能。本轮先停止外币通知方向，继续人民币金额与数据一致性审查；没有生产改动。


## 品牌图标视觉方案（2026-10-20，用户选择A后完成本地替换）

- 用户反馈当前蓝底白色日出图标像灯泡、不美观。定位为 frontend/public/tianchu-logo.svg，侧栏、登录页、favicon共用；没有把其他残留Vite图标误认为当前线上图标。
- 准备A（TC字母）、B（天字徽标）、C（增长折线）三版原生SVG，保留蓝色，附侧栏30px及标签页16px效果；独立本机浏览器渲染并查看对比，预览 `/tmp/crm-logo-options/preview.png`，SVG在同目录option-A/B/C.svg。
- 已向用户询问风格选择，推荐B。正式图标、产品界面和服务器尚未修改，不将视觉选择代为决定。初次临时预览脚本字符编码失败，改用显式Unicode字符串后渲染成功；未新增依赖或使用外部素材。


### TC图标实施验收（2026-10-20）

- 用户明确选择“A：TC字母”。按预览稿替换 frontend/public/tianchu-logo.svg，侧栏/登录页/浏览器favicon继续共用同一资源；三个引用统一加?v=tc1，避免旧图标缓存导致替换后仍显示日出图形。
- 生产构建通过。隔离浏览器加载实际dist检查登录页和侧栏图片complete/naturalWidth、实际currentSrc版本及favicon链接均通过；实际侧栏截图 `/tmp/crm-tc-logo-sidebar.png` 已查看，日志 `/tmp/crm-tc-logo-browser.log`。没有新增依赖或业务逻辑变更，不跑无关资金回归或新增镜像测试。
- 仅本地及GitHub同步，未部署服务器。人民币唯一业务约束保留，图标修改与金额/币种规则无关。

### 用户授权部署完成（2026-10-20）

- 用户明确要求“部署”，发布业务代码版本 c329c71ae312afb47420d3f8e725a7e6d62dc32e。包括TC图标及此前已完成回归的Q-017、Q-018、Q-019修复。既有完整后端回归1227项通过，前端构建及图标浏览器验证通过；GitHub CI运行39363146036成功。
- 更正此前中断判断：最初远程脚本以grep查找SVG中的字面TC，因图标使用path绘制而提前退出，发生在后端替换/重启之前；不是服务重启失败。改用SHA256验证。前端已切换至新版，公网首页和SVG分别匹配本地576e29b9…、5e45e012…；公网入口JS、CSS也与本地完整SHA256一致。
- 本轮重新核对发现后端仍旧版，因此继续发布后端。首次打包包含macOS元数据，发布前AST检查拒绝含空字节的元数据文件，未停止服务或替换后端。重新打包排除macOS元数据、pycache及pyc后，238个Python源码AST检查通过。
- 后端requirements.txt与服务器SHA256一致，未安装依赖、执行数据库迁移、修改生产业务数据或正式配置。停止服务后备份旧app并替换源码、重新启动，配置健康检查失败自动回退。关键user/router、core/importing、inquiry/model、erp/router、file/router、wecom/callback六文件SHA256均与本地一致，DEPLOYED_COMMIT记录上述版本。
- 最终验证：crm-sales.service active/running、NRestarts=0；本机8010/health返回status=up、database=ok，钉钉sync_running=true、stream_connected=true。启动初期Stream尚未连接，随后复查已恢复。公网/health实际为前端SPA回退页面，不能作为后端健康证据，后端健康以8010真实JSON为准。
- 保留旧前端缺失的哈希assets，降低旧标签页切换时找不到资源的风险。回退目录：前端/opt/crm-sales-agent/backup/20261020-123849-c329c71b；后端/opt/crm-sales-agent/backup/20261020-143100-c329c71-backend。更早完整备份/opt/crm-sales-agent/backup/20261020-120535-c329c71保留；未清理历史备份。
- 剩余边界：未在生产执行写入式业务回归或真实外部消息；健康检查和资源校验不代表所有业务链路无缺陷。人民币全入口校验调查仍未完成，长期质量审查继续按前文待调查范围推进。

### 部署状态冲突复核（2026-10-20 14:51）

- 用户贴出“后端仍于10-19 13:26启动、24文件不同”的另一份记录，询问差异原因。本轮只读复核，未再次发布或重启。
- 当前实际ExecMainStartTimestamp为2026-10-20 14:31:02 CST，service active/running，DEPLOYED_COMMIT为c329c71；按本地全部238个后端Python源码逐一计算SHA256并在服务器比对，checked=238、mismatches=[]，此前列出的24处差异当前均不存在。
- 实际健康JSON确认数据库ok、钉钉同步运行且Stream已连接。用户贴出的旧启动时间不能描述14:31发布后的当前状态；该记录原始采集时间未提供，不能断言另一会话何时执行了检查。此前确实发生前端已更新而后端未更新的中间状态，现已在后续发布中补齐。

### 报价删除入口核查（2026-10-20，待用户决定交互）

- 用户截图报价列表Q202610150001草稿无删除操作。代码核查：QuoteListPage没有删除列，QuoteDetailPage仅有费用/明细删除，shared/api/quote.ts没有整单删除封装；后端quote/router.py:401已有DELETE /quotes/{quote_id}，要求quote:manage及报价可见范围，关联任何订单即拒绝，成功后软删除并保留历史版本、审计。
- 已确认前后端入口对应缺口；未对截图对应生产报价查询订单关联或试删，不能仅凭草稿标签保证该单可删。当前没有执行生产写入。
- 待决定：建议在报价详情补整单删除及二次确认，沿用现有权限/订单关联限制。新增操作入口涉及交互选择，依照用户规则先询问，尚未修改业务代码。

### 报价详情删除入口实施（2026-10-20，用户批准后）

- 用户回复“可以”，批准在报价详情补整单删除和二次确认。详情头部增加删除报价按钮，仅quote:manage可见；封装并调用既有DELETE /quotes/{quote_id}，没有修改后端权限、状态或订单关联限制。
- 确认框显示报价单号，说明删除的是整张报价及所有版本，历史版本和审计保留、关联订单不能删除。取消不发送请求；等待期间禁用确认/取消及关闭，避免重复提交；成功提示并返回报价列表，失效报价列表及客户报价缓存；失败显示后端原因并保留当前页面和确认框。
- 修复前：在既有隔离浏览器回归新增整单删除用例，旧dist运行至删除入口等待失败（/tmp/crm-quote-delete-before.log），之前撤回审批/历史版本检查正常通过，证实缺少入口。
- 修复后：TypeScript及生产构建通过（/tmp/crm-quote-delete-build.log）；完整既有浏览器确认回归通过，新增验证取消零DELETE、后端关联订单拒绝时仍留详情、历史版本页删除仍提交正确quote_id、等待期按钮禁用、连续点击仅提交一次、成功返回列表（/tmp/crm-quote-delete-after.log）。使用独立浏览器profile及本机内存模拟API，未连接生产数据库或真实外部接口。node语法、git diff --check通过。
- 无新增依赖、数据库或后端变化。本轮仅完成本地实现及验证，尚未部署，也未删除用户截图中的真实报价。浏览器错误响应为模拟后端既有保护，不能声称已核实该真实报价是否关联订单；后端并发与软删后的所有派生链路未在本轮作新的全面验证。

### 报价删除入口发布（2026-10-20 15:30）

- 用户要求最新代码同步GitHub并部署。实现、回归测试及此前部署记录已完整提交842e384208d02a511159788024e411d75f2d4183，推送github/pending-acceptance；ls-remote确认一致。
- 后端全部238个Python源码重新只读比对，本地与线上0差异，本次只更新前端，不重启后端、不安装依赖、不迁移或清理数据。报价成本/核价讨论没有产生新的代码改动。
- 上传去除macOS元数据的构建包，校验index后切换web-dist，保留旧哈希assets以兼容旧标签页；回滚备份/opt/crm-sales-agent/backup/20261020-153058-842e384。WEB_DEPLOYED_COMMIT记录842e384，后端DEPLOYED_COMMIT继续保留c329c71以明确各自发布记录。
- 完成验证：线上118个本次构建文件SHA256逐一匹配本地（mismatches=[]）；公网index SHA256为21f43cec0682311f34eb377cfe68e8b4165fc4170b31b7944260d483fc64d64f，包含删除入口的QuoteDetailPage-DjAOuJ2R.js公网内容也匹配本地。crm-sales.service active，实际8010健康JSON数据库ok、钉钉sync_running/stream_connected均true。
- GitHub CI运行https://github.com/ytsbx/crm-sales-agent/actions/runs/39386620059：本轮最终查询UI冒烟success、后端回归仍in_progress，未把它表述为完整远端CI通过；本地构建及完整确认浏览器回归此前已通过。本次未在生产试删真实报价，用户可刷新后进入报价详情查看入口。

### 无成本报价与引导缺口（2026-10-20，待规则决定）

- 用户反馈未填写成本仍可报价，且缺少提示导致学习成本高。代码证据：pricing/service.py明确警告“无生效成本…按已维护售价报价不受影响”；quote/service.py的submit_for_approval在无成本、未低于独立保护价时设置approval_required=False。这是当前明确放行规则，不能用“核价自然会防止亏损”解释实际能力，亦不能仅凭静态代码把整条生产发送链路记为已复现Bug。
- 完全无成本与已有成本但缺项走不同判据，后者已有人工审批逻辑。当前未对生产尝试提交/发送、未修改真实报价或成本。
- 用户进一步指出缺少可见提示。报价详情目前未见对无成本状态的常驻业务引导；仍需以隔离浏览器验证实际返回/显示链路，不将静态搜索直接视为完整UI复现。
- 待决定方案：建议允许存草稿，同时明确显示未维护成本、缺失SKU及维护入口；成本未完整确认时禁止正式提交/对客发送和导出/转订单，不能由免审绕过。真实为零与未提供须区分。该方案改变当前报价放行规则与交互，先请求用户批准，再逐入口复现、实现和回归；不擅自变更历史单据或清洗生产数据。

### Q-021 成本完整性作为正式报价前置条件（2026-10-20，用户明确批准）

- 用户对“草稿可存，成本未补齐前禁止正式提交、发送、对客导出和转订单；常驻提示并提供维护入口；历史快照不自动改写”的完整方案回复“可以”。这是获批的业务规则变更，不把旧A06/D5明确放行逻辑误报为代码意外；新规则已固化QUALITY_GOAL.md。
- 修复前证据：新增内存SQLite回归运行4项，完全无成本、部分成本的真实submit_for_approval均未抛异常（2项失败）；完整成本及显式零成本正向2项通过。日志/tmp/crm-cost-gate-before.log。未在生产尝试提交/发送真实报价。
- 后端公共成本校验按报价自身成本快照完整性判定，完全无成本、缺项、历史完整性未知及矛盾标记均拒绝，明确已完整的零成本允许。校验接入提交审批（在免审规则之前）、正式发送、邮件登记、PDF及别名、转订单、Excel生成、报价存档下载和通用文件下载/预览；文件入口反查报价原件，避免绕过。不存在明细的报价不能正式导出。拒绝发生在正式状态/发送记录/文件写入之前；既有发送重试仍保持幂等，实际再次发送不能绕过。
- 详情返回不含金额的cost_problems，报价页持续显示缺哪些SKU、维护入口及下一步，无price:manage仅显示状态/联系管理员，不额外开放成本金额。成本未知时不显示伪零成本或伪利润。产品/SKU列表以批量查询返回成本状态（只含状态/标签），识别未维护、缺项、过期/未来/停用和同日冲突，不逐行N+1。维护入口直达价格中心的成本页及对应SKU；成本输入文案改正为“留空表示未提供，确实没有填0”。
- 补完成本不会自动覆盖报价：增加受现有quote:manage及数据范围约束、锁定报价/版本的显式草稿cost-refresh操作与确认，更新成本、利润和底价判定，保持成交单价、数量、主数据快照和价格来源；定制明细不自动改写，已锁定版本拒绝刷新并指引新建版本。维护成本后失效产品成本状态缓存。
- 回归：backend全量1251项通过（/tmp/crm-cost-gate-full.log），含新增24项；覆盖8个正式出口、历史未知/矛盾快照、已免审版本拦截、完整零成本、显式刷新保价及锁定保护、产品生效日期/停用/冲突状态、不含成本金额的提示。前端TypeScript及生产构建通过（/tmp/crm-cost-gate-build.log），24项quote-todo测试通过（/tmp/crm-cost-gate-todo.log），完整隔离浏览器回归通过（/tmp/crm-cost-gate-browser.log），新增SKU状态/准确维护链接、成本提示、取消无写/确认更新、无权限引导和未知利润文案验证。pyflakes、模型列/权限码、输出判据及查询JOIN静态检查通过。
- 按新业务规则更新既有“缺项可进人工审批”的断言为严格拒绝并验证不产生审批记录，没有删除或跳过回归；文件冻结/幂等用例补明确完整成本快照。真实PostgreSQL15全新隔离库迁移和种子成功后，check_quote_api、check_quote_lifecycle、check_quote_center_acceptance、check_batch7_repairs四套HTTP回归通过。首次lifecycle的空单夹具因新规则被拒，补已完整核价的明细并保留原状态/并发断言后，在另一新库全部通过。日志/tmp/crm-cost-gate-integration.log及/tmp/crm-costgate-pg.0wqnpH/；两个临时集群和本机测试后端均已停止，关闭了钉钉/企微推送、OA Stream及定时任务，没有外部实际发送。
- 残余边界：未改生产数据/配置、未部署，不新增数据库迁移或依赖；未知完整性的历史报价可能需要新建版本核对成本，已有下载到外部的文件无法撤回。尚未验证全清单的全部HTTP套件或生产各角色实际操作，不声称全项目没有问题。后续同步GitHub并核对远端CI，生产发布需明确授权。
- 收尾：业务实现提交307d07f已推送GitHub pending-acceptance。隔离HTTP脚本自动改写了仓库既有验收JSON（含时间和随机测试编号），该非业务产物已恢复本轮前内容，避免把一次测试输出混入功能改动；测试日志另行保留。确认两个集群停止后移除本轮新建的两个临时data目录，保留日志，不保留约108MB的一次性数据库数据。现有验收套件仍有旧外币快照测试说明，与人民币唯一规则不一致，登记为此前CNY遗留调查的一部分；本轮没有因此新增外币产品能力或修改生产币种数据。

### Q-022 SKU编码可选及名称优先（2026-10-20，用户明确批准）

- 用户确认四点：新建编码可留空自动生成；公司已有编码可手填；列表/选择框名称规格优先、编码辅助；不改已有编码、不放松资料完整性。另行提示CSV空编码重复导入风险后，用户明确选择本次保持CSV编码要求，仅放开页面及新建接口，不改ERP/外部身份匹配。
- 修复前：两个新建接口的真实内存SQLite回归分别传省略/null/空串/空白编码，8项均失败（前者schema必填/非空限制、空白字符串则原样存入），日志/tmp/crm-sku-auto-before.log。页面还强制编码必填；表单已有名称输入却未在创建payload传name，沿完整创建链确认后本轮一起补齐。
- 后端共用新建编码分配器：未填写时使用标准库UUID生成内部编码，在已有/已软删SKU集合检查冲突，数据库原全局唯一约束仍兜底；手工编码沿用重复拒绝，两个入口仍先锁产品、校验产品有效并写创建审计。只在新建分配，不批量改旧记录，不允许PATCH清空旧编码，无依赖/数据库迁移。名称、规格长度与物理量校验及报价资料确认流程保持。
- 页面新建编码提示“有公司编码可填写，留空自动生成”，新增payload带name。产品SKU名称置于首列，编码次级小字；报价、订单、商机、样品、价格规则、分析及回收站相关列表改为名称在前、编码辅助。7个主要选品入口共用skuLabel，依次显示产品名/规格名称/规格并去重，完整编码留在末尾，继续支持原编码搜索与ID选中，不改变关联数据。老记录缺少名称时保留编码作识别，不伪造资料。
- 验证：新增13项后端回归（两入口4种空编码、手工重复、原码保留、软删码避让、资料长度/负值校验），全量1264项通过（/tmp/crm-sku-auto-full.log）；新标签3项及全部前端逻辑合计100项通过（/tmp/crm-sku-auto-node.log），TypeScript/生产构建通过（/tmp/crm-sku-auto-build.log），完整隔离浏览器回归通过，新增空编码创建、名称规格payload正确、名称在编码前展示（/tmp/crm-sku-auto-browser.log）。新HTTP脚本check_sku_auto_code在独立PostgreSQL15新库/本机后端验证16并发请求跨两API编码唯一、手工编码编辑保留/重复拒绝、异常资料仍拒绝、CSV空编码报错且不建行，通过日志/tmp/crm-sku-auto-integration.log。脚本和前端测试均登记CI，静态登记检查、pyflakes app、新脚本及diff检查通过。
- 边界：CSV无编码导入防重复方案留待后续，不提前实施；既有SKU的name/specification可空规则没有擅自改成所有规格必填，产品名称兜底显示及正式对客资料确认仍有效。未修改生产业务数据或配置、未部署，不能将隔离回归视为所有外部集成已验证。

### Q-021远端失败补充处理（本轮发现并复验）

- 前批307d07f的CI运行39392402487已failure：UI检查发现cost_refresh审计动作漏登记/中文名；后端9个套件失败，主要是既有正常流程夹具使用空报价或未知完整性成本，另有失败夹具残留连带影响人员筛选/残留检测。没有把前轮本地通过当成远端通过。日志/tmp/crm-cost-gate-ci-failed.log。
- 补齐后端审计中文名真源并重新生成前端、价格历史动作映射及EXPECTED_ACTIONS清单。7个受影响套件的正常路径补明确已完整核价的历史快照/明细，保留全部权限、状态、并发、文件冻结、事务回滚断言；并补新明细的外键模型注册和先明细后版本的测试清理顺序，不放宽成本校验、不跳过失败套件。
- 在新隔离库本地复验：check_biz_docs、check_demand_flow、check_order_drafts、check_biz_doc_freeze、check_biz_doc_concurrency、check_round2_risk_repairs、check_user_list_filters、check_fixture_residue均通过；check_opportunity_gates补外键注册与清理顺序后在另一新库通过。日志/tmp/crm-sku-cost-ci-recheck.log、/tmp/crm-sku-cost-ci-final-check.log；审计动作和生成物检查通过。全144套件尚未在本轮本地重跑，以新推送后的远端CI为准，当前不声称远端失败已最终消除。

### Q-023 删除误导性的“本月成交冲刺机会”快捷按钮（2026-10-21）

- 用户确认只删除工作台/AI 助手中的该条快捷按钮，不删除 AI Sales Agent 卡片、历史会话或其他入口。
- 代码证据：工作台按钮和 CopilotDrawer 工作台快捷问题都会直接发送“分析本月成交冲刺机会（年月）”预设提问；该内容只是预设 AI 提问，不是真实统计指标，容易造成误解。
- 修复：移除两处该快捷按钮/快捷问题，保留其余 AI 入口；同步调整 `ops/smoke_ui.mjs` 使用剩余的工作台 AI 问题验证打开、失败重试、无写操作和不跳转行为。
- 验证：本地生产构建通过，`node --check ops/smoke_ui.mjs`、`git diff --check`通过；提交 `72775c355de9203322e90bb6e9b6841d42f8982f`。构建仍有既有 `lottie-web`/`eval` 警告，不影响构建，本轮未处理该已有取舍。
- 发布：未等待 GitHub CI；发布包SHA256为 `c7593db0e5420a628688b8dc615f1f2b0ca50bd67acceb5f66d0f251cba43d99`。服务器前后端均已切换到 `72775c3`，备份目录为 `/opt/crm-sales-agent/backup/unified-72775c355de9-tookf2zj`；服务 active、NRestarts=0，健康检查数据库/钉钉同步/Stream正常，公网HTTP 200。
- 影响范围：未修改数据库、依赖、业务数据或权限规则；旧的工作区文档删除状态仍是此前已有状态，未纳入本次提交。
