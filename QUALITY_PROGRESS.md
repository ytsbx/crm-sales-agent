# CRM 质量审查进度

## 2026-10-31 Q-108/Q-109发布完成（服务器核实、完整CI及线上验收通过）

- 用户本轮“确定服务器有一样的问题就修复部署”已落实，仅当前已修复两项：企微成员清单/客户分页异常响应不再假成功；发货数量输入提交/明细及汇总显示保持十进制精度。审批辅助标记仍待调查，Q-096/Q-069/Q-075原选择未变，未夹带新策略或历史清洗。
- 发布前只读核实服务器前后端仍953eea2，两个修改后端文件SHA与Git修前基线一致，当前OrderDetailPage实际JS与953固定发布包一致，server-baseline.log。证明线上同样代码缺口，不宣称生产已发生异常企微响应或历史发货数量有误。
- 本批重新执行check_wecom_follow_member_response/check_shipment_quantity_precision/check_shipment_request_key三个真实隔离PG专项全部通过，企微用MockTransport无真实外部请求，发货仅合成订单；21项发布安全测试通过。既有1670项完整单元、前端类型/构建和浏览器红绿证据保持；固定提交 `9d13165baa7a5f4e83c808f4c3c9e8e1019f931c` 完整CI运行41190468805后端/UI两job均completed/success，ci-final.json，包含本批全部登记脚本/精确数量浏览器断言，未跳过检查或红CI上线。
- 源码提交已同步GitHub/Gitee原pending-acceptance分支，发布包完全来自该固定SHA的git archive、baseline953eea2、冻结离线构建。包/tmp/crm-release-quality-9d13165.tar.gz，SHA256 `870c53c60439143c7b8a7859246da2e91a4d9456b7c086ef84e53328eb54cf2e`；工具SHA256 `8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab`，服务器两者校验一致。统一完整预检通过后等待同SHA CI全绿才--apply，不放宽基线/迁移守卫。
- 发布完成旧应用/前端备份、短暂重启切换、完整491文件/公网资源/健康检查，返回status=verified；备份 `/opt/crm-sales-agent/backup/unified-9d13165baa7a-wk5jmicf` 保留。未运行DDL/依赖升级、修改.env/生产企微或编号配置/角色权限/历史业务数量、清理真实文件或执行真实同步/发货测试。
- 独立postcheck以本地包manifest为期望，与服务器RELEASE.files完整一致，逐491文件哈希、前后端版本标记均9d13165、status verified、systemd active、8010 app up/database ok；健康附带钉钉sync_running/stream_connected true。公网首页及当前入口JS与固定包字节一致，postcheck.log退出0。健康/静态核验不替代真实生产业务角色浏览器/第三方故障注入/全锁图与大数据性能验证。
- 证据ops/backups/release-wecom-shipment-20261031/，含server-baseline、三专项、发布安全、build、manifest、preflight、CI、deploy、postcheck。隔离PG正常停止。收尾文档单独提交同步两个仓库，应用实际运行SHA保持9d13165而非后续文档提交，未产生应用补丁遗留；长期质量目标未宣称完成，不自动把本次上线授权扩为后续部署授权。

## 2026-10-31 Q-108/Q-109获准发布（进行中）

- 用户明确授权服务器同样问题后部署，本批仅已复现并本地修好Q-108/Q-109；审批辅助标记差异及Q-096/Q-069/Q-075不纳入。当前服务器前后端标记仍953eea2，两修改后端文件SHA及当前OrderDetailPage实际资源与953eea2固定包一致，server-baseline.log，确认同样代码缺口；不在生产执行同步故障/订单造数据。
- 证据汇总ops/backups/release-wecom-shipment-20261031/。本次复跑企微响应/实际发货数量/排批防重真实隔离PG，随后固定提交构建、完整同SHA CI及统一发布预检/执行/独立核验，全部成功后另记完成。当前不是发布成功声明，无DDL/依赖/生产数据或配置变化。

## 2026-10-31 报价撤回/失败回滚/重提与旧审批隔离补验（当前链路完成）

- 用户本轮要求“做完”，完成当前已开始的审批撤回链路，不将此解释为新增部署批准。上轮Q-109浏览器/真实PG精度修复为实质进展，已重读目标原文/质量文件/工作区，保留Q-108/Q-109有效修改；已有check_quote_api覆盖版本侧撤回正常请求，本轮新增的是双入口重提、旧申请隔离及写后异常回滚组合。
- 仅本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream。人工非admin申请人角色quote:view/manage+完整成本零毛利明细/确认零运费，CNY100报价，正常JWT/正式ASGI/PG提交真实pending审批。分别正式版本侧withdraw-approval和审批单侧withdraw，均quote=draft、version=not_submitted/submitted_at空、旧instance=withdrawn且只有一次withdraw记录；重提获得新instanceID，旧withdrawn/新pending，两次提交各有独立业务事件/客户系统动态。
- 用独立admin JWT对旧instance依次approve/reject/withdraw，均400“已处理”；每次独立SQL报价/版本/两实例/审批记录/客户动态/业务事件完整to_jsonb与基线逐值相同，不会批到新申请。重复当前版本submit也400零变化；申请人真实pending/mine收件箱仅该版本新instance，不保留旧实例为待审批。保留真实历史通知，不以旧通知仍在就断言应自动删除。
- 进一步两入口分别在最终withdraw审计钩子先flush，事务内SQL确认审批withdrawn/版本not_submitted已写，再注入RuntimeError500。独立会话上述六类完整快照等于pending请求前，说明状态/withdraw记录整体回滚；finally恢复钩子，原请求正常重试后执行上述新旧审批隔离仍PASS。指定进程钩子不等同真实断网/连接断开，不冒称全部并发审批锁图或通知故障已验证。
- 夹具首次事件查询cast参数被asyncpg按text推断而传int报错，initial-query-fixture.log保留；改为独立完整event LIKE参数后sequence-only.log两PASS。再追加写后审计异常，最终probe.py/result.log两PASS/exit0，日志含两次刻意异常栈；未修改应用来迎合夹具。没有在生产操作真实审批/客户或外发消息。
- 静态及真实SQL观察到辅助字段差异：版本侧撤回清version.approval_required=False但保留旧instance.current_node；实例侧撤回保留approval_required=True但清current_node=None。两者均在本轮阻止旧审批并能重提，不能将辅助字段差异直接等同业务状态损坏。前端QuoteDetailPage按approval_required显示“超出价格权限”，该标记可能表示仍超价格权限而非是否正待审批；业务意图/完整显示影响尚不足，单独待调查，不编新确认编号/不自行改展示或审批口径。本轮没有新增可证业务操作缺陷。
- 证据ops/backups/quote-withdraw-resubmit-20261031/。git diff --check通过，隔离PG正常停止；没有应用修改/新增永久镜像测试，不重复1670项/前端构建/浏览器或SSH。Q-108/Q-109本地未提交/推送/部署，线上仍953eea2；Q-096/Q-069/Q-075原待选择保持。当前“做完”要求的撤回检查已收尾，长期目标active未完成；后续需针对辅助字段语义寻找明确业务判据，或转其它独立高风险链，不据差异擅自修复。

## 2026-10-31 Q-109发货计划前端大数量丢0.001及数量显示精度（服务器确认，本地修复）

- 上轮正式导出/失败重试补验为实质进展。本轮恢复目标原文/质量文件/工作区，转发货数量精度；ShipmentBatchItemInput/ShipmentShipItem已按Numeric(16,3)校验，未把已有三位限制误报缺失。真正遗漏在前端请求/显示，不重复Q-084金额舍入或已修后端remaining_exact计算。
- Q-109/P2：OrderDetailPage排批Input与plannedQtys保留字符串，但提交map用Number(qty)。极大合法值9999999999999.999变成9999999999999.998；排批提示、批次明细和顶部/剩余数量原先消费旧浮点，亦显示错尾数。后端已有overview/summary精确字段，但前端类型及读取未接入；批次明细仅给旧浮点字段。
- 修前真实隔离浏览器运行已发布953eea2包资源，输入原文.999，捕获实际POST planned_qty数字.998，modal订购/未计划也显示.998；browser-red.log及body-red.json。将捕获数量交给本机真实JWT/正式POST排批（仅替换测试订单/明细ID和请求键），合法CNY订单明细订购.999，SQL planned存.998、GET unplanned_exact0.001；api-red.log实证，不仅是Number演示。没有在生产造巨额订单或改历史批次。
- 只读SSH当前前后端DEPLOYED_COMMIT均953eea2，order/service.py SHA与Git953一致，当前OrderDetailPage哈希资源与固定包manifest相同，server-readonly.log，确认线上实际页面存在相同转换，而非以服务器残留frontend/src推断。未读取真实业务客户/数量。
- 最小修复：排批直接提交原数量字符串，API planned_qty类型兼容number|string，原正数筛选/校验/权限/稳定键/请求事务不变。前端类型补已有overview/summary exact字段；排批可用量、顶部数量、未完成说明/实发提示优先精确字符串，去尾零保持普通数字原格式。批次接口保留旧数字，补planned_qty_exact/shipped_qty_exact，批次明细按状态选择原计划/实发规则后用exact优先，保留0而不回退计划。没有新数量口径、截断/重算、数据库/锁/依赖或历史数据清洗，属既有三位存储精度的遗漏消费补齐，无需另选业务算法。
- 新增登记check_shipment_quantity_precision，仅本机隔离PG/禁推送调度。最大量、0.001、1.250三场景：正式排批后SQL数量/明细exact/未计划量精确；同键回放同batch且create审计1；正式自动按计划实发，SQL shipped与明细/summary exact及all_shipped、remaining全部一致。最大订单全部排完unplanned0.000且实发后remaining0.000，不再剩0.001；两个小数对照仍保留各自剩余。订单/明细为本机ORM合法CNY数量及对应逐行金额夹具，不冒称从完整询价/报价生命周期建立的订单。
- 独立浏览器绿例重新捕获planned_qty字符串.999，将此捕获值再交真实PG排批验证（api-captured-green.log），页面标签/输入/请求均.999，失败重试完整body及键相同、模拟仅一批。正常4与极大值的请求/显示回归已纳入ops/confirmation_browser_smoke.mjs，保留原防重/主动新建场景，新增取消历史批次精确计划量显示；完整full-browser.log exit0并有新增PASS，不称浏览器与PG直连端到端或真实外部发货。
- 前端TypeScript+生产构建通过，既有lottie eval等警告未改；完整后端1670 passed in17.61s（本轮新增PG脚本而非pytest数量），pyflakes/node语法/diff通过。辅助格式函数随后仅调整注释归属，未改执行逻辑。证据ops/backups/shipment-quantity-precision-20261031/；隔离PG已正常停止，浏览器/server/profile正常退出。
- Q-109本地修改未提交/推送/部署，Q-108全部有效改动保留，线上仍953eea2。边界：极大合法数量/两正常三位对照、计划创建/回放/自动全批实发/数量展示；未穷尽多SKU巨大合计/全部老客户端/交叉锁竞争，也不修复历史已错误保存的数量或其它模块数量展示。长期目标active未完成；Q-096/Q-069/Q-075原选择不变，下一步可转其它业务链，避免重复已有发货故障模板。

## 2026-10-31 客户导出正式HTTP及失败重试浏览器补验（未发现新缺陷）

- 上轮Q-108客户分页补齐/1670项为实质进展。本轮恢复长期目标/质量文件/工作区，转导出链路。历史已有CSV公式及XLSX文本防护、导出规则静态/单元证据，不能再次凭等号报注入；本轮补的是正常非管理员正式HTTP/文件字节/SQL审计与下载失败真实前端交互，不新增镜像应用实现。
- 本机55441/crm_iso_analytics_20261030_test，关闭推送/调度/Stream。人工构造self角色仅customer:view/export，三自有未删客户（两展会/一官网）、一自有软删及一他人客户，名称带随机标识、逗号双引号换行及等号开头，地址含引号换行。正式正常JWT与ASGI/PG：列表keyword+source筛选严格本人两条，POST导出解析CSV也严格同两条；GET全本人导出三条，已删/他人均无，未借此扩大默认角色或生产权限。
- 实际响应UTF8 BOM、text/csv及attachment正确；csv.DictReader严格回读记录数、逗号引号换行、地址/负责人，原等号文本沿既有约定加前导单引号防公式，不宣称完全不转义。独立SQL成功export审计count、customer_ids、filters精确对应导出范围，五客户完整to_jsonb始终不变。GET/POST“其他”用途缺说明分别按既有422/400拒绝，无附件头、不增加export审计；正式管理员PATCH临时配置limit1后本人两条导出413明确超限、无文件/额外export审计，limit2同筛选成功恰2条。finally原样恢复本机export配置，无生产数据或配置写入。
- 隔离浏览器使用已发布953eea2固定包解出的web资源、独立headless profile和内存API，无真实业务账号/数据库连接。导出用途选择数据核对后，依次模拟HTTP413/403/500：每次只有一次POST、相同用途/筛选body，错误提示可见、弹窗/用途保留、不跳登录、零Blob URL/下载触发。随后200重试关闭弹窗，恰一次CSV下载流程及一次Blob创建/释放，释放地址与创建地址一致。Anchor下载click被计数替身截获以免落桌面文件，不声称Excel打开/真实磁盘下载/与上述PG联合浏览器端到端；错误文案当前通用“检查权限或筛选范围”，无静默成功证据，不擅自改其体验。
- 证据ops/backups/customer-export-http-20261031/probe.py/result.log两PASS、browser.mjs/browser.log四PASS，均exit0。浏览器/server/profile按finally清理，隔离PG正常停止。没有新确认Bug/应用修改或永久测试/重复1670项/前端构建/远端CI；git diff --check通过，Q-108有效本地改动完整保留，未提交推送部署或SSH读取生产客户。
- 边界：本机五客户/本人范围、指定用途与source筛选、合成CSV和三种指定HTTP失败；不含大数据内存/并发、真实Excel求值/编码切换、真实网络中断或生成器中途失败。既有机制覆盖本轮，不据此宣称全模块导出安全/性能充分证明。长期目标active未完成，下一独立方向可转文件生成失败/通知投影一致性，避免重复公式字符串探针；Q-108本地未部署，线上仍953eea2，Q-096/Q-069/Q-075原待选择不变。

## 2026-10-31 Q-108同类补查：企微客户分页未知响应假成功（本地补齐并验证）

- 上轮Q-108跟进成员清单修复为实质进展；本轮重读长期目标/质量文件/当前工作区，沿其调用链验证客户页本身，不把同一“未知清单当空清单”的根因重新编号制造问题数。Q-108原修复/测试保留，本批仍未获生产发布授权。
- 新触发证据：跟进成员返回正常member-a，但externalcontact/list返回{}。正式本机隔离HTTP同步仍200，独立SQL新增success任务、0条；API修前api-red.log保留，且没有把此响应称为明确空页。现有payload.get("external_contact_list") or []及external_userid or []使两清单都缺失时静默结束分页；单元修前11异常失败/5合法通过，unit-red.log，不依赖静态猜测。
- client.list_external_contacts在返回页前补识别校验：沿原优先级使用非空external_contact_list中的external_contact/external_userid，或external_userid ID列表；实际使用的清单须为列表、身份须非空白字符串。明确[]仍可用；新格式空但旧ID格式非空时仍按原fallback处理，不要求可选名称/follow_info、不重排/改写返回内容。异常抛既有安全WeComResponseError，沿用户已批准Q-064/Q-065“响应不明整次回滚并留失败”的边界；不改变明确企微业务错误的按成员部分成功策略、页数阈值、锁/事务位置、归一规则、权限或依赖。
- 新增test_wecom_contact_page_response.py16项，覆盖缺失/null/错误容器/错误行/缺身份及五类合法形态（两种空页、纯ID页含后续游标、对象页及新空旧非空fallback），返回内容原样保留；与Q-108原成员及既有客户同步测试合跑38 passed in0.36s。当前完整后端1670 passed in22.74s，full.log，未跳过或放宽旧测试。
- 扩展现有已登记check_wecom_follow_member_response，不新增镜像脚本/重复登记。正常JWT/正式API/真实PG仅55441/crm_iso_analytics_20261030_test，所有外部响应MockTransport、禁推送/调度/Stream：原10异常成员清单绿例保留；两种明确客户空页各200/success0且四事实表完整不变；缺失页/ID字符串/对象页缺身份三类×第一页或第二页六次均502明确响应格式异常、只新增1条failed任务（success0/fail1），企微客户/关系及CRM客户/联系人四表完整快照不变。第二页场景确实先拉首个客户详情再取第二页，前页缓存不落部分客户。正常两成员/两页/重复客户及重试仍各成功2条且不重复，CRM三表不变。
- 最终api-green.log四组PASS/exit0；最初API红在第一页{}假成功即按预期失败，未冒称其余五场景均有修前正式HTTP红例，其余由明确调用链/单元红及修后真实API验证。页格式检查只看实际使用分支，未借此拒绝未使用的可选兼容字段，未做游标协议或额外字段新策略。
- 只读SSH再次确认当前应用953eea2及client.py哈希与Git953原文件一致，server-readonly.log，证明服务器仍有该缺口，不修改生产配置/数据或触发真实企微调用。证据ops/backups/wecom-customer-page-response-20261031/。pyflakes/diff检查通过，隔离PG已正常停止；无前端变更，不重复构建/浏览器。
- 当前Q-108含成员清单及客户页两处同类遗漏，均本地修好未提交/推送/部署；线上仍953eea2，原十项已发布状态不变。长期目标active未完成，Q-096/Q-069/Q-075原待选择不变。边界：HTTP响应替身/本机SQL，不是真实企微分页故障或全部游标/限流/并发锁图证明；本链明确清单边界已补齐，下一步转其它模块的导出/错误反馈等独立方向，避免继续逐字段穷举低价值模板。

## 2026-10-31 恢复审查：Q-108企微跟进成员响应不完整却同步成功（服务器确认，本地修复）

- 上轮十项953eea2发布/双仓同步完成为实质进展。用户将长期目标设active，恢复目标原文/质量文件/工作区，从已发布基线转集成分页/响应边界，不重做已上线补丁；本项不在上一批生产批准范围，未自动部署。Q-096/Q-069/Q-075原选择保持。
- Q-108/P2：wecom/client.get_follow_user_ids使用payload.get("follow_user",[])，缺字段当空清单，其他类型未经验证就str转换。其上层list_all_external_contacts在成员为空时返回空结果，sync_external_contacts正常finish_job，路由200“同步完成0条”；无法区分企微明确返回[]与没有提供清单。缺字段具体触发已正式HTTP/PG复现：MockTransport上游get_follow_user_list返回{}，接口200、独立SQL该次job status=success/success_count0/fail_count0，客户/关系事实未变。真实影响是异常同步被误报成功，并非删除了客户或证明生产当前发生漏数。
- 只读SSH确认服务器DEPLOYED_COMMIT953eea2，wecom/client.py、router.py、service.py三文件SHA256均与本地修前相同，server-readonly.log。不读取企微真实数据/凭据或在生产故障注入。ERP分页候选当前真实适配器fetch_records仍明确拒绝未验收采集，未将仅替身可达的分页猜测记成生产Bug；企微跟进关系分页已有重复游标/页数保护，不重复报告。
- 最小修复get_follow_user_ids核对follow_user明确为列表、每项为非空白字符串，异常抛既有安全WeComResponseError；明确[]及有效字符串原样返回。此与同客户端已有user/list成员身份校验、Q-065异常响应及用户已批准Q-064失败回滚/留痕规则一致，属于遗漏检查补齐，无新权限、角色、数据归一策略、字段/锁/事务位置、页数阈值、依赖或自动重试选择；原按成员业务错误部分成功边界不变。
- 新增test_wecom_follow_member_response.py：10种异常（缺失/null/字典/字符串/数值、null、空白、bool、对象成员）和3种合法空/单/多成员。初次夹具漏设MockTransport客户端base_url，13项均为URL夹具失败，initial-fixture.log保留；补mock.invalid地址后真正修前10 failed/3 passed，unit-red.log。修后新13+既有客户同步9项共22通过，完整后端1654 passed in15.79s，unit-green/full.log；未为通过测试放宽断言或改合法响应内容。
- 新增登记check_wecom_follow_member_response，含本机隔离PG/禁外发/禁调度防呆，真实JWT/正式ASGI/PG，下游全部MockTransport。十异常各HTTP502明确“响应格式异常”，只留一次failed任务（success0/fail1），完整企微客户/关系及CRM客户/联系人SQL快照不变、无客户列表/详情后续请求、响应不含合成token/secret。[]对照HTTP200/success0、四表不变；两成员/两页/跨成员同客户去重，两次同步均成功2条、同两外部联系人更新不重复、每次详情仅2次、CRM客户/联系人/关系三表完整不变。api-red.log保留缺字段假成功；最终api-green.log正式脚本exit0。
- 证据ops/backups/wecom-follow-member-response-20261031/。pyflakes、git diff --check通过，隔离PG正常停止；只有client小范围应用改动、新永久单元/隔离脚本及清单登记，未改前端，不重复构建/浏览器。既有错误提示组件未在本轮重新操作，不能冒称生产角色浏览器或真实企微网络故障验收。
- 当前Q-108本地修复未提交/推送/部署，线上仍953eea2，上一批十项保持已发布。当前验证仅成员清单响应完整性与正常两页去重；未穷尽真实企微巨大页数/限流、游标异常的所有部分成功组合、并发同步锁图或ERP外部验收。下一步可转客户分页响应本身的格式校验/错误留痕（先核对已有防线），长期目标active未完成，不将一次生产批准扩大至后续问题。

## 2026-10-31 Q-098至107十项发布完成（服务器核实、CI/上线验收通过）

- 当前十项本地已确认/已验证修复按用户“确定服务器有一样的问题就修复部署”的明确授权发布；范围承接下一节发布准备记录，不扩大到Q-096/Q-069/Q-075。源码提交 `953eea2d0f968dca5579afbaafdfc613fa5dcc21`，现有GitHub和Gitee pending-acceptance分支均已同步该提交；本条收尾只补记录，不改变应用内容。
- 服务器十个实际修改后端文件与修前3c4291d基线SHA256完全相同，server-baseline.json。统一预检进一步通过完整后端基线/依赖/迁移head/包完整性/既有服务等守卫。服务器同码缺口已核实，不宣称生产现有编号配置错误或在生产造数据验证。
- 本批重新执行21项统一发布安全测试及七个真实隔离PG脚本，联系人两类并发、付款通知/两类建单动态/审批通知动态精度、排批丢失回执/回滚/同键并发/权限/浏览器稳定键、线索255边界、编号七目标规则/实际取号与建单均有对应历史红绿和本批PASS记录。当前完整单元1641项通过；固定提交CI运行41187170262，headSha精确对应953eea2，后端完整接口回归及UI交互两job均completed/success，ci-final.json/ci-watch.log；CI已包含所有新脚本，未绕过失败或放宽断言。
- 发布包完全来自git archive固定953eea2，基线3c4291d，冻结离线依赖构建通过；包/tmp/crm-release-quality-953eea2.tar.gz，SHA256 `fd4316623293eabffce62dcd2937c466b02a505a37c43c4e9a0cff42b737f41d`。工具SHA256 `8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab`。上传服务器独立临时目录后两SHA再次匹配，预检通过，等待同SHA完整CI成功后才执行--apply。
- 统一发布完成备份、短暂停止/切换/启动、完整491文件及健康/公网资源校验，返回status=verified/commit953eea2；回滚备份 `/opt/crm-sales-agent/backup/unified-953eea2d0f96-5szaejqt` 保留。未修改.env/生产编号规则/角色授权/业务数据/虚拟环境/迁移或依赖，未清理历史记录，也没有真实外部消息/业务操作测试。
- 独立postcheck.py以本机固定包manifest为期望，验证线上RELEASE.files整份相等，逐491文件SHA256、DEPLOYED_COMMIT及WEB_DEPLOYED_COMMIT均953eea2、release.status verified、systemd active；8010/health app up/database ok，健康返回钉钉sync_running/stream_connected true。公网首页及当前主入口JS与包内字节相同，postcheck.log包含491文件/健康汇总及公网字节核验PASS，exit0。这不是实际生产业务账号的创建/付款/审批全流程验证，不将健康字段当第三方故障注入结果。
- 全部证据ops/backups/release-quality-ten-fixes-20261031/，含manifest、七脚本日志、build/preflight/CI/deploy/postcheck。隔离PG已停止；应用源代码发布提交为953eea2，后续文档收尾提交与其运行内容一致但不另行部署，避免混淆仓库最新文档SHA和应用SHA。
- 当前十项修复均已部署，无本批应用修改遗留；仍待选择Q-096/Q-069/Q-075，未把本批部署批准扩大至后续新问题。长期质量目标保持用户paused，收尾不自动恢复审查；既有未穷尽生产角色浏览器/第三方故障/全系统锁图与大数据性能范围保持真实边界。

## 2026-10-31 当前十项修复获准发布（构建/CI/上线验收进行中）

- 用户明确要求“确定服务器有一样的问题就修复部署”，本次具体范围Q-098至107：主联系人并发/改挂提示；回款通知、手工建单动态、报价转单动态、报价审批通知/动态、AI报价卡片精度；排批弱网防重；批量线索原因长度；编号配置/取号长度。只发布已记录方案和验证，不带入Q-096/Q-069/Q-075待决定项，不改生产数据/编号配置/历史记录/数据库结构/依赖。
- 只读SSH当前DEPLOYED_COMMIT为3c4291d3606011195f3d0c0ff357ef81e17b2e1d，服务active；本次十个修改的backend/app文件逐项与该Git基线字节SHA256完全一致，server-baseline.json，证明服务器有同样代码缺口，未在生产造数据复现。完整后端基线还须由统一发布预检再次核对，不能用十文件代替全量守卫。
- 已有当前源代码完整单元1641项及前端构建/浏览器记录保留；本批重新跑21项统一发布安全测试通过，七个正式PG检查脚本覆盖全部十项问题，完整输出在ops/backups/release-quality-ten-fixes-20261031/。后续固定提交构建、同SHA远端完整CI、统一发布预检/正式执行/独立线上只读验收通过后才记录已上线，当前不是发布成功记录。
- 本次授权为明确应用发布；长期审查此前用户设paused，收尾不自动恢复发现新问题。同步现有两个仓库的源码授权沿用已明确同意范围。无外部真实消息/业务写入测试。

## 2026-10-31 系统配置与回收候选联动重算的提交故障补验（未发现新缺陷）

- 上轮Q-107已落实修复并通过1641项，本轮恢复长期目标/质量文件/工作区，转此前仅静态初看的settings.upsert_setting→recompute_open_candidates。已有check_pool_wait_and_settings覆盖正常联动，未发现本轮两种真实提交边界组合，因此不重复正常天数校验或编号问题。
- 仅本机55441/crm_iso_analytics_20261030_test，正常admin JWT/正式PATCH /settings/真实PG，禁推送/调度/Stream。两键pool_recycle_notice_days及pool_recycle_defer_days，各建pending/deferred/approved/executed/rejected/superseded/restored七种候选（每客户一条，满足现有开放候选唯一约束）。本机配置和全部候选在独立会话采集完整to_jsonb，审计也采完整行；本轮不是运行生产回收或调用真实审批。
- 两键×两故障四场景：最后setting审计前先真实flush，确认本次候选已按新天数改变，再注入RuntimeError500；另一组AsyncSession.commit先真正提交PG再单次抛错500。前者独立SQL配置/全库候选/该配置完整审计与请求前逐值相同，证明联动真实写入仍一起回滚。后者已提交审计1，恢复钩子后正常重试200再记一次合法设置更新审计；全部候选与首次已提交结果完全相同，不重新从重试时刻计算等待期。不能把重复设置的正常第二条审计误判成重复业务副作用。
- 请求传数字字符串，最终存储days为整数；预告更新只改pending/deferred的notice_days及notice_at+days得到的due_at，暂缓更新只改deferred的defer_days及decided_at+days得到的deferred_until。全隔离库候选逐行对照预期，所有其余字段/终态/approved保持原样；审计recomputed_candidates精确等于原符合条件行数，配置值和实算天数一致。
- 第一轮四场景已PASS，其中提交回执异常组沿用前场景相同设置值；保留same-value-retry.log。为进一步证明配置值本身也与候选一起改变，最终probe.py将第二场景分别用15/11天，且断言本次配置与原值确实不同；最终result.log四PASS、exit0，包含刻意异常栈。finally恢复原配置及原候选四个可变字段，删除本轮候选，钩子恢复；合成客户/正常设置审计留在隔离库，不清理生产数据。
- 证据ops/backups/settings-recompute-failure-20261031/。未发现新可达Bug/应用修改，不增加仅复刻实现的永久测试，不重复1641项/前端构建/浏览器；git diff --check通过，隔离PG正常停止，无SSH/生产写入/提交推送部署。Q-098至107十项本地修复保留。
- 边界：正常管理员、既有完整时间锚点、本机指定故障，不含配置修改与回收审批/扫描同时进行的锁竞争、缺失时间锚的遗留行、超大量候选性能或真实网络断线。通过本轮不能宣称所有配置/所有锁图无风险；下一独立方向可转后台集成游标/分页边界或文件导出错误反馈，避免继续围绕同类设置提交模板。Q-096/Q-069/Q-075原选择保持。用户本轮设长期目标paused，停止继续审查；目标尚未完成，恢复后从下一独立方向继续。

## 2026-10-31 Q-107按用户继续指示修复完成（本地验证，未部署）

- 用户询问编号规则/系统自动生成后，已澄清36位来自本机人为配置，普通业务员不能改、当前前端无修改入口；服务器相关代码相同不代表生产当前规则已经错误。用户随后回复“继续”，按已说明的推荐方案保留字段与既定格式、保存时拒绝无效组合，先本地修复验证，不扩大数据库/截断编号/改历史或权限。QUALITY_GOAL同步该范围。
- settings/numbering新增当前正式规则到目标列映射，长度直接读取Quote/SalesOrder/ContractDocument/CustomInquiry/BizDoc列元数据，而非另写32常数。七类quote/order/contract/inquiry/sample_doc/order_doc/quote_doc分别检查真实目标列；旧sample默认项当前无正式取号调用，不假定它是已有业务编号列。未知自定义规则保存行为保持，未来新增正式消费者须补入保存映射；generate_for运行时对任何传入目标列均检查，不依赖映射。
- settings/router新建规则在add/flush前检查下一流水的完整编号；更新构造合并后临时规则，用新重置周期的当前计数验证后才赋值保存，防止只传前缀绕过组合检查。沿用现有权限、1~12补零位数、格式、周期、审计与提交位置，不自动缩短/截断/重置计数。实际generate_for返回前再核对最终候选号，覆盖日期变长、计数器超过补零位数、撞号跳过以及旧错误配置；超限明确422，请管理员处理，不让varchar错误变500，事务沿原请求回滚。
- 新增test_numbering_length.py共17项：七类默认格式不变、七类32位中文前缀边界可用/33位拒绝、日期格式展开长度、真实下一流水超出补零宽度不截断、跨年固定长度。完整后端1641 passed in20.20s（full.log）；早期聚焦新16+原9项25通过，后加跨年一项已包含完整回归，不混记测试数量。
- 新增登记check_numbering_length，只允许本机隔离PG并禁推送/调度/Stream。正常admin JWT/正式API：七类36位更新422且完整规则/计数器不变、32位更新200并真实PG generate_for输出32位、主动rollback后规则/计数器快照不变；随后仅改前缀使33位也422零改变。暂挪本机quote_doc规则测试正式POST：36位422无行、32位200；finally删除本次新建规则并恢复全部原规则，不修改生产或历史业务编号。
- 另绕过API仅在本机设旧错误order规则，真实POST订单422“编号过长”，独立SQL订单数/全部order计数器完整快照不变；通过正式PATCH恢复合法32位配置，同一订单成功且真实order_no恰32位。对应修前result.log已证实配置200后建单500、同订单恢复默认则200，修后green.log三PASS/exit0。七类真实PG取号不等同七类完整业务创建/审批/文件导出全流程。
- 证据ops/backups/numbering-length-20261031/；pyflakes与git diff --check通过，隔离PG已正常停止。应用无前端变更，不重复前端构建/浏览器；不声称真实外部故障、生产角色或全部锁图已验证。日期格式保存时只核对当前下一号，不能证明任意未来日期都不增长，运行时检查负责明确拒绝实际超长号；不主动禁用现有自定义日期格式。
- 未提交/推送/部署或修改生产配置。Q-098至Q-107共十项本地修复，Q-107本项选择已落实，不再待决定；Q-096/Q-069/Q-075原待选择未获本项授权。当前完成用户指定编号问题，后续质量审查可转配置提交失败的一致性（此前仅静态初看，未声称验证完成）。

## 2026-10-31 Q-107编号规则可保存但生成订单号超过存储长度（已复现，待方案决定）

- 上一轮为创建权限澄清，未新增验证；本轮重新读取长期目标、质量文件、AGENTS及工作区，转业务配置与下游建单边界，不重复默认业务员创建测试。保留既有Q-098至106九项本地补丁。
- Q-107/P2：settings/schema.NumberingRuleUpdate允许prefix/date_format/seq_length组合；router只检查流水1~12及重置周期。NumberingRule.prefix字段16字、date_format32字、seq_length允许12；numbering.format_number直接拼接，而sales_orders.order_no为String32。每个字段单独合法不保证最终编号可存储。
- 本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream，正常admin JWT/正式ASGI/PG：保存order规则prefix为16个P、date_format=%Y%m%d、seq_length=12，PATCH200，返回next_preview长36。正式创建随机自有客户/有效SKU订单1件×1元，POST500，实际PG INSERT sales_orders明确StringDataRightTruncationError/varchar32。独立SQL该客户订单数仍0；恢复原规则后完全相同订单请求200、订单数1。finally恢复规则；没有清理或重算业务历史。此为配置使正常建单不可用，不是半单提交。
- 只读SSH取得服务器settings/schema.py、router.py、model.py、numbering.py及order/model.py五文件SHA256，全部与本地相关原文件一致，证明服务器代码具有同一缺口；未读取/修改生产编号配置或创建订单，不能断言生产当前已经配置成36位。部署目录无.git，首次尝试git rev-parse失败，后改为直接五文件哈希校验成功，不据失败命令推断运行版本。
- 证据ops/backups/numbering-length-20261031/probe.py/result.log/server-readonly.log，最终PASS/exit0，含真实PG超长异常栈；最初夹具误用/settings/numbering-rules得到404，initial-route.log保留，按真实注册路由/numbering-rules纠正后才完成复现，没有改应用迎合测试。隔离PG已正常停止。
- 修复存在配置体验/数据库取舍，暂不修改。推荐A维持现有单号字段长度，保存编号规则时检查生成号是否能存储，明确提示缩短前缀/流水位数；收益是提前发现而非后续建单500，代价是原先能保存的无效组合会被拒绝。B扩大编号字段支持更长号，代价是数据库迁移及上下游兼容验证。已向用户提出选择，未推定同意。真正实施前还需查齐各规则目标字段、日期格式扩张及序列超过补零位数的边界，不能仅验证今天预览即声称所有未来编号都安全。
- 未新增应用修改、未重复1624项/前端构建/浏览器，git diff --check通过；无生产写入/重启/提交推送部署。既有九项本地修复状态保持，Q-107新增待决定，不代替Q-096/Q-069/Q-075原选择。长期目标仍active，后续可继续独立配置边界，或获批后补齐Q-107跨单据规则与回归。

## 2026-10-31 批量线索逐项保存点及整批提交边界补验（未发现新缺陷）

- 上轮Q-106真实超长原因红绿及1624项为实质进展。本轮恢复目标原文/质量文件/规则/工作区，保留九项修改，继续上一轮未覆盖的逐项业务错误与外层失败；不重复把已修原因边界再列新问题。
- 当前batch_assign_leads逐ID去重，每条begin_nested保存点，AppError写skipped、成功写assigned，最后统一审计/commit。测试区别于简单手动rollback：让实际assign_lead先改owner/插LeadAssignment，flush并同事务SQL确认真实写入，再只对中间项注入AppError。此为受控故障而非新发现可达业务错误，不据此发明生产Bug。
- 仅本机55441/crm_iso_analytics_20261030_test，正常admin JWT/正式ASGI/PG，三随机pending线索/有效接收业务员，禁推送/调度/Stream。输入三ID加首ID重复，中间项写后拒绝，HTTP200 assigned严格首末两ID、skipped仅中间ID及精确原因；独立SQL中间线索完整to_jsonb与原值相同、无分配历史，成功两项owner正确且各1条历史，重复ID无额外历史。
- 另独立三线索在最后batch_assign审计处flush后抛RuntimeError，正式HTTP500；独立SQL线索完整值及全部分配历史等于基线，即使逐项保存点已经正常释放，外层事务仍一起回滚。恢复钩子正式重试200，各条一次历史/正确owner，不出现半批提交。所有进程钩子finally恢复，没有真实第三方操作/通知或数据库网络故障注入。
- 证据ops/backups/lead-batch-savepoint-20261031/probe.py/result.log，两PASS、exit0，含刻意最终审计异常栈。无新确认Bug/应用修改，不增加镜像测试/重复1624项/构建/浏览器；git diff --check通过，隔离PG正常停止，无SSH/生产修改/提交推送部署。
- 边界：管理员、本机三行、已存在接收人、两类指定故障，未覆盖跨部门实际分配策略、接收人途中停用、批次并发锁图/大数据性能或真实UI跳过展示。现有保存点语义通过本轮，不能推广成全部SQL错误都会逐项跳过（未捕获SQL异常仍整批回滚）。九项修复Q-098至106仍本地，原Q-096/Q-069/Q-075待选择，长期goal active未完成；后续转其它风险方向，避免反复同批次保存点模板。

## 2026-10-31 Q-106批量线索分配原因超长500（服务器确认，本地修复）

- 上轮任务批量混合状态/权限验证为实质进展，本轮恢复目标原文/质量文件/规则/工作区，转lead.batch_assign逐条保存点与历史一致性；发现schema同规则入口遗漏，不根据“无锁/批量”表面静态推断故障。
- Q-106/P2：单条LeadAssign.reason已max_length255，LeadBatchAssign.reason却无限长；共享assign_lead把原文写入lead_assignments.reason String255。正式批量接口reason256个中文字触发PG StringDataRightTruncationError，未被逐项AppError捕获而整批500。独立SQL两线索完整值及分配历史与基线完全一致，说明整批回滚，不是跳过清单与实际部分提交错位。
- 本机55441/crm_iso_analytics_20261030_test，正常admin JWT，ORM两条随机pending线索，正式POST batch-assign到有效种子业务员，禁推送/调度/Stream。probe.py/red.log保留真正INSERT异常与HTTP500、零变更SQL，修前严格预期400失败。只读SSH当前3c4291d、lead/schema.py SHA256 e8e4bdd2abae99f24903370280b73256f0e1f418ff8ac25be887407cc7f413c3与本地原文件一致，sed确认缺失限制，server-readonly.log；不写生产数据。
- 唯一明确修复是LeadBatchAssign.reason补与单条/数据库一致max_length255，保留None/空串/原文，不自动截断、不改字段或数据清洗，不改变谁能分配/分配给谁/逐项部分成功策略。应用只改schema一行。新增6项单元（None/空/255中文/255ASCII可用，两类256均拒绝并对照单条），新增登记check_lead_batch_reason真实隔离PG边界回归。
- 正式HTTP修后256中文/ASCII均400明确“reason最多255个字”，两线索/历史无变化；255中文批量200、两项assigned、无skipped，SQLowner正确/两条历史原因完整原样。green.log PASS/exit0；6单元通过0.12s，完整后端1624 passed in 15.49s；应用/单元/PG脚本pyflakes与diff通过。永久脚本首轮静态发现多余Customer导入后已移除，未放宽断言/跳过检查。
- 证据ops/backups/lead-batch-reason-20261031/。无前端修改不重复构建/浏览器，隔离PG正常停止，无提交推送部署。Q-098至Q-106共九项本地修复；不新增权限限制，也不把本地修复/只读同码确认说成已上线。
- 边界：管理员实际HTTP、原因长度边界、正常两线索分配与PG历史；不含逐项业务异常注入、无权账号/大量批次性能或并发分配锁图。本轮未确认其它候选缺陷，下一步可转批量线索逐项业务拒绝的实际保存点回滚；Q-096/Q-069/Q-075待原选择，长期目标active未完成。

## 2026-10-31 批量完成任务混合状态/权限与重复ID补验（未发现新缺陷）

- 上轮人工跟进提交故障为实质进展。本轮恢复目标原文/质量文件/规则/工作区，转批量操作。batch_complete_tasks明确定义“完成有效项、跳过无权限/终态/专用任务并说明”，不能把部分成功本身当Bug或擅改全有全无。前端TaskListPage约146行已接收completed/skipped并显示原因摘要，本轮仅源码确认该UI调用链、未跑浏览器。
- 现有check_b2_round_repairs覆盖管理员简单两任务；本轮新增普通self业务员混合选择、重复ID及客户时钟。仅本机55441/crm_iso_analytics_20261030_test，关闭推送/调度/Stream，正常zhangsan JWT。ORM随机本人客户next_followup_at两天后、本人pending回访、已完成回访、管理员所属pending任务、本人contract_signed_upload任务；SQL独立确认空缺正BigInt ID，没有造越界编号。
- 正式POST batch-complete提交有效任务ID重复两次及四类不可处理项，200返回completed只有效ID1项、skipped严格四不同ID，已完成及回签专用原因明确。独立SQL有效任务done/备注原值，其余三任务完整行to_jsonb（含时间）等于基线；客户最后一个待回访完成后next_followup_at为空。未把回签任务当普通任务完成或影响他人记录。
- 再正式提交同批五个不同ID及新备注，200 completed空/skipped5，所有任务完整快照及客户时钟不变，原完成日期/备注未被第二次请求覆盖。批量动作本身允许产生新的操作审计，本轮没有宣称所有审计不增加。未注入数据库故障或伪造可见性结果。
- 证据ops/backups/task-batch-mixed-20261031/probe.py/result.log，PASS、exit0；无新Bug/应用变更、不重复1618项/构建/浏览器。diff通过、PG正常停止，无SSH/生产修改/提交推送部署，Q-098至105八项本地补丁保留。
- 边界：顺序批量混合状态及重试，不含多批交叉锁等待/转交并发、任务详情全部权限或大批量性能。此前全有全无怀疑已由明确契约和实际响应排除，后续宜转其它导入/批量真实异常，不能重复把符合契约的skipped列为故障。Q-096/Q-069/Q-075待原选择，长期目标active未完成。

## 2026-10-31 人工跟进及自动回访任务提交异常补验（未发现新缺陷）

- 上轮合同同键真实锁等待/交接重放为实质进展，本轮读取目标原文/质量文件/规则/工作区，转人工跟进跨任务/客户时钟。先核对check_followup_plan已有直接服务rollback/四请求并发/计划修改与完成取消覆盖，本轮仅补正式API最后审计抛错、真实提交后回执异常，不把既有普通顺序能力当新Bug。
- create_manual_followup在同事务保存跟进、自动回访任务、客户last_followup_at/last_contact_unknown/next_followup_at、审计与通知事件，路由commit后投递；该入口创建下一待办而非完成旧待办，本轮据实际调用链修正验证范围，不虚称测试了完成任务失败。
- 本机55441/crm_iso_analytics_20261030_test，推送/调度/Stream关闭，正常种子业务员zhangsan JWT；每组ORM随机本人客户、last_contact_unknown=True，正式POST followups填两天后UTC计划和稳定键。故障一mutations.write_audit在最后create审计前先flush并同事务SQL确认任务已建1，再抛错；HTTP500后六类客户/跟进/任务/业务事件/事件通知/跟进审计独立全行to_jsonb快照完全等于提交前。故障二AsyncSession.commit先真实提交再单次抛错，HTTP500但提交状态完整；钩子finally恢复，非真实PG断网/真实消息投递。
- 恢复后同键正式重试200，followup/task/business_event/create审计各1，next_task_id相符、任务pending；客户已有效跟进/未知联系标记清除，独立SQL next_followup_at严格等于提交UTC计划。已提交组首次500后快照等于最终成功快照；每组再重试两次200/replayed=true/原跟进与任务ID、六类全行快照不变，同键改下一动作409且无副作用。未出现重复任务或客户时钟漂移。
- 证据ops/backups/followup-commit-failure-20261031/probe.py/result.log，两PASS、exit0（预期故障栈保留）。真实业务员HTTP/PG但不是浏览器、真实模型或生产账号。无新确认缺陷/应用修改，不新增镜像测试/重复1618项/构建，diff通过、隔离PG正常停止，无SSH/生产变更/提交推送部署。
- 边界：创建人工跟进+下一待办、顺序指定故障，不覆盖任务完成交错、业务员转交/部门变化或进程强杀。check_followup_plan既有并发证据本轮未重跑，不能由本轮两组断言全系统事务可靠。后续可检查其它尚未覆盖导入/批量修改异常链，避免重复同一提交钩子模板而无新增业务风险；Q-098至105八项本地修复待发布，Q-096/Q-069/Q-075原待选择不变，长期目标active未完成。

## 2026-10-31 合同同键并发与交接后原键回放非管理员补验（未发现新缺陷）

- 上轮合同入账提交前后故障验证为实质进展，本轮恢复长期目标/质量文件/规则/工作区，新增真实锁等待与当前负责人变化组合。现有合同键指纹/归属源码和管理员顺序测试不替代本轮非管理员实际接口。Q-098至105八项补丁保留，未改业务规则。
- 本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream，专用随机两个self账号有file读写/customer:view/order读写但非admin；正常JWT，ORM合成私海客户，旧负责人正式multipart上传PDF夹具，正式POST上传合同入账。同键两请求：首请求在最后generate审计前暂停，第二请求启动，独立pg_blocking_pids确认第二确实被首PID阻塞；放行后10秒内两者200同合同ID，独立SQL合同/文件/原件关联/回签任务/成功生成审计各1。此验证同原文件两提交的真实串行路径，未证明所有不同文件/跨订单锁图。
- 管理员正式客户transfer给新用户，继续使用交接前原JWT。旧账号同键POST合同、合同详情及通用原件download均403/404；新负责人同键原请求200同合同ID，回放后五类完整行快照等于交接完成后的快照，没有重复任务/审计或回放副作用，下载字节等于上传内容。没有为允许正常接手而按创建人永久锁定请求键，沿现有合同规则即可。
- 首次探针误用交接前全行快照比较，合法交接本会迁移待办负责人，触发断言；保留initial-transfer-snapshot.log，改为独立采集交接后基线并严格比较回放，不修改应用/跳过实际可见性。最终probe.py/result.log两PASS、exit0。没有把客户端字段差异或合法负责人更新误报新Bug。
- 无新确认缺陷/应用变更，未重复1618项/构建/浏览器/远端CI；git diff --check通过，隔离PG正常停止，钩子/协程finally恢复清理，无SSH/真实外发/生产修改/提交推送部署。证据ops/backups/contract-concurrent-replay-20261031/。
- 边界：真实本机非管理员HTTP/PG、同原文件同键并发、顺序交接后回放，不含交接在途与入账并发、Word、签署/作废、不同文件同键以及大规模锁竞争。合同当前两个新增异常组合已充分补证，应转其它未充分覆盖的跨模块业务状态，避免不断重复合同顺序路径；长期目标active未完成，待发布八项与原待选择保持。

## 2026-10-31 上传合同原件入账失败与提交回执异常补验（未发现新缺陷）

- 上轮默认业务员真实创建/重试为实质进展。本轮恢复目标原文/质量文件/规则/工作区，转不同生命周期：当前正式上传合同入账故障，不重复此前作废/软删/恢复顺序测试。先确认HTTP已停用模板生成，只接受customer_drafted原文件及来源/标题；没有为测试绕过现行入口或恢复历史模板功能。
- 本机55441/crm_iso_analytics_20261030_test，正常admin JWT/真实ASGI/PG及专用磁盘，禁推送/调度/Stream。ORM随机客户，正式multipart上传随机本地PDF格式夹具（无外部URL/真实合同内容），正式POST /contract-documents指定原件/客户来源/稳定键。不是渲染生成PDF或完整签署流程，未调用真实钉钉审批/外部消息。
- 两组进程内注入：最后generate审计处先flush再抛RuntimeError；AsyncSession.commit先真实PG提交再单次抛错。两组正式HTTP500且钩子命中，finally恢复。前者独立会话合同/原文件/原件挂载/回签任务/成功生成审计状态等于请求前（原上传文件整行保留，其他零）；后者合同/挂载/任务/成功审计各1且文件未变。与实际数据库网络断开不同，不将指定故障替身冒称生产故障复现。
- 两组均先通用下载原文件字节相同，再用同键正式重试200；之后重复两次同键，均原合同ID，五类完整关联快照不变；同键改标题严格409且不新增。最终合同专用download200字节与原上传完全一致；提交回执异常组重试后完整快照等于实际成功状态，没有清理已提交原件、重复任务或成功审计。仅检查generate审计不变，download自身合法审计不计入“全部审计不变”结论。
- 证据ops/backups/contract-create-failure-20261031/probe.py/result.log，两PASS/exit0（含两次预期异常栈）。无新确认Bug或应用修改、不新增镜像测试/重复1618项/构建/浏览器，diff通过，隔离PG正常停止；无SSH/生产写入/提交推送部署，Q-098至105八项本地补丁保留。
- 边界：普通客户上传PDF入账、管理员、顺序异常；未覆盖Word解析/真实模板渲染、同键并发/客户交接交错、强杀/真实连接中断及真实生产角色。既有机制覆盖本轮，不泛称所有归档异常已验证。下一步可查该流程同键并发与当前归属重放是否共享正确锁序，先核对已有测试避免重复；Q-096/Q-069/Q-075待原选择，长期目标active未完成。

## 2026-10-31 恢复审查：默认业务员实际建客户与重试补验（未发现新缺陷）

- 用户重新激活长期目标，本轮恢复原文/质量文件/AGENTS/工作区；上轮撤回只读新建按钮限制是用户口径决定，本轮进一步拿真实接口证明默认业务员正常路径，不重复以人工view-only角色代表正常销售。
- 仅本机55441/crm_iso_analytics_20261030_test，关闭推送/调度/Stream。正常登录隔离种子zhangsan，GET auth/me确认有效salesperson非admin、有customer:create且无customer:assign；没有新增或修改角色授权。正式POST /customers仅随机名称/request_key200，返回owner_id本人；详情200同名称、按名称/本人筛选列表只该客户。未指定他人负责人，不混淆普通创建与已有分配权限。
- 同键同内容正式重试两次均200且原客户ID；同键改名称严格409，独立SQL随机名称客户数1、owner本人、成功create审计1。证明此默认业务员可直接建客户且既有防重可用；无权限收紧、禁用按钮或新应用修改。证据ops/backups/salesperson-customer-create-20261031/probe.py/result.log，PASS/exit0；diff通过、隔离PG正常停止。
- 局限：本机种子账号/真实ASGI/PG，非生产角色配置证明，不含此次浏览器表单操作/提交回执中断；不把此前人工只读浏览器夹具改写为正常销售验证。未SSH/改生产/提交推送部署，未重复1618项/构建/浏览器。Q-098至105八项仍本地，已撤回的新建按钮限制不再待决定；Q-096/Q-069/Q-075原待选择保持。
- 下一步可转查其它独立业务链如合同状态/归档重试，不再围绕已经覆盖的客户创建许可重复扫描。长期质量目标保持active，未声称全系统完成。

## 2026-10-31 用户拒绝增加新建客户按钮限制（关闭体验选择）

- 用户明确指出新建客户不应卡得太死，不批准原“无customer:create时禁用按钮”建议。该待选项关闭，不再请求同一选择，不修改按钮为隐藏/禁用，也不新增权限限制。
- 当前代码证据seed.py SALES_PERMISSIONS含customer:view/create/update，MANAGER_PERMISSIONS继承SALES_PERMISSIONS，管理员路由特例放行。此前view-only专用账号是本机人工构造的权限边界夹具，不能将其结果描述为正常业务员不能创建客户。已向用户纠正这一上下文并撤回限制建议。
- 本轮未证明当前生产正常业务员缺创建权，没有为解决人工测试情形修改生产角色/后端鉴权或扩大他人数据访问、客户分配权限。用户期望正常业务账号可顺畅建客户已同步QUALITY_GOAL；如后续发现真实角色遗漏，应以实际配置证据处理，不能将种子当作生产全角色证明。
- 无应用修改，未重复测试或部署；Q-105已按先前批准本地修好及验证，Q-098至105八项仍本地。Q-096/Q-069/Q-075旧待决定不随本项关闭而被批准；长期目标仍暂停状态，本次只处理用户明确的说明/纠正，不自行恢复全面审查。

## 2026-10-31 Q-105获批修复完成（本地验证，未部署）

- 用户在通俗说明“10件中排4件，回执异常后再保存会多排4件”后明确回复“可以”，批准同次表单稳定请求键、复用现有防重表、先本地验证不部署；覆盖下节待决定状态。QUALITY_GOAL已同步。用户随后问前面待决定项，已说明并重新呈现只读新建客户按钮选择，该体验项仍未批准，没有夹带修改。
- 后端ShipmentBatchCreate增加可选request_key（最长128），create_shipment先走原权限与当前订单范围/行锁，再用用户+动作+稳定键保留结果，指纹含order_id及全部计划业务字段，避免同键跨订单回放；同键同内容返回原batch_id/batch_no，同键异内容409，不同键仍允许另排批。取消订单仍不能创建新批；已成功结果可回放其历史创建事实而不再次执行。原无键调用保持兼容，支持X-Request-Key。无新增数据库字段/迁移，复用request_keys。
- 占位、批次/明细、跟单节点、审计与结果在同一事务；提交前异常由既有get_db回滚（不另起事务删除已成功记录），真实提交后回执异常保留done结果可回放。前端每次主动打开排批表单生成键，失败保留键/输入，成功清空，下次主动新建使用新键；API类型传递request_key。未改数量、实际发货、历史批次或权限规则。
- 新增登记check_shipment_request_key，仅本机隔离PG防呆与关闭外部副作用。真实JWT/ASGI/PG验证：实际commit后抛错500再重试原批次；最后审计抛错500后批次/审计/键均零、同键重试成功；同键改数量/跨订单均409且无新批；不同键同内容另建，无键老调用及header键正常；订单归属转走后旧键403不能回放。两个请求同键并发，首请求暂停于审计、独立pg_blocking_pids确认第二请求真实等待订单锁，放行后两者200相同批次，SQL批次/审计/键各1，10秒界限内完成。api-green.log三PASS、exit0；不能据这一锁场景宣称全系统死锁已穷尽。
- 隔离浏览器正式构建与内存API模拟已存但500：失败后原输入保留，第二次请求完整body/键相同且只一份模拟批次；成功后主动新开相同数量键不同、第二批正常。已纳入ops/confirmation_browser_smoke.mjs永久回归，聚焦browser.log及完整full-browser.log退出0；完整原有浏览器检查亦通过。模拟API不冒称后端联合端到端，真实后端由前述独立PG验证。
- 前端生产构建（含TypeScript）通过，既有lottie eval等警告保留；完整后端1618 passed in15.34s，应用/schema/PG脚本pyflakes、node语法、git diff --check通过。证据ops/backups/shipment-create-fix-20261031/，隔离PG正常停止，浏览器/profile/HTTP正常清理。无生产写入/配置变更、依赖或DDL变化，未提交推送部署，前七项有效修改保留。
- 当前Q-098至Q-105共八项本地修复已验证，尚未发布。局限：键只在本次页面表单生命周期内保留，主动关闭重开/整页刷新按新表单处理；不对历史重复批次自动清洗，不按相同内容永久合并合法分批；多订单交叉键并发及全部生产锁图未穷尽。只读创建按钮、Q-096/Q-069/Q-075仍待原选择。长期质量目标未完成，本轮完成用户已批准Q-105工作。

## 2026-10-31 Q-105排批计划提交成功但响应异常后重试新增批次（待方案决定）

- 上轮实发提交故障三场景全量快照补验为实质进展，本轮恢复长期目标/规则/质量文件/工作区，检查其明确未覆盖的“计划创建”而非同一实发动作重复扫描。保留Q-098至104本地补丁，新建客户按钮体验尚待回答，不实施。
- Q-105/P2：order/router.create_shipment及service.create_shipment_batch每次创建新批号，只有未计划数量余额及(order_id,batch_no)唯一性，没有同次请求键。前端OrderDetailPage createShipmentMutation约527行发送items，无请求标识；只onSuccess关闭表单/清输入/刷新，onError仅Toast、保留表单；isPending挡处理中点击，不能辨认提交成功但回执丢失后的人工重试，默认mutation无自动重试。shared/api/order.createOrderShipment也是裸POST。此为代码调用链，不称本轮已浏览器点击复现。
- 实际证据仅本机55441/crm_iso_analytics_20261030_test，正常admin JWT/真实ASGI/PG，关闭推送/调度/Stream。ORM随机客户/生产中CNY订单10件×1元/定制明细。正式POST计划4件，AsyncSession.commit钩子先实际PG提交再一次抛RuntimeError，HTTP500；独立SQL一批planned4/shipped0、成功排批审计1。恢复钩子后完全相同JSON重试200，SQL两不同batch_id/批号1和2，各4件，计划合计8、审计2。并非同批重复实发或部分提交。
- 第三次相同4件严格400“超过还可安排”，SQL批次数/计划量/审计均不变，正式GET shipped_exact0/remaining_exact10。既有数量闸正确，不能夸大为超计划总量/超发；实际影响为弱网后同次意图可能多排一批及其独立跟单节点。同内容确实也可代表主动两批，不能按内容永久去重或自动删除历史批次。
- 只读SSH确认当前应用3c4291d、order/router原SHA4d3e90a…及order/service原SHA45d29a4…与对应已部署基线一致；本地前轮修改仅动态金额，与本轮创建路径无关。server-readonly.log留证，不操作生产数据。probe.py/result.log含预期故障栈、提交/重复SQL证据及余额保护PASS，exit0；隔离PG正常停止，无应用修改，不重复1618项/构建，diff通过。
- 推荐方案A：前端每次新建表单生成稳定键、失败保留、成功/主动新建换键；后端复用现有core.idempotency/request_keys表，范围含用户/动作/订单、同键同内容回放原批号，同键异内容409、不同键同内容仍允许正常分批，权限/当前可见性需先校验。无需新表/字段迁移，但每次多保存一条防重记录及相关读写，需验证并发等待/回滚/已成功后回放；不擅自承诺无代价。方案B保持现状，失败后刷新人工检查，避免引入新处理但偶发多排风险保留。
- 已按AGENTS.md业务/事务/交互取舍请求本项决定（先本地修复验证、不部署），未收到答复不实施。没有把旧回款幂等授权扩成该模块授权、不删除已有批次。Q-105待选择，Q-098至104七项本地待发布，新建客户体验/Q-096/Q-069/Q-075仍原等待；下一步可在等待时转独立业务异常链，长期目标未完成。

## 2026-10-31 实发登记提交前后故障与重试完整性补验（未发现新缺陷）

- 上轮Q-104确认卡两类红绿/权限组合/1618项为实质进展。本轮读取目标原文/规则/质量文件/工作区，转查不同高风险链：正式批次实发登记失败/重试。现有顺序/并发发货数量和完成闸门证据不代替本轮提交边界验证，保留Q-098至104全部本地补丁，待选择体验不实施。
- 当前ship_batch先锁订单/批次、重读数量与状态；服务完成实发数量、批次日期/运单、订单shipped、跟单及order:batch_ship事件，路由最后审计/commit/投递。已shipped批次原规则400拒绝再次登记，不擅改成成功幂等或新增发货请求键。
- 仅本机55441/crm_iso_analytics_20261030_test，正常admin JWT，禁推送/调度/Stream。每组ORM随机客户/生产中CNY10元订单与数量10/单价1的定制行，正式API计划4件，再正式ship输入实际3件/业务今天/隔离运单。未走完整报价生产流程，不代表ERP/MES外部履约验证。
- 三个进程内故障钩子：最后ship_batch审计抛错（先flush并实际同事务SQL证明批次shipped/数量3），AsyncSession.commit先真实PG提交后一次抛错，以及提交后dispatch入口抛错、不真实发送。每组正式HTTP500，钩子finally恢复，不称真实网络断流/进程强杀/第三方失败。
- 审计故障后独立会话sales_orders/order_shipment_batches/order_shipment_batch_items/order_milestones/order_status_history/followups/business_events/notifications/audit_logs九类关联全行to_jsonb（含时间/ID/JSON）等于提交前；恢复钩子后正式ship200。另两组已实际提交，批次与订单shipped、实发量3、运单原值，成功实发审计和batch_ship事件各1。三组均再正式重试两次，严格400含“已登记发货”，每次九类完整快照不变。
- 正式GET shipments三组均shipped_exact=3、remaining_exact=7、all_shipped=false，证明部分实发没有重复累加或误认为全部完成。证据ops/backups/shipment-failure-20261031/probe.py/result.log，三PASS、exit0（预期RuntimeError故障栈保留）。无新Bug/应用修改，不新增镜像测试或重复1618项/构建/浏览器；diff通过、隔离PG正常停止，无SSH/生产修改/提交推送部署。
- 边界：本机顺序、单行/单批、确定故障点，未覆盖真实数据库连接中断/批次创建提交回执丢失、多个并发批次、真实物流外部回执及全系统锁图；不从“实发不重复”推断计划批次创建已有请求幂等。下一步可独立检查计划创建弱网重复的既有数量上限/前端防重机制，确认实际影响再判断是否需要新策略选择。Q-098至104七项仍本地，既有待选择不变，长期目标未完成。

## 2026-10-31 Q-104 AI报价审批确认卡大额少一分（服务器确认，本地修复）

- 上轮Q-103正式审批通知/动态两出口红绿及全量为实质进展。本轮恢复目标原文/质量文件/规则/工作区，选择最后一处已定位同类静态线索agent.runtime.action_display，而非重复订单/报价通知验证。原新建按钮体验与Q-096/Q-069/Q-075未获新决定，保留原待选择和前六项有效本地修改。
- Q-104/P2：agent/runtime.py约752行request_quote_approval确认卡对QuoteVersion.total_amount先float再:,.2f。真实action_display/内存SQL与正式GET /agent/sessions/{id}/真实PG两类红例均出现报价99999999999999.99、fields报价金额¥99,999,999,999,999.98。仅展示文字，未执行AI写动作、未调用模型或更改报价审批状态/账本。
- 永久test_agent_quote_card_precision四金额（大额/1234.56/0.01/0）×四权限组合（本人可见/他人超范围/无quote:view/报价已软删），实际函数/SQLite SQL。修前仅可见大额一项失败，其余15通过，red-unit.log；无权组合本就不返回报价号/金额，不能将本轮精度Bug误称权限漏洞。
- 独立API：本机55441/crm_iso_analytics_20261030_test，关闭推送/调度/Stream，正常admin JWT，ORM合成客户/四金额版本、本人AgentSession及四个approval_required动作。只GET会话详情，数据库Decimal金额被卡片float格式丢分，red-api.log。没有调用chat/stream/confirm或真实模型；夹具不代表经过模型提议的完整工作流。
- 只读SSH当前应用3c4291d，runtime.py SHA256 45c092240ebb65bdf20b17b5397f07a9602ee1d0429ee54b23723412d977a051与本地修前完全一致，sed定位同句，server-readonly.log。无生产会话/数据读取测试、写入或部署。
- 唯一明确最小修复：移除float直接格式现有Decimal，卡片字段仍是同名字符串；数据范围/软删/查看权限/确认按钮/动作风险等级及执行规则不改，不清洗历史。前端CopilotDrawer约234行及AgentPage约204行直接显示field.value，无再次Number转换（仅源码调用链检查，不称浏览器实测）。
- 绿例：永久16项通过0.32s；真实正式GET四金额均精确，AgentAction状态全部approval_required、AgentExecution计数0，证明读取未触发执行（green-api.log）。完整后端1618 passed in15.18s，应用/测试pyflakes与diff通过；无前端修改不重复构建/浏览器或冒称CI。证据ops/backups/agent-card-money-20261031/，隔离PG正常停止；未提交推送部署。
- 边界：真实接口仅管理员，权限四组合为服务层真实SQL单元；未把组合单元当全角色HTTP矩阵，未覆盖模型真实生成/过期快照或所有AI字段。全modules同一float(amount/price/cost):2f搜索无剩余匹配，只能排除该表达式形式，不能推断全部金额路径完成。Q-098至Q-104共七项仍仅本地，长期质量目标未完成；后续可转查其它金额聚合或业务并发链，避免围绕已修文案反复扩大结论。

## 2026-10-31 Q-103报价提交审批通知/动态少一分（服务器确认，本地修复）

- 上轮Q-102转单入口真实红绿例/六组及全量为实质进展。本轮读取目标原文/质量文件/规则/工作区，验证此前quote.router提交审批的两处同类静态线索，不用订单场景代替报价审批实际证据。原新建按钮体验与Q-096/Q-069/Q-075未获新选择，不实施；保留前五项本地修复。
- Q-103/P2：quote/router.py submit_approval约1495/1517行，待审批通知及客户系统动态分别float(version.total_amount)再:,.2f。合法99999999999999.99报价正式提交后数据库仍.99，四条审批通知及一条客户动态都写99,999,999,999,999.98。仅文案精度，不声称报价账额或审批阈值在本轮被修正，规则引擎summary内部数字是另一契约路径。
- 仅本机55441/crm_iso_analytics_20261030_test与禁推送/调度/Stream。随机专用用户/全量role仅quote:view/manage，专属PricePermission最低毛利15%（不改既有角色/全局配置），正常JWT。ORM合成随机客户/当前draft/not_submitted CNY报价、完整成本定制明细（成本等于售价，零毛利触发现有15%价格审批）、已确认零运费；正式POST submit-approval产生pending实例。独立SQL通知/动态/版本金额严格红例（red.log），无真实消息投递或真实业务数据。
- 只读SSH实际应用3c4291d，quote/router.py SHA256 1448d9f76dc1986c6839746097fa31a038e25dba3b856f07b5f473b61e460ca8与git HEAD原始同文件完全一致，sed两float语句确认（server-readonly.log）。本地shasum并行读取时已是修改后040ad3…，未拿修后hash冒充修前；最终用git show HEAD确定原始1448d9…与服务器一致。无服务器写入或部署。
- 最小纯收益修复两处移除float，直接格式现有Decimal，保留零/空值兜底、千分位/两位小数、通知收件人、既有价格审批/权限/币种/事务，历史通知动态不回写。新增check_quote_submission_precision并登记check_suites，显式仅本机隔离PG防呆与副作用关闭；夹具直接构造待审批事实，不冒称完整创建核价端到端。
- 永久正式HTTP三组大额/1234.56/0.01均pending，数据库报价金额正确、所有本轮待审批通知完整文字及客户动态严格匹配；每版本实例数1，再次正式提交严格400且已提交提示、实例/通知/动态数量内容均不变。green.log三PASS、exit0。完整后端1602 passed in15.01s（full.log），应用/脚本pyflakes与diff通过，未改前端不重复构建/浏览器。证据ops/backups/quote-submit-money-20261031/，隔离PG正常停止，未提交推送部署。
- 边界：本地真实ASGI/PG与站内通知行，未实测外部发送/全部审批分档边界、免审分支/主管角色全矩阵、历史通知重算。agent.runtime报价审批确认卡float仍仅静态线索，需独立实际序列化/可见性调用再判，未顺手修改。Q-098至Q-103共六项修复仍仅本地未发布，长期目标未完成；后续可核对AI确认卡精确金额及权限过滤，不调用真实模型或执行业务动作。

## 2026-10-31 Q-102报价转订单客户动态少一分（服务器确认，本地修复）

- 上轮Q-101手工动态红绿例/真实建单及全量为实质进展；本轮读取长期目标原文/规则/质量文件/工作区，针对此前明确待调查的另一正式入口create_order_from_quote。新建按钮体验选择未回复，不实施；不把长期续跑等同新发布授权。
- Q-102/P2，order/service.py约412行对version.total_amount先float后:,.2f，正式POST /quote-versions/{id}/convert-to-order?confirm=true落库订单/全款应收均99999999999999.99，但客户followup动态显示99,999,999,999,999.98。影响文字精度，不是账额/应收金额少计；不复用Q-101手工入口证据替代本次真实转单。
- 本机55441/crm_iso_analytics_20261030_test，正常admin JWT，禁推送/调度/Stream。ORM合成随机客户与accepted/approved CNY当前报价、sent_at/accepted_at、明确完整成本的定制行（100×999999999999.9999、成本明确0）及已确认零运费；正式HTTP转单，独立SQL核对订单/应收/动态，red.log严格失败因.99→.98。夹具直接构造已接受事实，不冒称完整核价审批发送接受端到端。
- 只读SSH确认应用3c4291d，order/service.py SHA256 45d29a4b58ac1622002a7d70e4309b0799566369431dcd2522f3dea1fdb4bbb1与本地原始文件一致，sed定位相同float语句，server-readonly.log。未在服务器创建报价/转单或改正式数据。
- 纯收益最小修复：删除一次float转换，直接格式现有Decimal，保持人民币/千分位/两位小数/收件人/事件键/权限/金额算法/事务全不变，不清洗旧动态。应用仅order/service.py一行；扩展既有已登记check_order_progress_precision，不新增重复套件。其手工三组保留，再增转单三组99999999999999.99/1234.56/0.01，逐组SQL订单额=全款应收、完整动态精确、order:create事件数1；正式重复转单409、SQL同版本订单仍仅1。
- 永久脚本两入口共六组通过（green.log、exit0），完整后端1602 passed in15.45s（full.log），应用/脚本pyflakes及diff通过。无前端变更，不重复构建/浏览器或冒称CI。证据ops/backups/quote-convert-progress-money-20261031/，隔离PG正常停止，无提交推送部署。
- 残余：未覆盖confirm-win商机整条流程（同一service但未实际走入口）、拆期计划/真实主管推送或历史动态修正；quote审批通知/动态与agent确认卡的静态同类线索仍待各自触发，不能当已确认。Q-098至Q-102五项本地修复待具体发布授权；新建按钮体验与Q-096/Q-069/Q-075仍待原选择。下一步可验证报价提交审批实际通知金额，而非反复重跑已通过订单入口；长期质量目标未完成。

## 2026-10-31 Q-101手工建单客户动态金额少一分（服务器确认，本地修复）

- 上轮只读客户创建403与UI失败闭环为实质进展；创建按钮体验问题已发人工选择、未获回复，不实施。恢复目标原文/质量文件/规则/工作区后，转查Q-100相邻但不同的订单自动动态金额出口，未把同一回款问题再次编号。
- Q-101/P2，order/router.py create_order约185行用float(order.total_amount):,.2f组成record_and_notify内容。真实手工建单合法明细quantity=100、unit_price=999999999999.9999，SQL订单total_amount=99999999999999.99，自动followups.content却为【系统】SO…金额¥99,999,999,999,999.98（手工建单）。订单金额正确，仅自动过程文字少0.01；主管通知排队沿该内容，但本轮不冒称实测外部投递。
- 仅本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream；正常admin JWT，ORM合成随机客户/可用产品SKU，正式POST /orders让服务真实计算数量×单价，不预写订单总额。独立SQL查账额及followup完整文案严格断言红例，red-exact.log。初始脚本误用返回id（实际order_id）和少算【系统】前缀，分别核对契约/实际落库后修夹具，保留日志，最终修前失败仅因.99→.98，不凭格式化纯函数自证。
- 只读SSH当前应用3c4291d、order/router.py SHA256 4d3e90a368dee9d7cf2e2a122185a71f1877d48965cb691595a5b8f318ed3bff与本地原文件一致，sed确认float语句，证据server-readonly.log。未在生产建单/改数据/重启或部署。
- 唯一明确修复是移除float，直接对现有Decimal用原千分位/两位小数格式；无金额计算/权限/通知接收人/数据库/历史动态清洗变化，无新查询/依赖/事务取舍。应用只改order/router.py一行。新永久check_order_progress_precision显式本机隔离PG防呆、独立导入/关闭外部副作用，登记ops/check_suites；不要把临时runpy/globals探针直接视作可静态检查永久脚本，首次pyflakes暴露动态导入未识别后已替换显式导入，最终无静态错误。
- 永久脚本正式POST三组99999999999999.99/1234.56/0.01，逐组SQL金额、完整动态文字及order:create唯一事件数1严格通过（permanent-green.log、exit0）；完整后端1602 passed in15.78s（full.log）、应用/脚本pyflakes及diff通过。无前端改动不重复构建/浏览器。证据ops/backups/order-progress-money-20261031/，隔离PG已正常停止，无提交推送部署。
- 相似静态线索仍待调查：order/service报价转单动态、quote/router审批通知/过程文字、agent/runtime报价审批确认卡有同类float金额格式，但本轮没有各自正式触发证据，未作为已确认Bug或顺手改动。手工建单响应旧total_amount数字字段也是独立契约范围，本轮不靠这个输出断言精确或擅加字段。下一步可优先用已接受完整成本报价正式转单验证同类动态，不重复已通过手工建单三组。
- Q-098/099/100/101四项修复仅本地，未提交推送部署。新建按钮体验与Q-096/Q-069/Q-075保持原待选择，不以自动续跑替代批准；长期目标未完成。

## 2026-10-31 只读账号新建客户实际失败闭环补证（体验方案待决定）

- 前轮前端权限刷新已通过，另观察到无customer:create仍显示新建客户；本轮恢复长期目标/规则/质量文件/工作区，将该观察补成实际提交证据，不扩张生产发布授权或自行收紧权限。
- 隔离浏览器正式frontend/dist客户列表，内存API提供salesperson仅customer:view，点击现有新建按钮、填写随机隔离名称并点击创建，模拟正式40301/“无操作权限：需要 customer:create”。严格断言恰一次POST、Toast明确缺权、原Modal及输入保留、无创建成功提示、登录token未清除。证据ops/backups/customer-create-readonly-20261031/browser.mjs/browser.log，PASS/exit0，profile与HTTP finally清理。此为模拟API UI，不称联合端到端。
- 独立本机55441/crm_iso_analytics_20261030_test真实ASGI/PG补证：随机角色仅customer:view/file:view，正常登录旧JWT，管理员正式分配self角色；正式POST /customers仅name/request_key返回严格403与同提示，独立SQL该随机名称客户数及该操作者成功create客户审计数前后均0；原GET /customers仍200。证据api.py/api.log，PASS/exit0。创建/登录/角色夹具限隔离库，不修改真实账号；推送/调度/Stream关闭、PG正常停止。
- 结论：既有后端防越权和前端错误处理均覆盖，不是“权限同步无效”、越权创建或假成功。已确认界面体验是只读用户可以填完表才知道缺创建权，代码CustomerListPage普通新建Button与createMutation.onError对应。是否提前禁用涉及既有保留入口与交互取舍，未自动隐藏/禁用、不编号成未经口径确认的安全Bug。
- 已按AGENTS.md用户体验取舍规则请求人工选择：推荐保留新建入口但无customer:create时禁用并明确原因（收益避免白填，代价不能先打开表单）；替代保持现状（可查看表单，接受提交后拒绝）。当前待回答，不将沉默视作批准；暂不实施或部署。可继续独立审查，不将这一项等待当成全项目阻塞。
- 本轮无应用修改，不重复1602项/构建/CI；git diff --check通过，未SSH/生产变更/提交推送部署。Q-098/099/100仍本地待发布，Q-096/Q-069/Q-075维持原待决定。后续本项获明确选择后再按范围补回归；未覆盖其余导入/编辑按钮或真实生产角色，长期目标未完成。

## 2026-10-31 前端权限刷新补验收尾及创建按钮待调查

- 用户本轮要求“做完”，完成已开始的刷新链检查后收尾，不继续扩展新审查方向。上轮真实后端多角色动态权限矩阵已有证据，本轮区分为全新隔离headless浏览器、本地frontend/dist和内存API，无数据库/生产账号。
- 检查useCurrentUserSync既有启动拉取/60秒轮询/窗口聚焦及登录代保护、RoutePermissionGuard和menu已批准“保留入口、缺权说明原因”策略，未擅自隐藏菜单或改轮询。实际四次/auth/me：持久化旧admin启动被真实模拟salesperson替换；撤customer:view后刷新，客户页面明确拒绝、菜单仍在、业务页面及其新建按钮不再挂载；恢复view后刷新页面恢复；恢复create后刷新现有入口正常。浏览器退出0，profile/HTTP finally清理。没有实际等待60秒或测试窗口聚焦，不把源码配置当本轮实测。
- 待调查UI项：view-only时，localStorage已确认不存在customer:create，CustomerListPage约513行仍是无权限条件的普通“新建客户”Button；实际按钮仍呈现，不能将按钮存在认定权限同步未生效。后端POST /customers明确require_permission(customer:create)。本轮没有提交创建表单/真实后端联合测试，不声称越权创建或新增已确认安全Bug；具体界面后果/权限提示、是否与既有保留入口策略一致，需后续完整复现及人工交互口径选择。没有自动改成隐藏/禁用或绕过后端。
- 初始“view-only按钮应消失”断言失败日志create-button-observation.log保留，后续测试明确标记OBSERVED实际行为，未伪装为原断言通过。证据ops/backups/auth-permission-browser-20261031/probe.mjs/result.log；三条PASS与一条观察，不能写成四项按钮权限测试全绿。
- 无应用修改，无新增后端/构建/全量重复测试，无服务器操作/提交推送部署，diff通过。Q-098/099/100三项修复仍仅本地且此前验证通过；本轮按用户要求收尾，不声称长期目标全部完成，也不自行暂停长期goal。后续若恢复审查可补本项创建失败闭环或转其它高价值模块，Q-096/Q-069/Q-075保持原待决定状态。

## 2026-10-31 多角色动态撤权、范围收缩与账号启停真实补验（未发现新缺陷）

- 上轮确认阶段三类故障完整快照验证为实质进展，本轮恢复长期目标/规则/质量文件/工作区，保留Q-098/099/100本地补丁。历史已覆盖鉴权函数内存SQL、登录/改密/退出并发；本轮新证据限定为正式角色管理接口与当前PG多角色叠加/用户启停，不重复把旧JWT实时查询机制记成新修复。
- 当前get_current_user每请求查User.active、有效登录会话、启用角色及权限，resolve_data_scope取启用角色最大范围；用户停用只改status，原实现没有吊销会话。先明确现有行为再验证，不凭“旧JWT里曾有权限”认定越权，也不擅自增加启用后强制重登规则。
- 本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream；ORM合成两账号/独立对照角色、测试账号self与all双角色、各自私海客户，真实登录后始终用同一原JWT。管理员正式PUT范围all→self→all、PATCH broad角色disabled→active、两个角色PUT权限移除customer:view→恢复、PUT用户角色摘broad→恢复，各阶段GET me与两个客户详情验证：self不能读别人、all可读、两角色均撤查看权则自己客户也403、停用/摘掉broad不再提供全量范围。对照账号独立角色每阶段正常读取自己的客户。
- 共10阶段（含初始及重新启用）×4主要读取，另停用期间me/自己客户/refresh均401，正确密码新登录严格403“账号已停用”。正式enable后，同一原JWT恢复使用；独立SQL登录会话ID/revoked_at与启停前完全相同、仅1行且未吊销，符合现有“临时停用阻断访问、启用后保留未过期会话”实现，不把它升级为已确认漏洞或擅自清会话。本轮未实际等待令牌过期，亦不保证已过期会话可用。
- 首次对照账号误共用被撤权角色，其403是预期传播，非应用缺陷；改为独立对照角色后复跑。第二次把停用后的密码登录误预期400，核对正式路由明确403后改为严格403与精确提示；两份初始日志保留，不更改应用/放宽断言。最终ops/backups/auth-role-live-20261031/probe.py/result.log含10阶段PASS与最终PASS、exit0；不记录JWT/密码哈希正文，不操作真实账号。
- 无新Bug/应用变更，不重复1602项或构建/浏览器，不声称远端CI。git diff --check通过，隔离PG正常停止；未SSH/改生产/提交推送部署。边界：本机ASGI/PG顺序管理变更，未覆盖进行中的长请求、撤权与写入并发、部门树重组、真实第三方认证或前端权限缓存刷新体验。下一步可核对前端刷新后的菜单/禁用提示与动态权限接口契约，先排除现有测试而不自行扩展权限。长期目标未完成，Q-098/099/100未发布，Q-096/Q-069/Q-075待原选择。

## 2026-10-31 回款确认提交前后故障与重复操作补验（未发现新缺陷）

- 上轮Q-100通知金额红绿例及真实服务器同码确认/1602项为实质进展。本轮恢复长期目标原文/质量文件/规则/工作区，保留Q-098/099/100全部本地修复；选择之前创建回款响应丢失、凭证登记补偿未覆盖的“财务确认最后阶段”故障，而非重复普通终态顺序测试。
- 调用链payment.router.confirm：统一锁/待确认检查→确认事实→分配→逐期重算→同步跟单节点→过程事件/通知→审计→序列化→commit→投递。ensure_payment_pending对已确认返回现有400/40002；不能擅自把确认重试改成成功重放。真实通知投递仍关闭，本轮不引入新事务或幂等策略。
- 本机55441/crm_iso_analytics_20261030_test，正常admin JWT及真实ASGI/PG。ORM合成随机客户和每组CNY100.01订单，正式API创建应收/待确认回款。三种进程内单点钩子：最后confirm审计抛错（钩子先flush并用同事务SQL证明confirmed及100.01分配已经产生）；AsyncSession.commit先真实提交再一次抛错；commit后的dispatch_pending入口抛错、不进行外部投递。仅模拟指定异常位置，不能宣称真实网络断流、真实第三方故障或生产复现；全部钩子finally恢复。
- 审计故障HTTP500后，独立会话对sales_orders/receivable_plans/payment_records/payment_allocations/order_milestones/followups/business_events/notifications/audit_logs九类关联行to_jsonb完整快照（含ID/时间/JSON）逐项等于提交前，证明本轮已flush变更整体回滚；恢复钩子后正式confirm200成功。其余两组HTTP500但实际已提交：回款confirmed/confirmed_at存在、应收paid、100.01单份分配、定金/收款两个派生节点日期正确、confirm审计/业务事件/回款确认通知各1。
- 三组均在去除钩子后重复confirm两次、reject一次，严格400且明确“只有待财务确认…当前已确认”，每次九类完整快照不变；正式finance-summary精确已收100.01。没有重复计款、覆写确认时间、增加成功审计/事件/通知。首次测试夹具误预期422，现有AppError默认400代码及真实响应核对后改为严格400，保留initial-status-expectation.log；未改应用状态码/放宽为任意拒绝。
- 证据ops/backups/payment-confirm-failure-20261031/probe.py/result.log，三PASS、exit0（含三次刻意RuntimeError预期日志，不当作未解释故障）。未新增Bug或应用修改，不添加镜像单测/重复1602项/构建。git diff --check通过，隔离PG正常停止；无SSH/生产写入/实际投递/提交推送部署。
- 边界：有序隔离故障，不含进程强杀、实际PG连接中断、真实企微渠道失败、所有客户改派锁图或前端500后刷新体验。提交后失败时客户端仍可能先看到500再被告知已确认，数据正确不等于交互完全理想；本轮不据注入点擅自认定新的实际故障路径或更改成功契约。Q-098/099/100仍仅本地，Q-096/Q-069/Q-075待原选择。后续可转查真实账号停用/角色变更后的既有会话失效与数据范围实时性，先排除既有覆盖；长期审查未完成。

## 2026-10-31 Q-100回款确认通知大额金额少一分（服务器确认，本地修复）

- 上轮打样删除/恢复共享组合真实API/PG验证为实质进展，本轮恢复目标原文/质量文件/工作区，转查资金链。最初提到回款撤销，核对历史与正式API后确认没有已确认回款撤销能力且已审查，不新增该功能、不把既有设计记Bug，继续沿confirm查通知与金额事实一致性。
- Q-100/P2，backend/app/modules/payment/router.py confirm_payment约1162行：收到金额Decimal先float再:,.2f。合法Numeric(16,2)边界99999999999999.99被格式成99,999,999,999,999.98，导致通知与数据库/财务概览差0.01。仅通知文案精度，未据此宣称账本或分配少计；不新增币种或改变金额口径。
- 红例：仅本机55441/crm_iso_analytics_20261030_test，禁推送/调度/Stream，正常admin JWT，ORM合成随机客户/CNY订单，正式创建应收/登记3笔/确认2笔（沿既有Q-095财务概览精度场景）；独立SQL金额与概览exact均正确，通知两条均为.98，严格完整文案断言失败。证据ops/backups/payment-notification-precision-20261031/probe.py/red.log。未执行真实企微/钉钉发送、真实财务动作或清库。
- 只读SSH确认服务器应用3c4291d、payment/router.py SHA256 93f83bc2bd28ee91498fc64810610b0d7967aebf7e80bd4f84c719db80a65f8c与修复前本地完全相同，sed定位同float语句；server-readonly.log留证。首次尝试服务器rg未安装，未当作业务故障，随后用sed完成定位与日志保存。没有服务器写入/重启/部署。
- 自主修复依据：唯一期望是已有确切Decimal按原千分位/两位小数输出，移除一次不必要float转换，无新查询/锁/权限/数据库迁移/金额重算/通知渠道调整。未改通知收件人或增加金额泄露面，不修改历史通知。仅payment/router.py一行应用改动；扩展已登记check_order_finance_precision，在原有实际两次确认后断言两条完整通知精确为.99，复用真实回归而非增加镜像格式化测试。
- 绿例：正式永久脚本大额场景通过（green.log），另外以同链路验证0.01与1234.56正常金额，每组两次确认及SQL/概览/通知相符（cent.py/log、ordinary.py/log）。完整后端1602 passed in17.11s（full.log），应用/脚本pyflakes与git diff --check通过；无前端变更，不重复构建/浏览器或冒称CI。隔离PG正常停止。
- 状态：Q-100本地修复与回归完成，未提交/推送/部署，Q-098/099原补丁保留。边界是本机ASGI/PG与站内通知行，不含真实第三方投递、全部金额格式路径、财务确认故障注入/提交回执不明；相邻payment/notification模块未发现同样金额float格式语句，不扩张为全系统排除。下一步可独立补验确认阶段故障与提交后重试，不擅自把重试返回业务拒绝改为新幂等契约。Q-096/Q-069/Q-075仍待原决定，长期审查未完成。

## 2026-10-31 打样软删恢复与独立共享附件组合补验（未发现新缺陷）

- 上轮客户交接非管理员共享矩阵已产生真实接口/SQL证据。本轮恢复目标原文/规则/质量文件/工作区，核对现有附件授权、sample删除恢复及Q-036历史回归；本轮新增场景是“同文件另有独立客户共享挂载”，不是重复报告打样自动跟进软删问题，也不扩张删除规则。
- file.access对sample委托get_visible_or_404（含deleted_at及原数据范围），通用文件对任一可见业务挂载放行；sample DELETE/restore只改软删标记并留审计。已检查询价入口：没有对应恢复接口，不能为测试擅增恢复功能；最终选择已有正式打样恢复业务验证组合，不声称本轮覆盖询价版本链。
- 本机55441/crm_iso_analytics_20261030_test及专用FILE_ROOT，禁推送/调度/Stream；随机owner/shared/outsider三个self角色真实登录，分别按既有file/customer/sample权限读取，owner额外file:manage/sample:manage。ORM合成owner客户/未提交pending样品及shared独立客户，正式multipart将两个TXT作为supplement挂打样，再由admin正式attach其中一份到shared客户；不冒充完整审批建样流程，也没有外部提交。
- 删除前/正式DELETE后/正式restore后，三阶段×三账号×八入口=72次主要读取断言（两文件各详情/下载/预览+打样单条附件列表/批量列表）。删除前owner读两份、shared仅共享份；删除后owner连原上传者身份也不能借打样挂载读取两份，shared凭独立客户挂载仍读共享份；恢复后owner原权限恢复。outsider始终不可读，额外尝试restore被拒，正常owner恢复200。使用同批登录JWT验证每次当前状态判定；合法文件返回字节逐次相同。
- 三阶段独立SQL核对sample.deleted_at与请求结果一致，两个FileRecord和三份BusinessFile整行to_jsonb（含ID/时间/分类）完全不变；成功删除/恢复审计各1，无权恢复未产生成功事实。证据ops/backups/sample-shared-delete-20261031/probe.py/result.log，9矩阵PASS及最终PASS，exit0。没有发现新Bug或修改应用，不重复全量测试/前端构建，git diff --check通过，隔离PG正常停止；未SSH/改生产/提交推送部署。
- 边界：本机真实API/PG、顺序删除恢复、普通TXT直接业务挂载，不含自动跟进附件（已有Q-036专项）、合同/报价归档、并发恢复/删除锁图、真实生产账号或所有部门范围。客户共享与本轮打样组合已有证据，下轮转向其他高风险业务状态/重试链，避免反复扫描相同文件路径。Q-098/099保持本地未发布，Q-096/Q-069/Q-075仍待原决定；长期审查未完成。

## 2026-10-30 客户交接共享附件非管理员真实权限矩阵（未发现新缺陷）

- 前轮隔离浏览器Blob预览补验为实质进展。本轮读取长期目标/质量文件/规则与当前工作区，转查此前管理员共享文件验证未覆盖的归属变化访问；不重复将Q-076归档原件问题计为新缺陷，不修改权限策略。
- 调用链：file.access.can_access_file逐一核对现存BusinessFile挂载的模块权限/实时负责人范围，任一可见即放行；存在关联但均不可见时不回退曾上传者。列表/批量列表分别用visible_object按当前可见客户过滤，不因共享一份文件泄露另一不可见客户的挂载。正式customer.transfer更新当前归属，访问请求不靠旧JWT缓存owner快照。
- 仅本机55441/crm_iso_analytics_20261030_test，关闭推送/调度/Stream。新增随机4账号及独立角色并真实登录：old/self有文件读写及客户查看/修改，new/self与shared/self只有文件/客户查看，no_module/all仅file:view且无customer:view。ORM只构造两个随机私海客户；old通过正式multipart上传TXT到甲，管理员正式attach同文件到shared负责的乙，避免用伪造关联替代共享流程。
- 三阶段×四账号×六读取入口共72次主要读取断言：正式文件详情/下载/预览、客户附件批量列表与甲/乙两个单条列表。交接前old/shared可读、新负责人不可读；管理员正式transfer甲给new后，old即使原JWT/上传者身份仍403/404，new正常读、shared沿乙关联继续读；管理员正式unlink乙挂载后shared失去文件访问，但仍能查看自己乙客户空附件列表。三个阶段no_module均不能读文件/客户附件，all数据范围未绕过模块权限。所有合法下载/预览字节与原文完全相同。
- 独立SQL确认交接实际owner_id变化、两挂载ID/客户ID保持；最终只剩甲挂载，FileRecord整行to_jsonb始终不变。没有永久删除文件、清库、生产账号或真实第三方操作。证据ops/backups/file-transfer-access-20261030/probe.py/result.log，12矩阵PASS及最终PASS、exit0；未发现新Bug/应用修改，不重复1602项或前端构建，diff通过、隔离PG正常停止，未SSH/提交推送部署。
- 验证边界：普通TXT客户共享挂载、self与缺模块权限/all组合、顺序交接/解绑，未穷尽部门树/合同归档/已删客户/并发鉴权与写入竞态。现有规则覆盖本轮场景，不能据此宣称整个文件模块无漏洞。Q-098/099仍本地未发布；Q-096/Q-069/Q-075仍待原决定。下一步可转查非客户来源业务附件在其源单软删/恢复后的读取一致性，先确认现有回归覆盖再选新场景。

## 2026-10-30 附件前端Blob预览与释放隔离浏览器补验（未发现新缺陷）

- 上轮Q-099收尾核对重跑专项并记录结果，本轮转查前轮尚未覆盖的前端实际消费链。api.client.blob取响应Content-Type创建带类型Blob；AttachmentPanel按响应MIME分支，text/*先blob.text再React pre文本节点，image/*只进img，PDF进既有阅读器iframe。后端CSP/nosniff响应头不会随createObjectURL自动转移，不能把服务端响应头当作Blob自身保护；本轮以实际渲染验证补证。ImageGallery与SkuImageCell也只有图片渲染入口，没有HTML注入调用。
- 仅本地生产构建frontend/dist、随机端口内存API、全新headless浏览器profile，无数据库/生产账号。使用正式客户详情?tab=files与真实附件组件/axios/blob链；模拟API四组裸内容响应沿后端实测安全类型及响应头。正文只有本地标记HTML/脚本，不含外部URL，无真实消息/第三方业务请求。此为前端模拟API浏览器测试，与上轮真实后端ASGI/PG证据分开，不冒充前后端联合端到端。
- literal.txt/text/plain;charset=utf-8：实际pre保留完整<h1>/<script>文本，没有img/iframe、没有创建BlobURL，也没有执行本地脚本。disguised.jpg/image/jpeg正文HTML：实际img加载失败，不渲染HTML、没有执行。fake.pdf/application/pdf正文HTML：实际进入PDF iframe，Blob类型application/pdf，无本地标记注入/父页脚本执行；不声称无效PDF能正常阅读。real.png/image/png：真实img完成且naturalWidth>0，正常图片可见。三份非文本Blob均在关闭对应预览后实际调用revokeObjectURL，waitFor独立断言全部通过。
- 证据ops/backups/file-preview-frontend-20261030/probe.mjs/result.log，四PASS、进程exit0；首次夹具未选附件tab超时保留initial-tab-not-selected.log，改为现有?tab=files后通过，未修改应用来绕过失败。无新增Bug或应用变更、不重复全量后端/构建；git diff --check通过，浏览器和本地HTTP服务器/profile均finally清理。Q-098/099原有本地补丁保留，未提交推送部署，未操作服务器。
- 限制：本机单一Chromium、管理员模拟API、四组确定内容及短时间观察；未覆盖真实PDF内部交互/脚本、移动浏览器、所有文件嗅探组合或非管理员权限。不能由这四例推断全部文件类型安全。下一步转查尚未充分覆盖的非管理员共享附件归属变化访问矩阵；Q-096/Q-069/Q-075仍待原选择，长期目标未完成。

## 2026-10-30 Q-099选择回复收尾核对（本地完成，未部署）

- 按用户“提示刷新后再操作”选择核对现有补丁：等待客户锁后重查联系人当前归属，改挂时409明确提示，先拒绝再清主位；刷新后的正常重新选择保留。
- 本轮重跑联系人专项4项通过（0.28s），git diff --check通过；核对既有真实隔离PG两个改挂入口的409/刷新重试200证据及完整后端1602项通过日志。未将历史测试冒充本轮重跑，也未做生产数据写入。
- QUALITY_GOAL和前述Q-099完整记录已同步；Q-098/099仍仅在本地，未提交、推送或部署。本次不扩大到新审查问题。

## 2026-10-30 附件主动内容预览与下载响应头真实补验（未发现新缺陷）

- 前轮共享附件合并/解绑真实文件与PG验证为实质进展。本轮恢复目标原文/质量文件/AGENTS与工作区，转查普通附件内容安全，保留Q-098/099本地修复。现有file.router已明确危险MIME/后缀拒绝、规范化带参数类型、安全后缀兜底与nosniff/CSP，未因允许上传HTML直接报告同源XSS。
- 仅本机55441/crm_iso_analytics_20261030_test与专用FILE_ROOT，正常admin登录、真实multipart上传/预览/下载八组。内容为无外部URL的本地标记HTML/SVG、一个1像素PNG，不接收真实客户文件、不访问外部地址、不执行脚本。八组上传均200。
- isolated.html/text/html;charset、isolated.svg/image/svg+xml、disguised.png/text/html;charset、fake.svg/image/png四组预览均422且明确不支持在线预览，确认危险类型/后缀和带参数绕过被实际路由挡住。literal.txt/text/plain（正文是HTML）、disguised.jpg/image/jpeg（正文是HTML）、real.png/image/png、fallback.txt/octet-stream四组200，Content-Type分别固定安全主类型；均nosniff、Content-Security-Policy="sandbox; default-src 'none'"、inline filename*，响应字节等于上传原文。
- 八组正式download均200、Content-Disposition attachment，原字节完整，没有把可下载主动内容错误等同同源内联执行。伪JPEG允许返回图片类型并带沙箱，不能据“内容不是图片”单凭静态推断XSS；本轮未跑浏览器，不声称实测所有浏览器嗅探/脚本行为。PDF明确沿既有浏览器阅读器例外，本轮没有扩张白名单或随意加sandbox破坏PDF预览。
- 证据ops/backups/file-preview-content-safety-20261030/probe.py/result.log，八PASS、exit0。无新确认Bug/应用变更，不添加镜像测试，不重复1602项/构建/浏览器或冒称CI。diff通过，隔离PG正常停止；禁推送/调度/Stream，无SSH/生产数据/配置/服务变更、真实第三方、提交推送部署。
- 边界/下一步：本轮普通孤立个人上传/管理员正式HTTP，未覆盖非管理员权限、PDF内置脚本、移动浏览器及预览前端blob URL/iframe的全部组合。后续可跟踪前端实际预览是否保留服务端安全边界，先检查既有测试与真实触发再判问题；Q-098/099仍本地待发布，Q-096/Q-069/Q-075仍待既有选择，长期目标未完成。


## 2026-10-30 客户合并同一共享附件与单关联解绑补验（未发现新缺陷）

- 上轮合并失败/提交回执不明两组真实ASGI/PG/全行快照验证为实质进展。本轮恢复目标原文/规则/质量文件/工作区，选择其明确未覆盖的共享附件特殊关联；保留Q-098/099全部本地补丁。
- 初始“同一文件同时挂两客户，合并会撞重复唯一约束”怀疑被模型/迁移排除：BusinessFile仅有business_type/business_id普通索引，没有此唯一约束。不能把推测直接记Bug，亦不能为去重擅自删除挂载分类/备注历史。正式attach也未禁止不同业务对象共享文件。
- 本机55441/crm_iso_analytics_20261030_test与专用FILE_ROOT：ORM合成随机两客户，正常admin JWT/真实ASGI multipart上传一份无业务数据txt到来源客户，正式attach把同file_id挂目标，分别category=source_note/target_note。正式POST merge200；独立SQL两个BusinessFile ID/file_id/category/remark/created_at全部保持，仅来源business_id改目标；FileRecord整行to_jsonb完全不变。正常目标附件列表两条挂载都在，各自分类保留，不把保留两条不同分类挂载认定重复文件写盘。
- 正式通用下载两次字节与原始content完全相同，SHA256 1bb75d4374133ca320f204a2895348b476024c954f0f337047c71ac06a442b9d。正式DELETE来源迁入的business-file关联200后，目标原关联ID仍唯一存在、附件列表剩一条，再次下载字节相同、FileRecord整行未改。证明普通共享文件合并及单关联解绑不误删原文件；没有调用DELETE /files永久删除或把普通txt当受保护归档原件测试。
- 证据ops/backups/customer-merge-shared-file-20261030/probe.py/result.log，exit0。真实文件/PG/鉴权/API，但非生产用户/浏览器、未包含无权限账号或受保护合同/报价原件。禁推送/调度/Stream，无SSH/生产数据/配置/服务变更或外部真实发送。
- 无新确认Bug/应用修改，不新增镜像测试，不重复1602项/构建/浏览器或声称CI。diff通过，隔离PG正常停止；未提交推送部署。Q-098/099仍本地，Q-096/Q-069/Q-075仍待既有选择，长期目标未完成。共享附件访问权限在客户归属变化/合并后的非管理员矩阵仍未穷尽，可后续独立验证，不能由管理员下载成功推断全角色正确。


## 2026-10-30 客户合并失败回滚及提交回执不明补验（未发现新缺陷）

- 上轮Q-099获批本地修复/两个改挂入口实际锁等待绿例及1602项通过为实质进展。本轮恢复目标原文/AGENTS/质量文件/工作区，完成此前被人工选择回复打断的客户合并异常验证；Q-098/099补丁保留，无本轮应用修改。
- 调用链customer.tags.merge_customers按ID锁源/目标客户并重读，统一迁移关联/降级来源主联系人/合并标签/补缺字段、来源软删、插入合并日志；tags_router最后write_audit及commit。未仅凭“无中间commit”声称必定全回滚，使用真实API与独立SQL核查提交结果。
- 仅本机55441/crm_iso_analytics_20261030_test，正式admin登录/POST /customers/merge。每组ORM构造随机两客户（目标缺地址、来源有地址）、两边主联系人、来源任务/跟进/CNY123.45订单与标签；没有专属价/税号冲突，不选择任何未批准合并口径。夹具未走完整业务建单流程，不声称所有合并关联或权限端到端。
- 故障一：仅进程tags_router.write_audit在最后merge审计抛RuntimeError；钩子在真实同事务SQL确认联系人已迁到目标、来源已软删后才抛错。正式HTTP500，独立会话对customers/contacts/tasks/followups/sales_orders/customer_tags/customer_merge_logs/对应merge审计共8类所有行to_jsonb完整值（含时间）与基线逐项相等，证明真实批量迁移/字段补齐/主位变化/日志整体回滚，无半次合并。恢复钩子后同请求200成功。
- 故障二：仅AsyncSession.commit先执行真实提交后一次抛RuntimeError，HTTP500但独立SQL合并结果完整；不是将500包装成成功或模拟真实数据库断网。两组最终均来源软删/目标有效、目标补原地址、两联系人归目标但仅保留目标原主、任务/跟进/订单改customer_id且owner不变、原跟进内容及订单123.45完整、来源标签入目标，合并日志及merge审计各恰1。再次请求均非200拒绝已删来源，完整行快照不变，不重复迁移或补日志；不存在请求级幂等回执，未把拒绝重试说成重放成功。
- 证据ops/backups/customer-merge-failure-20261030/probe.py/result.log，两组PASS且exit0，含两个预期故障栈；两个钩子finally恢复，禁推送/调度/Stream。未发现新确认Bug，不新增应用或镜像回归，未重复1602项/构建/浏览器或冒称CI。git diff --check通过，PG正常停止。
- 局限：未实测专属价冲突、企微映射/附件磁盘、报价/合同/打样等其余关联、真实第三方或并发跨客户写入/权限变化；不能把这8类行的原子性证明扩为全系统合并正确。无SSH/生产数据/配置/服务变更、真实外部发送或提交推送部署。Q-098/099仍本地待发布，Q-096/Q-069/Q-075仍待既有决定，长期目标未完成；下一步可转独立合并关联/异常边界或其他高风险链，先复现再判取舍。


## 2026-10-30 Q-099用户选择刷新后再操作，本地修复验证完成（未部署）

- 用户明确选择“提示刷新后再操作（推荐）”，覆盖下节待选择状态；QUALITY_GOAL同步。用户回复到来时正在独立检查客户合并调用链，尚未实际运行合并故障探针；优先完成获批Q-099，不将未执行计划记成验证结果。Q-098补丁/回归继续保留。
- customer.service.set_primary_contact沿现有客户锁顺序，先保存期望归属并取得该客户锁，再查询联系人实际customer_id及未删状态；归属已变则409“联系人已改挂，请刷新后再操作”，已不存在沿原404口径；仅核对持久化标量，不用populate_existing冲掉调用方正常编辑字段。校验通过后才调用原take_primary_slot腾位和Q-098显式写回。没有锁新客户或自动替换其主位，不重试/迁移归属、不改表或权限项，保留正常同客户选择。
- 同一客户锁会由take_primary_slot再取得一次但不扩大物理锁集合/锁序，额外一次归属读取；原客户锁保证遵守既有父锁的改挂路径不能在核对后移动。没有新增联系人行锁、改变提交点或扩大到客户改派权限策略。并发删除不遵守父锁的完整窗口仍未穷尽，不能把此404检查称作已完成联系人全事务串行化。
- 扩展本批已登记check_contact_primary_race：先跑Q-098两个实际Lock等待且最后选A的正向，再分别跑change-customer/bind-customer→设主的两个真实锁等待场景，改挂200、旧选择409且明确已改挂，SQL原/目标主位不变、无失败set_primary审计；之后用新请求正式set-primary200，仅新客户主位切换为该联系人，原客户主位保持，审计恰成功改挂/后续成功选择各一条。结果permanent-green.log exit0，独立新合成客户，无生产请求或业务规则替身。
- 既有test_contact_primary_stale追加2项实际服务/内存SQL，旧归属变化409与已删404均在清原主位之前拒绝，所有联系人SQL值不变；Q-098旧True/False及重复选择两项保留，专项4 passed in0.31s。完整后端1602 passed in17.31s，exit0（full.log），应用/PG脚本/测试pyflakes及diff通过。本轮无前端变化，不重复构建/浏览器或声称CI；前端现有mutation错误消息处理沿原契约，实际新提示UI未本轮浏览器实测。
- 证据ops/backups/contact-move-primary-race-20261030/含先前服务器旧实现红例/固定AST对照/当前永久绿例及专项全量日志。服务器同码证明沿本节前已核实3c4291d三文件SHA，无本轮生产写入或测试。隔离PG正常停止，Q-098/099修复和测试记录仅本地未提交/推送/部署；用户本项明确先本地不部署。
- 剩余边界：PATCH联系人编辑在前置take_primary_slot与flush阶段的更广并发、联系人删除/归属/客户权限改变矩阵未穷尽；下一步可继续独立客户合并失败回滚或其他链，先复现再判断。Q-096/Q-069/Q-075仍待原选择，长期目标未完成，不以本项完成声称全项目已完成。


## 2026-10-30 Q-099联系人并发改挂后设主仍按旧归属操作（服务器确认，待口径选择）

- 上轮Q-098本地最小持久化修复、真实PG红绿及1600项通过为实质进展。本轮恢复长期目标/规则/质量文件/工作区，补其未覆盖的跨客户改挂竞争，保留Q-098有效补丁/新增回归，没有替换工作区为服务器旧版本。
- Q-099/P2：set-primary在get_visible_contact读归属后才由take_primary_slot锁客户；等锁期间另请求move_contact_to_customer已把同一联系人改挂目标客户，后到请求仍持旧customer_id，清旧客户主位后把联系人设主。数据库实际联系人已属新客户，目标原主未腾，最终触发uq_contacts_primary_per_customer并500。不同于Q-098“同客户旧True漏写”，本例初始联系人False，旧部署函数也必需写True，不能将Q-098补丁误当回归来源。
- 本机55441/crm_iso_analytics_20261030_test，正常admin登录/真实ASGI/PG；ORM构造每轮两个随机客户（各有自己主联系人）及一个原客户非主联系人。独立事务持原客户行锁，先启动change-customer/bind-customer（指定新客户、is_primary=False），观察实际Lock等待，再启动该联系人set-primary并确认两个实际Lock等待和任务未完成后释放。两入口均改挂200、设主500，日志明确部分唯一索引冲突；独立SQL改挂已生效且非主、原与目标主都保留，审计仅成功改挂一条，无伪成功set_primary。失败事务回滚避免主位损坏，但用户收到不可解释500、需刷新再做。
- 为排除Q-098引入：独立进程只从git show 3c4291d提取原set_primary_contact AST运行同探针，其它正式代码/数据路径保持，两个入口均再次200/500（baseline-red.log）。没有覆盖工作区。只读SSH实际3c4291d，customer.service/router/contact_util三文件SHA依次ae017cd94e8c2a5ab9dc84ceed806852fc043fa0a485dccc14b35148bed89899、a415cbdafe16e6e04b43d319eb5ccf34459024d6fe6f984ff18679d0f745a23c、4ec1b60c8fca4f0f0cad3f27cdd0b5d01f310f70fa5a1b7bf586acf666eb3e14与固定提交匹配（server-readonly.txt），确认服务器相同旧归属路径。未生产创建/改挂联系人。
- 待决定A（推荐）：锁后发现客户归属已变，明确提示“联系人已改挂，请刷新后再操作”，不继续修改旧客户主位；避免替用户把旧页面选择解释成更改新客户。代价是竞争时需刷新重试，须核对锁序/身份重检/原已读状态。B自动按新客户执行，减少重试，但可能在未展示的新客户上替换其主联系人，需重新校验可见性并稳定取得新父锁，涉及额外等待/锁策略。保持现状则并发窗口仍500。按AGENTS.md业务/权限/事务取舍先询问，尚未实施任何方案。
- 证据ops/backups/contact-move-primary-race-20261030/probe.py/red.log、baseline.py/baseline-red.log及服务器哈希，两个进程exit0是已确认坏行为断言成立，不是修复通过。无新应用修改，不重复1600项/前端构建或冒称CI；diff通过，隔离PG正常停止。无生产数据/配置/服务变更、真实外部发送、提交推送部署。
- 下一步等待Q-099并发归属变化口径选择，Q-098仍本地待发布，Q-096/Q-069/Q-075原选择保持。其它独立链可安全继续，长期目标未完成，不满足全局blocked；联系人删除与改挂/权限调整的完整矩阵仍未穷尽。


## 2026-10-30 Q-098并发重新选择原主联系人返回成功却清空主位（服务器确认，本地修复）

- 上轮报价发送故障两组正式API/PG验证为实质进展。本轮重读长期目标、AGENTS/质量文件与工作区，保留前轮未提交发送验证记录，应用基线仍已部署3c4291d，文档HEAD8db9150。转查联系人唯一主位，不重复已有线索转化并发。Q-096仍未获等待策略批准，未修改。
- Q-098/P2触发：客户原A主/B非主，两个正式set-primary请求先选择B、再选择A；两请求都在取得客户锁前读入各自Contact。第一请求锁内把A置False并B置True，第二请求依旧持有A.is_primary=True旧对象，清除B后再赋A=True不被ORM视为dirty，漏发A的UPDATE。唯一索引只防双主、不保证至少一主，因此接口两次200/data.is_primary=True，但数据库A/B均False。可能导致用户明确选主成功却没有主联系人，后续依赖主联系人的默认值/列表信息不一致；未夸大为联系人数据删除。
- 真实红例仅本机55441/crm_iso_analytics_20261030_test，正常admin登录/ASGI；ORM构造随机客户和两联系人（初始A主），独立事务持客户FOR UPDATE，先启动B请求并观察pg_stat_activity实际Lock等待，再启动A并观察两个Lock等待且任务未完成，释放后两请求200，独立SQL均False、期望唯一A断言失败（probe.py/red.log）。不是只asyncio.gather猜并发或SQLite伪造生产行为。
- 最小修复customer.service.set_primary_contact沿原take_primary_slot取得客户锁、腾别人主位后，显式flag_modified(contact,'is_primary')保证此明确选择实际写入。仅新增标准SQLAlchemy ORM变更标记，无新增查询/锁顺序/事务边界/表结构/依赖；仍原接口原权限/互斥规则，不改删除/归属策略、不自动选其他人、不清洗历史。避免用加载前旧True决定省写，属唯一明确的持久化纠错，无业务取舍。
- 同真实探针另建合成客户修后SQL A=True/B=False、两响应200，唯一A正向断言通过（green.log）。新增永久check_contact_primary_race及ops/check_suites登记，导入app前限定本机一次性PG/禁推送调度；真实两Lock等待矩阵再次通过（permanent-green.log）。每次合成数据独立保留，不清库。
- 新增test_contact_primary_stale两项实际服务+内存SQL覆盖旧True/原False及同一对象重复选择。首版SQLite夹具因模型只声明postgresql_where、SQLite建成全customer唯一导致建第二联系人失败，尚未执行业务；仅在测试内重建与生产等价的is_primary且未删部分唯一索引后2 passed in0.28s，initial-sqlite-fixture.log保留。没有移除唯一保护或把夹具错误记Bug。
- 附加独立故障验证：在上述成功并发后，正式DELETE当前A时仅进程customer.router.write_audit钩子抛错，HTTP500；恢复钩子后独立SQL两个联系人deleted_at/主位完全与之前相同，成功delete审计0（delete-failure.py/log，exit0）。证明删除失败回滚，不代表删除与改派/选主同时发生的所有竞争已覆盖。
- 完整后端1600 passed in20.45s，exit0（full.log）；应用/新PG脚本/测试pyflakes、diff通过。本项纯后端，未重复前端构建/浏览器或声称远端CI。只读SSH当前3c4291d，customer/service.py SHA256 ae017cd94e8c2a5ab9dc84ceed806852fc043fa0a485dccc14b35148bed89899与固定提交一致（server-readonly.txt），确认服务器同遗漏写入代码，未在生产造联系人。
- 证据ops/backups/contact-primary-race-20261030/。PG正常停止；本轮Q-098实现/新增测试/套件登记与进度仅本地，尚未提交/推送/部署。上一批7项部署授权已用于3c4291d，不自动扩展新问题；无生产数据/配置/服务变更、真实外部发送。
- 边界/下一步：本轮固定同客户二选一排队、同对象重复操作及删除回滚已证实；联系人同时转客户/删除、客户改派导致可见性变化的跨事务口径尚未穷尽，不把本修复扩成全联系人事务治理。下一独立方向可查这些竞态并先复现/判取舍；Q-096/Q-069/Q-075原待选择保持，长期目标未完成。


## 2026-10-30 报价发送失败回滚与提交回执不明重试补验（未发现新缺陷）

- 前轮7项应用修复发布至3c4291d、同SHA完整CI及491文件验证为实质进展。本轮重读长期目标原文/质量文件与工作区，当前文档HEAD8db9150、应用3c4291d对应关系保持；不把再次激活目标视为Q-096事务策略批准或后续问题部署许可。开始时工作区干净。
- 转查报价发送异常链，区别此前顺序状态与并发套件。send-email明确无SMTP能力，仅写status=logged、delivered=false，不推进正式发送；不因缺少外部投递而加新业务功能，也不把重复登记等同重复真实邮件。正式发送由mark-sent/lifecycle统一校验并提交事实/审计/业务事件。
- 仅本机55441/crm_iso_analytics_20261030_test，正常admin登录/真实ASGI/PG。ORM合成随机客户、两个approved CNY报价/版本及明确完整成本定制明细、确认零运费夹具，正式send-email/mark-sent执行；未走完整创建/核价/提交审批流程，不声称端到端建单或真实外部邮件。没有商机，所以本轮没有验证自动阶段推进。
- 两组均先正式send-email200且delivered=false，SQL确认quote仍approved、sent_at空，只有一条未投递登记，无send审计/业务事件。审计故障组仅进程钩子在lifecycle.write_audit处理该报价send时抛RuntimeError：HTTP500，独立SQL完整状态/时间/记录计数与之前完全相同，先flush的发送记录也回滚。恢复后相同请求200，再次重试200仍只一份正式发送事实。
- 提交回执不明组仅AsyncSession.commit钩子先真正提交PG再一次抛RuntimeError：HTTP500但独立SQLquote=sent、sent_at存在、正式发送日志/审计/业务事件各1（加前面的logged登记日志总数2）。恢复后同request_key重试200，所有状态/时间/计数不变；再重试仍不重复。两组同key换receiver均422并保持SQL结果，防止将不同实际发送错误合并。未把500说成成功或模拟成真实网络断流。
- 两处钩子finally恢复，推送/调度/Stream关闭、仅合成业务数据，未发真实邮件/消息。证据ops/backups/quote-send-failure-20261030/probe.py/result.log，含两次预期故障栈及最终两PASS、exit0。没有新确认Bug/应用修改，不重复1598项/构建/浏览器或冒称CI；git diff --check通过，PG正常停止。
- 无本轮SSH/生产数据/配置/服务变更、提交推送部署。局限为两类本机进程故障注入，不覆盖进程强杀、真实数据库连接断开、SMTP协议或关联商机锁图；现有机制覆盖本轮场景，不能据未覆盖处制造Bug。下一步可查其它尚未充分验证的集成/附件异常链；Q-096/Q-069/Q-075仍待原决定，长期目标未完成。


## 2026-10-30 当前7项修复已部署并验证（3c4291d）

- 用户本次明确要求“确认服务器也有相似问题就修复部署”，服务器旧40a6864相关四文件/实际前端基线对应确认后，发布Q-090/091目标版本与备注、Q-092原查看范围汇总、Q-093有效目标对象、Q-094输入边界、Q-095财务概览金额精度、Q-097规则日期异常隔离，共7项。Q-096催收并发锁策略尚待用户决定，没有实现或夹带发布；Q-069/Q-075原选择也未改。
- 固定应用提交3c4291d3606011195f3d0c0ff357ef81e17b2e1d已推送现有GitHub/Gitee pending-acceptance，ls-remote确认两者一致。完整本地后端1598项、前端构建/完整浏览器及各项真实PG证据见前文；本轮发布工具安全测试21项通过。相同SHA远端CI41176836497两job（UI冒烟、全部后端回归/静态）completed/success，ci-final.json独立核对headSha一致：https://github.com/ytsbx/crm-sales-agent/actions/runs/41176836497 。未跳过失败项或改断言凑通过。
- 从git archive固定提交及实际40a6864服务器基线独立冻结依赖构建，发布包/tmp/crm-release-quality-3c4291d.tar.gz SHA256 97a814a75dccd5fbed36521be41016c6b96c543cbb329726c1ecb1fb5f039289；统一工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab。上传/tmp/crm-release-quality-3c4291d后包/工具SHA相同，服务器preflight通过；仅在完整同SHA CI全绿后--apply，返回verified。回滚备份/opt/crm-sales-agent/backup/unified-3c4291d36060-rqv1nvek保留。
- 独立SSH postcheck：DEPLOYED_COMMIT与WEB_DEPLOYED_COMMIT均上述完整SHA，RELEASE.json status=verified，全部491个发布文件逐一SHA256一致；crm-sales.service active，8010真实健康status up/database ok，钉钉OA submission_enabled/sync_running/stream_connected均true。当前首页实际入口引用AnalyticsPage-DQDpgfP5.js含expected_updated_at等字段，OrderDetailPage-Dr5m7txS.js含三个财务概览exact字段；未以残留frontend/src或SPA /health页面证明成功。统一发布器的公网首页与入口资源校验已通过。
- 无数据库迁移、依赖升级、正式配置修改或历史业务数据清洗；没有用真实生产账号写客户/订单/回款进行测试。隔离测试PG均已停止。证据ops/backups/release-quality-targets-20261030/：release-tests/build/preflight/deploy/postcheck日志、ci-final.json及核验脚本。
- 本批应用发布完成；随后同步的质量记录提交仅文档，应用运行SHA仍为3c4291d，不用文档HEAD误报应用未同步。长期质量目标未完成，不把本批授权扩张到之后新问题。Q-096仍待明确方案选择；真实生产各角色全链路、真实第三方故障注入、全系统锁图和性能仍未穷尽。


## 2026-10-30 当前7项修复获批发布准备（尚未上线）

- 用户在明确了解7项尚未部署后授权“确认服务器也有相似问题就修复部署”。本批范围Q-090/091目标版本与备注、Q-092获批原范围汇总、Q-093/094目标有效对象及字段边界、Q-095财务概览精确金额、Q-097规则日期异常隔离；Q-096仍只解释未批准，禁止纳入锁策略调整。GitHub/Gitee沿已有明确同步授权同步本批现有仓库。
- 再次只读SSH当前前后端均40a686422ea3a996c5482804721b0f4e7ba7f600，analytics.targets/router、payment.service、settings.service四文件SHA256与前述原始红例/固定发布提交相同，确认7项代码问题未被其他发布修复。前端Q-090/091以当前固定发布构建及前轮实际资源哈希/浏览器红例对应，不用服务器残留frontend/src推断。
- 当前累积完整后端1598项通过、Q-095完整浏览器及生产构建通过、目标范围真实PG/ASGI与字段边界、规则溢出六场景均见对应记录。发布工具21项安全测试本轮通过，git diff --check通过。只从固定新提交构建发布包，以40a6864作为服务器基线，等待同SHA完整CI及服务器预检通过再apply；不迁移、装依赖、改配置或清洗业务数据。完成后另写真实发布结果，不把准备写成已上线。


## 2026-10-30 报价Excel文本单元格安全检查完成（未发现新缺陷）

- 用户在Q-096解释后要求“做完这个”，按当时正在进行的Excel文本安全检查收尾，不视为已接受Q-096“忙碌订单下一轮”事务策略或新的生产发布。保留Q-090至095/Q-097全部有效补丁，Q-096仍未修改。
- 核对bizdoc.xlsx：标题/抬头/客户/联系人/条款/费用名/正文用_put_text强制data_type=s；明细产品/规格/单位/备注同样强制字符串，金额走既有定点格式。样品申请表也复用_put_text。已有防护，不能仅因外部文本以等号开头报告公式注入。
- 新隔离文件探针直接执行真实render_quote_xlsx，五组=1+1、=SUM(1,2)、+1+1、-1+1、@SUM(A1)，每组同时写入15处文本（标题、公司/客户/联系人/负责人、条款、明细、费用和正文）。openpyxl回读原文完全相同且全部字符串，无公式型单元格；独立解压OOXML全部工作表无f元素、无externalLinks文件，金额24.68/25.68保留。五组result.log全部通过，未调用Excel执行公式或访问外部地址。
- 原有样品申请表与业务文档生成相关62项通过，1.74s（related.log），含已有样品公式防护断言；git diff --check通过。仅渲染器/真实文件与测试夹具验证，不声称本轮正式HTTP建单导出、生产下载或Excel桌面渲染。未连接数据库，无需启动PG；没有新增应用/测试实现或重复1598项全量。
- 证据ops/backups/xlsx-text-safety-20261030/probe.py/result.log/related.log及五份合成xlsx。无生产/SSH/真实第三方、提交推送部署。此项检查完成，长期全项目目标未完成；未来独立方向可查其它导出/附件异常边界，Q-096/Q-069/Q-075仍待既有决定。


## 2026-10-30 Q-097一条极端天数规则拖垮整个自动任务批次（服务器确认，本地修复）

- 上轮密码边界真实接口/持久化/审计验证为实质进展。本轮恢复长期目标原文/质量文件及工作区，转查自动任务异常配置；Q-096仍未获事务策略选择，未实施其锁/重查修复。保留Q-090至095全部有效修改，新补丁仅配置日期异常隔离，与Q-096催收并发为不同问题。
- Q-097/P2：settings.service.run_auto_tasks仅在int转换处捕获OverflowError，而三类规则在分支中另做timedelta和datetime/date加减。非负整数days=4000000能通过配置但日期超范围；days=1000000000连timedelta也超范围，均未被规则错误处理接住。TaskRuleInput/Update允许此JSON配置，实际正式创建/修改200，正常API可触发，不是只手造非法数据库状态。
- 红例本机55441/crm_iso_receivable_scan_20261030_test：正常admin登录/正式POST及PATCH task-rules，ORM仅建随机CNY客户/100元订单，正常API建当日应收。三类quote_no_followup/customer_silent/receivable_due × 两天数六次POST /tasks/run-auto-rules均500，日志明确date value out of range或timedelta上限异常；每次独立SQL正常对照规则的任务数0。正式停用本次错误规则后扫描200、同一正常规则生成恰一条，证明错误配置阻断正常业务且前面写入随失败回滚，未声称产生半提交或账目损坏。
- 最小修复：整数校验后统一计算本条规则使用的日期边界，只捕获OverflowError并沿既有rule_errors/failed_rule_count记录“days 超出日期可计算范围”，继续其它规则。quote/customer仍用原now回溯，receivable仍按today_business向前；没有另设业务天数阈值、截断天数、改保存配置/规则开关、加锁、变更事务提交点/权限/数据/依赖或历史清理。既有设计明确非法配置只失败本规则，因此此缺失异常分支属无实质取舍的直接修复。
- 绿色真实HTTP同脚本另建独立数据，六次均200、恰本错误规则列入failed_rule_count且原因含日期；正常对照规则任务持久化恰1、六次重复扫描不重复该规则/订单任务，停用错误规则后正常保持（green.log）。原probe.py/red.log保留，没有为通过而把业务错误改成全局吞异常。
- 在既有test_unrated_customer_rules追加6项永久真实服务/内存SQL回归，覆盖两类溢出×三规则及run_auto_tasks审计记录失败详情；专项22 passed in0.40s，完整后端1598 passed in14.97s，exit0（unit-green/full.log）。应用及调整测试pyflakes、diff通过。前端既有SettingsPage处理failed_rule_count并Toast.warning输出rule code及error，scheduler已有failed_rule_count日志；本项不新增UI/响应契约，不重复未改前端的构建/浏览器或冒称CI。
- 只读SSH实际40a6864，settings/service.py SHA256 389a82e55d5dc1ad2a6e3f0f85fe0e4d117c17dc7b1ef137102bd9e0dc8c325e与固定发布提交及Q-096服务器证据相同（server-readonly.txt），确认服务器同缺口。未查询/修改生产规则或运行生产扫描，无生产重启/发布。
- 证据ops/backups/task-rule-date-overflow-20261030/；隔离PG正常停止。新Q-097与Q-090至095均本地未提交/推送/部署，自动继续不扩张生产授权。Q-096/Q-069/Q-075仍待原决定。
- 边界/下一步：完整手动规则API与回滚/绿例证实，不冒称完整调度job/第三方定时故障已实测；此补丁只隔离无法表示的日期，不在本轮改变“配置可保存、运行时报告错误”的既有产品策略。其它规则副作用/并发/大数据性能仍未穷尽，可转独立集成或文件异常链。长期质量目标未完成。


## 2026-10-30 管理员/个人改密字节边界与审计真实接口补验（未发现新缺陷）

- 上轮登录/重置及刷新/退出真实PG并发为实质进展。本轮恢复目标原文/AGENTS/质量文件和工作区，Q-096仍未获方案选择，保持未修改。转查账号输入边界，保留Q-090至095全部有效补丁。
- 静态初始疑点未成立：user.schema.UserCreate/UserUpdate已有password_too_long_bytes校验及bcrypt72字节上限，PersonalAccountUpdate也按UTF-8字节校验，security.hash_password有服务层兜底。不能因两个入口写法不同报告成缺校验或擅自重构合并；本轮仅补正常API/PG持久化与失败副作用证据。
- 仅本机55441/crm_iso_analytics_20261030_test：ORM造一个随机合成非管理员账号，正常登录；管理员PATCH /users/{id}及账号自身PATCH /auth/account分别提交73个ASCII、25个中文（75字节），四次均400且明确72字节提示。管理员无效请求同时携带姓名变更，独立SQL确认name/password_hash、该用户update审计计数、全部会话sid/status/revoked_reason均与基线相同；原JWT仍GET me200，没有部分保存/误吊销。
- 正向边界：管理员正式重置24个中文恰72字节成功，再用该完整密码登录；个人正式改成72字节ASCII成功，明确require_relogin，两个步骤均使先前JWT401。新完整密码登录/GET me200；分别追加一个中文/ASCII再登录400，最终实际verify_password同样拒绝73字节ASCII，证明没有静默截断为等价密码。
- 独立SQL与真实hash校验确认最终新密码有效，恰两条成功user/update审计，before/after JSON无测试旧/新密码或哈希正文；管理员响应也没有password/password_hash。脚本不打印令牌/哈希/密码正文，只输出验证标签。result.log exit0，未发现新确认Bug或应用修改。
- 证据ops/backups/password-boundary-20261030/probe.py/result.log。测试为正式ASGI/PG和合成用户，不声称生产账号或实际浏览器；推送/调度/Stream关闭，PG正常停止。无新应用变化，不重复1592项/前端构建/浏览器或声称CI；git diff --check通过，未新增镜像测试或业务规则。
- 无SSH/生产账号/配置/数据/服务变更、外部真实登录、提交推送部署。创建账号入口上限的代码证据已有，但本轮没有另外执行POST /users，不能将本次重置/个人改密验证扩称所有创建/导入入口。后续可转不同模块的异常输入、文件/集成边界；Q-096/Q-069/Q-075仍待原决定，长期目标未完成，Q-090至095仍本地未发布。


## 2026-10-30 登录/重置密码及刷新/退出真实PG并发补验（未发现新缺陷）

- 前轮Q-096正式API交错复现及服务器同码确认属于实质进展。本轮恢复长期目标原文/AGENTS/质量文件和工作区；Q-096选择未回复，不实施其事务调整，转查独立鉴权链。保留Q-090至095有效补丁，无新应用改动。
- 先核对历史2026-10-26鉴权审查：旧JWT跨请求撤权/停用/改密失效已经用实际鉴权函数+内存SQLite验证，普通登录也已有账号行锁，不能再次静态报告成新Bug。本轮区别为当前代码、真实PostgreSQL、正式ASGI路由与可观察的实际锁等待，而非重复顺序函数调用。
- 本机55441/crm_iso_analytics_20261030_test，ORM仅造三个随机非管理员测试账号及独立会话对照账号；登录、管理员PATCH重置密码、GET /auth/me、POST refresh/logout均为真实API。独立事务持目标User行锁，先后启动登录/重置两个请求，pg_stat_activity确认两个users FOR UPDATE实际Lock等待且任务未完成，再释放，每组10秒完成上限。
- 登录→重置：200/200，重置前已有会话及排队期间刚新建的会话全部revoked/password_change，旧凭证访问和续期均401；重置→旧密码登录：200/400，旧会话全部失效，旧密码不能再建会话。两组真实SQL计数分别等于已签发会话数、active为0、吊销原因正确；新密码登录及GET me均200，独立用户旧会话持续200。
- 刷新/退出窗口：仅测试进程execute钩子在refresh真实读取active会话后暂停，另一请求正式logout提交，再恢复refresh。途中refresh本身返回200，但它复用同一sid，实际会话仍revoked/logout且总行数1；旧JWT和该刷新响应JWT之后访问/续期均401，没有会话复活或补开sid。不能仅因一个已通过鉴权的途中刷新返回200就声称吊销绕过；本轮结论依据之后实际鉴权及SQL终态。钩子finally恢复，未伪造查询结果或业务写入。
- 三组最终result.log exit0。证据ops/backups/auth-session-race-20261030/probe.py/result.log，未记录令牌/哈希正文或真实用户信息；所有密码和账号为专用合成数据。无新确认Bug，不新增问题编号或复制已有鉴权实现测试；无应用变更不重复1592项/前端构建/浏览器或声称CI。git diff --check通过，PG正常停止。
- 本轮未访问服务器/真实外部登录或修改生产账号/配置；未提交推送部署。初始关注的停用后跨请求拒绝已有历史验证，本轮没有新增停用/重新启用的实际测试，不把历史证据写成本轮已跑。停用→启用的会话保留策略、钉钉/企微真实认证、进行中业务写请求与撤权的交错尚未本轮验证；后续可转这些尚未充分证明的独立路径。
- Q-096事务策略仍等待用户决定；Q-069/Q-075原选择保持。长期目标未完成，Q-090至095仍本地未发布，自动继续不构成生产授权。


## 2026-10-30 Q-096回款收齐后并发扫描仍新建催收任务（服务器确认，待事务策略决定）

- 上轮Q-095真实PG/浏览器红绿及1592项通过为实质进展。本轮恢复长期目标原文/AGENTS/质量文件和工作区，转查应收到期自动任务，不重复已验证的应收删除锁等待。保留Q-090至095有效补丁；本轮无新应用修改。
- 静态核对排除一处初始怀疑：payment.refresh_overdue_plans已按订单→应收加锁并populate_existing，不能报告它“只锁子节点反向死锁”。实际缺口在settings.service.run_auto_tasks的receivable_due分支：查询pending/partial/overdue及到期日后，循环只检查owner和已有任务，未锁父订单或重查当前应收。task扫描advisory lock仅互斥扫描，财务确认不取此锁，不能阻止中间到账。
- Q-096/P2真实触发：全新本机55441/crm_iso_receivable_scan_20261030_test完整迁移/seed，保留日志；正常admin JWT、正式应收/登记/确认/POST /tasks/run-auto-rules。仅ORM建立随机客户和两张CNY100订单，其余正常API；用种子现有receivable_due规则、明日到期计划，未改规则配置或生产数据。对照订单先正式确认收齐，再扫描无任务；竞争订单扫描SQL已返回真实候选后，以测试进程AsyncSession.execute钩子暂停该请求，再从另一真实会话正式confirm并提交paid100，独立SQL任务数仍0；恢复扫描后正式请求200，新建该订单待处理payment任务1条，应收仍paid。
- 探针没有伪造查询结果/paid状态/任务行；只控制两个真实请求的交错，finally恢复execute并回收任务，最后exit0。没有以任务created_at证明先后（事务时间戳可能取事务开始时刻）；证据来自暂停点、确认实际提交及三个阶段的任务数量。red.log明确记录paid/no task→恢复扫描→pending催收任务。实际影响为已收齐订单仍被新增多余待办、业务员需核对处理；未证明账款损坏或重复扣款，不能夸大。
- 服务器只读SSH当前40a6864，settings/service.py SHA256 389a82e55d5dc1ad2a6e3f0f85fe0e4d117c17dc7b1ef137102bd9e0dc8c325e、scheduler.py b3a413b562f7d6efdd40221e5618d474a8a41134e55d511503c84b09017ee22c与固定提交一致（server-readonly.txt）。真实复现为手动运行规则入口；定时任务也调用run_auto_tasks且刷新只预锁已逾期订单，明日到期不在刷新锁集合，但未冒称本轮执行完整定时任务或生产已发生。
- 待决定方案A（推荐）：生成前在父订单锁内重读计划/订单/当前归属及同规则未完任务，已收齐/取消/失效则跳过；忙碌父订单SKIP LOCKED留给下轮，减少扫描与财务互等。代价是忙碌订单可能推迟一次扫描，需要验证锁序、等待/死锁与去重。方案B保持现状、人工清理偶发多余待办，无新等待但问题持续。单纯再查一遍而不协调锁仍有检查后写入窗口，不作为可靠修复。
- 不自行选择事务方案：AGENTS.md明确“涉及业务规则、权限、数据清洗、数据库结构、生产环境或用户体验取舍时，先记录并请求人工决定”；长期目标也要求事务策略风险先由用户决定。特别是定时流程先refresh持订单锁再进入扫描advisory，不能简单加阻塞父锁而忽略与手动扫描的锁序，获批后须构造相应等待/死锁对照再实施。先本地修复验证，不部署、不清理现有任务，不自动套用此前Q-029批准。
- 证据ops/backups/receivable-task-race-20261030/，含新库迁移/seed、实际ASGI探针/日志及服务器只读哈希。无新修复不重复1592项/前端构建/浏览器或声称CI；git diff --check通过，PG完成后正常停止。无生产写入/重启/真实外部发送/提交推送部署。
- 下一步：等待Q-096事务策略选择，此问题保持未修改；其它独立资金/归属/文件链审查仍可继续，不满足全局blocked。Q-069/Q-075原待选择不变，长期目标未完成，本地Q-090至095仍未发布。


## 2026-10-30 Q-095订单财务概览丢分（服务器确认，本地修复及浏览器验证）

- 上轮应收删除/自动及人工分配四组真实锁等待验证为实质进展。本轮恢复长期目标原文/质量文件和工作区，转查普通金额验证未覆盖的大额财务概览消费者；保留Q-090至094所有有效补丁。先查历史：订单/回款/应收列表已有exact字段，但独立finance-summary及订单详情三张分配/待确认KPI仍只读float，是新的遗漏路径，不重复报告已修列表。
- Q-095/P2：payment.service.order_finance_summary在PG Decimal精确查询与合计后，对八项金额仅_f输出；唯一前端消费者OrderDetailPage“已分配到应收期/已到账待分配/待确认回款”直接formatMoney数字，恢复不了丢失的分。另一个后端消费者agent.tools将同一finance字典原样加入有payment:view的订单结果，没有改权限。正式GET接口本身已做订单可见性检查。
- 红例仅本机55441/crm_iso_analytics_20261030_test，正常admin登录/真实ASGI。ORM建立随机CNY订单99999999999999.99（未经过完整建单/履约API），正式建同额应收、登记三笔各同额回款，前两笔正式confirm、第三笔pending；既有允许超额到账/待分配规则未改。独立SQL订单/已确认合计/分配分别99999999999999.99、199999999999999.98、99999999999999.99；GET八项只返回浮点，其中单额为.98、已收合计.97，无exact字段。probe.py/red.log含实际响应及预期失败，不把数据库源值记成已损坏。
- 前端独立浏览器加载修前dist，内存API夹具基于该真实HTTP旧字段及SQL期望exact补充，实际进入订单详情；三张KPI均显示99,999,999,999,999.98，要求.99的断言失败（browser-red.log）。此为真实前端与响应替身证据，不冒称浏览器直连PG或生产浏览器。
- 最小修复：order_finance_summary把同一次原始Decimal金额收集为字典，旧八个数字字段保持原值/类型，增加各自*_exact两位字符串，None保持None；没有先float后反推、不改变SQL条数/统计/超额规则/金额舍入/权限/锁/数据库/历史记录。FinanceSummary补可选类型（unreceived_amount_exact原已有），订单详情三个现有KPI优先exact并保留旧字段fallback。无新按钮/规则/依赖或实质取舍。
- 新增3项永久服务测试覆盖0/123.45/合法大额、全部八项正负与零精确输出、旧数字兼容和计数；与原分配/回款一致性相关合计42项通过（unit-green.log）。真实ASGI修后同探针另新订单179通过（green.log）。新增已登记check_order_finance_precision，导入app前限定本机隔离PG并关闭推送/调度/Stream，正式登录/应收/回款/confirm、独立SQL/八项exact及旧数字类型检查通过，新订单180（permanent-green.log）。该套件不清理或覆盖既有真实业务行。
- 完整后端1592 passed in15.59s（full.log），前端TypeScript及生产构建通过（build.log）；完整隔离浏览器确认回归exit0（browser-green.log），新增BROWSER_ORDER_FINANCE_SUMMARY_EXACT_CENTS=true，原订单/应收/回款、目标版本/备注/团队和其他确认场景保留通过。应用/新增脚本/测试pyflakes、Node语法、统计数据范围静态检查及diff通过；未删断言或把旧失败改成放行。
- 只读SSH实际40a6864，payment/service.py SHA256 1459c717bfea29de02412966bb7d76d52e39d15a45bf4f1631855e806692fe55与固定发布提交一致（server-readonly.txt），确认服务器同精度缺口。未在生产创建大额回款/读取真实账目或修改服务。前端运行源码对应关系沿固定40a6864发布证据，不以服务器残留frontend/src为准。
- 证据ops/backups/order-finance-precision-20261030/；永久前端夹具ops/fixtures/order-finance-exact.json。PG正常停止，Q-090至095补丁与测试/记录尚未提交/推送/部署，本轮没有新生产发布许可。没有重写历史结账或生产数据。
- 边界/下一步：本项财务概览及三个既有显示消费者闭环已证实，旧数字字段为兼容仍可能有浮点误差；真实生产角色/浏览器、所有付款分析/AI文本格式消费者和大数据性能未穷尽，不宣称全部资金路径完成。可转其它金额消费者或业务异常链；Q-069/Q-075原待决定保持，长期目标未完成。


## 2026-10-30 应收删除与自动/人工回款分配实际锁等待补验（未发现新缺陷）

- 上轮非管理员会签/转交五组实际Lock等待验证是实质进展。本轮恢复长期目标原文、质量文件与工作区，转查资金链，保留Q-090至094全部有效修改。起初拟查回款撤销，完整核对历史和现有API后确认已确认回款没有撤销功能，ensure_payment_pending已禁止再次confirm/reject；这属于既有业务设计，不添加撤销或把缺少入口认定Bug。该顺序终态已在2026-10-29补验，未重复。
- 选择此前未充分证明的“与回款直接挂载不同的目标期”删除窗口：自动确认把超额回款分到后期，或人工分配把确认款改到后期时，该后期恰被删除。delete_receivable除PaymentRecord直接挂载外已检查PaymentAllocation；两个路径均先锁共同订单。本轮针对隐藏的分配引用做真实触发，不只依据代码推测保护有效。
- 仅本机55441/crm_iso_analytics_20261030_test，正式admin登录/ASGI。ORM构造随机客户/四张CNY200订单（未通过完整建单/履约API），正常API各建立两期100、登记回款并确认/人工分配；不伪造确认状态、不更改已有业务行。独立事务先锁本张sales_orders行，先后启动两个请求并通过pg_stat_activity确认两个真实Lock等待、任务未完成，再释放，每组10秒完成上限。
- 四组全部通过：自动确认200先于删后期→200/422，两期各分100且paid；先删后期再确认→200/200，仅第一期分100、待分配100（两项独立操作合法，不能错误要求必须一方失败）。已确认100的人工转第二期先于删除→200/422，分配manual第二期100、首期pending/二期paid；先删二期再人工转配→200/404，原auto首期100与allocation_mode均不变。删除保护读取锁后最新分配，无已删除计划上的孤儿分配。
- 每组独立SQL核对回款状态/金额/模式、全部分配行及source、剩余计划状态、基线后本业务审计动作；正式finance-summary验证实收=已分配+待分配，没有失败请求成功审计或半改。最终result.log exit0。初次探针误导入app.core.time，在登录和写入前失败；改为现有timebase后才运行，initial-import.log保留，不记应用缺陷。
- 证据ops/backups/payment-plan-delete-race-20261030/probe.py/result.log。无新确认Bug或应用变更，不重复未变应用1589项/构建/浏览器或冒称CI；git diff --check通过，专用PG正常停止。推送/调度/Stream关闭，无生产/SSH/真实外部发送/提交/推送/部署。
- 边界/下一步：本轮普通整分金额、管理员、单张订单两期及上述四个锁序，不代表所有节点修改/确认/改派或压力场景。可转查金额精确字段在订单财务概览/付款消费者是否闭环，先查历史避免重复，不能因_f静态存在就直接报告精度Bug。Q-069/Q-075仍待决定，长期目标未完成；Q-090至094仍本地，新的生产发布没有获得本轮授权。


## 2026-10-30 非管理员会签/转交真实锁等待与处理权补验（未发现新缺陷）

- 上轮内部单节点七组实际Lock等待及审计/通知验证构成实质进展；本轮恢复目标原文、AGENTS及质量文件/工作区，补此前明确未覆盖的非管理员会签与转交，而非重复管理员七组。保留Q-090至094全部本地修改，没有新增应用补丁或问题编号。
- 本机55441/crm_iso_analytics_20261030_test合成两主管、两财务、一申请人及三个随机测试角色；权限经现有Permission/role_permissions和PricePermission配置，数据范围all。五账号均通过正式auth/login获取JWT，未替换CurrentUser或提权为admin。ORM建立随机客户/报价/版本/manager或co_sign待审批实例，正式审批/转交API执行。未跑核价/正式提交全链，不能声称完整业务建单或部门范围测试。
- 重用“独立事务持有报价行锁→请求一实际等锁→请求二实际等锁→释放”的验证方式，pg_stat_activity确认两Lock等待且任务尚未完成，每组10秒完成上限。主管transfer→approve、approve→transfer、approve→approve均200/403；财务transfer→approve为200/403，approve→transfer为200/400。等待后的授权/节点/实例状态被重新检查，没有旧处理人按等待前权限继续写入。
- 三组主管竞争后正式完成节点链：manager转交只通知新主管；主管通过后实例仍pending/co_sign，原节点current_assignee_id已清除，两名合格财务各仅一条会签提醒；主管与无审批权申请人此时approve均403，财务正式approve后主单/版本/实例均approved。仅测试无审批权申请人的拒绝，不夸大为具备审批权申请人的自审场景覆盖。
- 财务转交竞争：转交优先时旧财务approve403，新财务正式reject后主单approval_rejected、版本/实例rejected；通过优先时后转交400，最终三者approved。每阶段独立SQL检查summary/状态、审批记录、审计动作完全匹配唯一成功路径；申请人最终结果提醒恰一条，无失败请求多写副作用。五组result.log exit0，未发现新缺陷。
- 调用链证据沿approval.router._load_pending、_assert_can_approve、_assert_transfer_target和access.approval_denial：锁后重读状态/指派、节点权限及拟接收人资格；approve进入co_sign显式pop当前指派。本轮实际结果支持这些已有机制覆盖上述竞争，不以静态怀疑新增Bug。
- 证据ops/backups/approval-transfer-race-20261030/probe.py/result.log。仅隔离合成数据，推送/调度/Stream关闭；PG正常停止。无新应用变化，不重复完整1589项/前端构建/浏览器或声称CI，git diff --check通过；未访问/修改生产、发送真实外部消息、提交、推送或部署。
- 边界/下一步：未覆盖部门/下级范围、账号停用或权限调整与审批同时发生、客户改派/软删及第三方回调交叉事务，五组不能代表全系统锁图。下一步宜转不同高风险链路如付款撤销/异常重试消费者，先查历史覆盖与实际证据；Q-069/Q-075仍待原决定，长期目标未完成，Q-090至094仍本地待发布授权。


## 2026-10-30 内部报价审批与两种撤回入口的真实锁等待补验（未发现新缺陷）

- 前轮Q-094修复、PG输入边界和1589项完整后端通过为实质进展。本轮恢复长期目标原文/质量文件/工作区，转向此前只充分验证顺序重提、未覆盖实际锁等待的内部审批异常流程，保留Q-090至094全部有效修改。未新增应用补丁或新问题编号。
- 调用链：approval.router._load_pending先通过quote.service.get_visible_version按单据→版本加锁，等待后populate_existing重读实例并检查pending和当前版本；quote.router.withdraw_approval同样先锁报价/版本并重查pending。初始“并发审批与撤回可能双成功”的静态怀疑本轮七组场景未成立，不能记成已确认Bug。
- 仅本机55441/crm_iso_analytics_20261030_test；正常admin登录、真实ASGI正式审批/撤回API，ORM构造独立随机客户/报价/待审批版本/manager单节点实例。未经过正式SKU核价/提交审批流程，不能冒称完整建单端到端或非管理员权限测试。没有会签节点、没有使用生产/演示库，推送及调度/Stream已关闭。
- 独立事务先持有每张合成报价FOR UPDATE，启动请求一并通过pg_stat_activity确认实际Lock等待，再启动请求二并确认两个请求均处于Lock等待且任务未完成，才释放阻塞；不是仅asyncio.gather就推断请求重叠。10秒完成上限内七组全部通过：approve→approve、approve→reject、reject→approve、approve→审批侧withdraw、审批侧withdraw→approve、approve→版本侧withdraw、版本侧withdraw→approve。每组先请求200，后请求400“已处理/没有待审批”；主单/版本/实例分别保持唯一胜方一致状态，实例finished_at存在。
- 首轮七组验证状态及唯一审批记录后，增加审计/通知断言另造七组全部通过：恰好一条胜方审批记录及一条对应审计；通过或驳回恰一条报价通知，撤回零条，不出现失败请求的重复副作用。初次夹具漏填QuoteVersion.created_at导致NOT NULL失败，发生在并发请求前；补齐夹具时间后运行，initial-fixture.log保留，不记应用缺陷。最终result.log exit0，state-only.log为前次有效证据。
- 证据ops/backups/approval-lock-race-20261030/probe.py及日志。无应用修改，不重复1589项/前端构建或冒称本轮CI；git diff --check通过，专用PG正常停止。无SSH/生产写入/真实外部发送/提交/推送/部署。
- 边界/下一步：本轮只证明本机真实PG上述单节点、管理员及报价行锁等待场景，未穷尽非管理员会签/转交、客户改派、删除和外部审批回调交叉锁图，未做生产压力或大数据基准；可继续检查会签转交或第三方失败重试的独立证据。Q-069/Q-075仍待原决定，长期目标未完成。


## 2026-10-30 Q-094目标输入超出既有字段容量时报500（服务器确认，本地修复）

- 前轮Q-092获批后重新验证四范围/五指标矩阵属于新增验证证据，不是仅重述状态。本轮恢复长期目标原文/AGENTS/质量文件及工作区，接续已保留的目标字段真实红例；保留Q-090至093有效修改。
- Q-094/P3：SalesTargetUpsert未限制remark长度及new_customer_target上限，模型分别String(255)/Integer。仅本机55441/crm_iso_analytics_20261030_test，通过正常admin登录及正式ASGI保存合成个人目标，再提交256个中文字符备注或2147483648新客目标，均HTTP500；日志分别确认PG StringDataRightTruncationError和int32越界。每次独立SQL检查原目标值/updated_at及成功审计数保持，不把异常误报为已损坏业务数据；触发影响为可预知的无效输入仍报内部故障、无法判断怎样修正。证据probe.py/red.log。
- 最小修复仅router.SalesTargetUpsert：新客目标le=2147483647，备注max_length=255；复用既有中文校验错误处理。对齐现有不可突破的存储容量，不新增业务阈值、不截断文字、不扩大字段/迁移/清洗、不改变正常输入或权限，属无实质取舍的入参校验。None/空备注与0新客保持有效。
- 追加7项永久schema回归：两种越界数值拒绝、0/最大int32可接受、中文255/256字边界、空/None备注。与原目标范围及版本测试合计40项通过（unit-green.log）。复用已登记check_target_frozen_scope真实PG/ASGI测试：超限输入400且提示含明确上限；目标总数/原目标全部值与版本时间/成功审计数不变；最大int32及255个中文字符200并SQL原样落库。原Q-092四范围×实时/冻结五指标及Q-093有效/失效对象矩阵继续通过，1997-02（permanent-green.log）。未降低断言或跳过失败。
- 完整后端1589 passed in15.14s，exit0（full.log）；应用/新增测试/PG脚本pyflakes及git diff --check通过。本轮未改前端：既有保存失败Toast沿用后端提示，onError不关闭弹窗；没有重复无关构建或将此静态调用链描述成新的浏览器实测。
- 只读SSH服务器实际40a6864，router.py SHA256 b06387c31bc413b3c5e5fc8a8ac58089e1d435ac018476d4937b9d2b680a655e、model.py f07f5bdeae7bb14ba6c11dde539df77302c5c03d196705681c95d87ec5046a1c，与固定提交匹配（server-readonly.txt），确认同代码缺口；未在生产请求超限写入，也未查真实目标。生产无部署/重启/配置/数据变更。
- 证据ops/backups/target-input-limits-20261030/；专用PG完成后正常停止。Q-090至094仍本地未提交/推送/部署，本次自动继续不构成新发布许可。长期目标未完成，历史缺损快照/组织变动及其它模块边界未穷尽；下一步转独立业务链路（如分析筛选与统计、审批异常路径）先复现再判断，避免重复已经通过的目标边界。Q-069/Q-075原待决定保持。


## 2026-10-30 Q-092用户确认后复核

- 按用户“按原权限显示”确认保留已完成的Q-092实现，本部门主管的团队汇总在实时与结账后均只统计原可见成员，具有下级/全量权限者维持原完整范围。
- 本轮重新执行本机隔离PostgreSQL/ASGI永久套件check_target_frozen_scope，1996-02四范围×实时/冻结、五指标与下钻一致、无权对象拒绝、原存档保持及目标输入阶段均通过，进程exit0；日志已保留ops/backups/analytics-scope-exact-20261030/approval-recheck.log。既有完整后端1582项通过记录继续有效，本轮未重复计数。git diff --check通过。
- 未新增权限项，未改生产数据或部署；本项批准仍为先本地测试、不上线。组织历史变动/缺失个人快照等残余边界沿Q-092原记录保留。


## 2026-10-30 Q-093目标接口接受不存在的人员/部门并永久写入（服务器确认，本地修复）

- 上轮Q-092获批范围规则/五指标矩阵和1574项为实质进展。本轮恢复目标原文/规则/质量文件/Git，接续此前未实际开始的目标输入校验，保留Q-090至092全部有效改动；不扩大到人员在职/角色能力或组织治理政策。
- Q-093/P2仅本机55441/crm_iso_analytics_20261030_test：独立SQL先确认user1000073和dept1000029均不存在，正常admin登录后正式POST /sales-targets/upsert提交各对象，均HTTP200“目标已保存”；SQL实际目标101/102引用该空编号（probe.py/red.log），不是只表单能填也不是并发错误提示。SalesTarget.user_id/department_id只有BigInteger字段无相应FK，upsert又未校验对象，真实调用链没有其它拦截；伪目标可留存，后续名称/归属没有对应档案。
- 最小修复：upsert_target在期间/互斥/非负等原校验后、读写目标前按指定对象session.get User或Department，不存在明确404“目标业务员/部门不存在，请刷新后重新选择”。两对象互斥，普通指定对象最多一次主键读取，公司目标不新增此读取；没有新表/FK/锁/迁移。SalesTargetUpsert两个对象编号限制为正BigInt范围，None仍公司，避免0混淆scope_key和超大编号先触发驱动错误。仅验证存在，不过滤inactive、不限定sales角色/部门状态，不收紧既有有效对象能力，无业务取舍或新增依赖；历史伪目标不自动清洗。
- 新增8项永久test_target_scope_input：user/dept×0/-1/超BigInt六个schema负例，两种不存在对象在任何目标查询/写入前404；只测新增行为，不复制整套业务实现。既有test_review_round46_repairs版本回归补齐真实User表及id1测试用户夹具，原微秒/时区/金额断言不改；新增存在检查不能再用“目标指向无用户表”的不完整测试事实。相关33项通过（unit-green.log）。
- 复用本批已登记check_target_frozen_scope追加真实PG/ASGI对象校验阶段，避免重复造管理体系：构造未出现在人员/部门及目标表的空编号，缺对象404；0/-1/超BigInt拒绝、同时指定人/部门422；无效请求前后目标数及sales_target成功审计数完全不变。正常个人/团队/公司和被测试置inactive的已有用户/部门目标均200，明确守住历史能力。前段Q-092五指标/四范围实时与无源冻结矩阵继续通过，1994-02（permanent-green.log）。
- 永久红例独立进程只加载已部署40a6864的upsert_target AST，其余当前实现/范围修复保持；矩阵全部通过后，缺对象请求仍200新建目标121，对404断言失败（permanent-red.log）。未覆盖工作区或把合法正常请求误报，旧坏数据仅保留隔离证据。
- 完整后端1582 passed in15.35s，进程exit0（full.log），新增8项计入1582，PG套件另行不混计。应用/新增及调整测试pyflakes、diff通过。另建本机crm_iso_scope_input_20261030_test新空库并完整迁移/seed，原有check_sales_targets正常保存/版本保护/团队更新/范围/分批金额套件全部通过（fresh-migrations.log/fresh-seed.log/previous-target-regression.log），避免其会清理的套件触碰前轮隔离历史证据。不使用生产/演示库跑套件。
- 只读SSH实际40a6864，targets.py SHA256 15c0f290ca7e91201d11b5ebf8f8179bb49f171b7de4c71076c61e7d81fef732、router.py b06387c31bc413b3c5e5fc8a8ac58089e1d435ac018476d4937b9d2b680a655e与固定提交一致，确认服务器同缺校验路径（server-readonly.txt）。未读取生产用户/部门档案或伪造生产目标。
- 证据ops/backups/target-invalid-scope-20261030/；本轮只后端/测试/记录变更，未重新执行无关前端构建/浏览器或声称远程CI。专用PG完成后正常停止，当前Q-090至093补丁/测试/记录均本地未提交/推送/部署。用户尚未授权这批新发现生产发布；上批40a6864线上验收状态保持。
- 边界：存在检查不是新增数据库FK或跨请求硬删除锁，未宣称穷尽组织硬删除与建目标并发；未更改inactive对象是否继续可设目标的原规则，不自动修复历史空对象目标。备注长度等其它字段尚未本轮验证，不顺手扩大修复。Q-069/Q-075原选择保持，长期目标未完成；下一步可转目标输入其它边界或不同未充分覆盖模块，必须继续先真实复现再决定。


## 2026-10-30 Q-092获批按原权限显示，团队存档汇总本地修复完成（未部署）

- 用户明确选择“按原权限显示（推荐）”，覆盖前节待选择状态，QUALITY_GOAL同步。本轮先恢复长期目标/质量记录/Git，保留Q-090/091前端补丁；独立目标非法对象输入审查尚未开始实际验证，优先完成获批Q-092，不把起初准备检查当成已完成。
- 最小修复仅analytics.targets结账覆盖阶段：已有团队行如含当前allowed_users无权查看的成员，取dept_members与allowed_users交集，从已经权限裁剪的个人冻结快照合计五项指标；新客返回int、金额保留Decimal/exact。完整团队存档与公司汇总原值不改，权限覆盖整团队的department_and_sub/all及无下级的部门保留原完整快照读取，避免不必要改变旧完整范围结果。不查询实时金额、不新增SQL/锁/表结构/历史重算或依赖；只增加部分可见团队的内存成员合计。
- 真实PG/ASGI补验：先原简单红例另空2022-02，department团队实时/冻结均10.11，detail10.11；department_and_sub/all仍60.66。原2021-02红例保持，未清洗。新永久check_target_frozen_scope.py与ops/check_suites.txt同次登记，导入业务模块前限制本机一次性PG、禁推送/调度；创建三层部门+外部门、各一位合成用户、老客/新客订单、已确认款/发货事实和个人/团队/公司目标，五项金额/新客全部非零可校验，不只测signed。
- 永久矩阵：self/department/department_and_sub/all × 实时/冻结8组，验证精确个人行与允许团队行集合、无权user/dept参数403、公司汇总原既定全量规则、每条团队行签单/回款/发货/复购/新客与同范围下钻一致。结账后仅把本套件合成当年源订单标取消，仍正确读冻结明细/个人实绩，正式完整部门存档root签单63.66，department可见签单11.11；既有小红例是10.11/60.66，新夹具额外每人新客1元，所以数值不同有明确来源，不混写。
- 源事实用ORM构造，CurrentUser依赖覆盖；将带确认款的合成订单置cancelled仅为“实时事实消失”的隔离测试注入，不声称正常业务取消接口允许这种组合，也未放宽取消规则。快照前后独立SQL全部汇总scope/metric/value/version/frozen_at/frozen_by逐项不变，证明权限读取没有重写完整存档。未操作生产/演示/开发库。
- 永久红例独立进程只加载已部署40a6864的targets_with_actuals AST、未覆盖工作区，前三实时/范围对照通过后，department冻结signed实际63.66对期望11.11断言失败（permanent-red.log，1991-02）；当前实现同脚本全部通过。最终为减少影响只对部分可见团队重组，其余范围原读法不变后再次运行，1993-02五项/八组全部通过（permanent-complete-green.log），原snapshot完整值保持。没有删除断言/用实时重算凑被冻结结果。
- 完整后端最终1574 passed in15.61s、进程exit0（full-final.log），应用/永久脚本pyflakes、diff通过；PG矩阵脚本单独执行不混计新增pytest项数。前端本项未改，Q-090/091上一轮完整浏览器保持，不重复与范围后端无关的构建/浏览器或冒称远程CI。脚本登记与新增文件仍在本地，未来必须同批提交。
- 本轮服务器同路径证据沿前节刚核实40a6864两个后端哈希及工作区基线保持，未为绿例写服务器。证据ops/backups/analytics-scope-exact-20261030/，正常停止专用PG；Q-090/091/092本地修复、测试与记录保留，尚未提交/推送/部署。用户本项明确先本地/不上线，不自动延续上批发布许可。
- 边界：本项按当前查看范围/部门成员交集读已有个人冻结事实，未设计新的历史组织版本；结账后人员移部门、被删除、或历史损坏缺个人快照的完整策略尚未穷尽，不据此宣称全系统冻结范围/组织变动已完成。未擅自修复或清洗这些历史状态，现有缺失指标fallback保持。公司汇总人人可见例外保留，不开放个人明细。Q-069/Q-075原待决策保持，长期目标未完成；下一步可转独立目标输入校验或组织变化对存档影响，先复现再判定。


## 2026-10-30 Q-092部门范围团队汇总结账前后成员口径不同（隔离复现、服务器确认，待选择）

- 上轮Q-091备注保留/真实HTTP与完整浏览器为实质进展。本轮恢复目标/规则/质量文件/Git，转为本批Decimal/exact字段在范围矩阵的实际验证，不重复已证实金额红例。仅写质量记录，保留Q-090/091有效前端改动，未新增应用补丁/部署。
- 本机55441/crm_iso_analytics_20261030_test构造三层部门root→child→grandchild加外部门，各一位随机合成用户/客户/CNY订单10.11/20.22/30.33/40.44，个人/各团队/公司目标；先选源订单/回款/发货/目标/存档均空的历史期间。真实ASGI测试四种CurrentUser范围self/department/department_and_sub/all，在实时及正式freeze之后将本套件四张源订单置cancelled仅留存档，验证精确个人行/下钻仍遵守范围、无权userid/deptid均403，公司汇总依既有人人可见规则保持101.10。8组均通过（2020-02/result.log），不冒称正式角色登录/生产浏览器或整个权限矩阵已完工。
- 首次临时脚本在ops/backups而非backend/scripts，未加入scripts搜索路径导致_test_support导入失败，修正加载路径后才实际运行，initial-loader.log保留；无应用问题。第一矩阵只核了允许的部门行集合，未断言团队行金额，不能用这次PASS包装汇总已正确。
- 新增团队金额断言，另空2021-02完整重跑：department范围root团队实时summary10.11/detail10.11；正式全公司结账后summary60.66/detail仍10.11。department_and_sub及all同一团队实时/存档均summary60.66/detail60.66，外部门40.44不计入该团队。probe-team.py/team-result.log确认同账号/团队在结账边界变数，即使源事实消失，原存档该范围读数仍不一致，不是浮点误差。
- 原因targets实时桶先按操作者owner_ids收窄、部门行再与department_member_ids（含下级）交集；冻结时由管理员all收集完整部门含下级汇总，读取_filter_frozen_by_scope只检查dept_id允许后直接用整部门存档覆盖，未按该操作者成员集合拆分。下钻始终与查看范围取交集，所以部门主管结账后总数/明细不一致。
- 两种方案涉及权限/团队统计口径，未自行实施：A建议汇总始终按原查看范围，结账后用已存的可见个人事实/明细汇总，保留原完整存档、不重写；需验证新客/复购/来源消失及部门成员变化的边界，不能简单拿实时重算冒充冻结。B允许本部门主管看本部门含下级团队汇总（类似既有公司汇总例外），实时补齐同口径，但不因此开放下级个人明细；这会扩大原部门范围下的聚合可见性，需用户选择。未选择前不改权限或改历史快照。
- 已请求具体选择，用户答“没懂”，不构成批准；已用“本部门10万、下级50万，结账前10万/后60万但明细10万”解释，并简化询问“按原权限显示”或“本部门主管也看含下级汇总”。等待明确答复；Q-069/Q-075原待决策不变，Q-092未实施，不因自动继续或上批发布许可视为获准。
- 只读SSH当前实际40a6864，targets.py SHA256 15c0f290ca7e91201d11b5ebf8f8179bb49f171b7de4c71076c61e7d81fef732、target_actuals.py cfea88899f7883e916f00abf5aec50c1026d776d7a36a75e29c3ba2b5588429f与固定提交匹配；当前工作区backend/app无差异，确认服务器相同路径。未读取生产组织/订单/金额或进行生产写操作。
- 证据ops/backups/analytics-scope-exact-20261030/。正常停止隔离PG，未新加永久测试或重复未改代码的1574/前端构建，diff通过。当前仅Q-092处理受人工决定约束，其它独立审查仍可推进，长期目标未完成，不满足全局blocked条件。


## 2026-10-30 Q-091目标编辑省略备注、无意清空已有说明（服务器确认，本地修复）

- 上轮Q-090已有版本保护接线/正式冲突验证/完整浏览器为实质进展。本轮重读目标/规则/质量文件/Git，接续独立备注字段往返；保留Q-090四文件及记录，不把其它编辑字段当已验，也不沿用已结束的40a6864批次部署许可。
- Q-091/P2仅本机55441/crm_iso_analytics_20261030_test：随机合成负责人，通过正常admin登录/正式upsert创建2026-06个人目标36，销售100/复购50，remark为“隔离备注：展会目标，不应因修改金额被删除”；正式GET返回该备注，页面编辑表单没有备注控件。真实浏览器打开不改直接保存，捕获payload无remark，定向断言失败（browser-red.log/red-payload.json）。原样请求经正常登录回放正式ASGI，更新同36，独立SQL remark从非空变None；最新审计before_data备注仍原文、after_data备注None（api-red.log），证明实际写入删除，不是只漏展示。
- 根因页面既不显示/编辑也不回传已有remark，SalesTargetUpsert默认None，原服务明确row.remark=remark；不应为此修改接口省略/清空语义或增加备注编辑新功能。唯一最小前端修复：与Q-090原行版本一起记住原备注，只有period/user/dept均与打开对象一致时带回；切换对象/月份保持原提交规则，不复制隐藏旧备注到别处。没有新控件/交互/权限/后端/数据库/事务/依赖，保留未编辑字段，显式API清空备注能力不变。依赖同工作区Q-090版本保护，避免把旧备注覆盖他人新备注；未来应同批发布。
- 实施脚本首轮查找旧注释锚点失败（ValueError），发生在写文件之前；shell继续执行的构建仍为旧实现，定向绿例自然失败，原browser-green.log保留，不当作修复通过。改正锚点应用补丁后重新构建/验证，最终build-final.log/browser-final-green.log才是有效绿例。
- 绿例重新创建合成目标37，避免拿红例已清空备注当保留对照：浏览器发送原remark及版本，正式ASGI原样回放更新同37，SQL前后remark均原文、审计前后均保留（api-green.log）。没有恢复/清洗生产或已有历史错误备注；红例36仅本地合成证据保持。
- 永久fixture为现有团队/个人目标提供不同合成备注；confirmation_browser_smoke增加个人/团队正常保存原备注、409冲突保留输入与原备注、切换下一月份省略旧备注断言。定向分析片段通过（slice-green.log），完整浏览器进程exit0，新增BROWSER_TARGET_EDIT_PRESERVES_REMARK_WITHOUT_COPYING_TO_OTHER_PERIOD=true及既有所有断言通过（full-browser.log）。TypeScript＋生产build通过，diff通过；后端应用未改，不重复上批1574或假称本轮整套后端已跑，真实HTTP/PG/审计验证另行完成。
- 只读SSH实际应用前后端40a6864；targets.py SHA256 15c0f290ca7e91201d11b5ebf8f8179bb49f171b7de4c71076c61e7d81fef732、router.py b06387c31bc413b3c5e5fc8a8ac58089e1d435ac018476d4937b9d2b680a655e与固定提交匹配；当前已确认入口AnalyticsPage-Be_vwx_J.js SHA256 1f19b597454ee5e9e2a1312fb2b098eed36ab4980cb5f963ba58bc75d535484b与固定包相同。包实际页面同一备注测试缺remark失败（deployed-browser-red.log/payload），确认服务器同问题，未读取生产目标/备注或写服务。
- 证据ops/backups/target-remark-20261030/。专用隔离PG已停止，浏览器/profile/server正常finally清理，修改/永久测试/fixture/本记录保留本地未提交/推送/部署。本轮不新增备注编辑入口；若以后需要展示/编辑备注属于独立UX选择，当前不为此索要不必要批准。Q-069/Q-075原决定保持，长期目标未完成。
- 下一步可转目标作用域切换、删除/恢复或其它未充分覆盖业务链路；不因版本/备注已验宣称整个目标管理完成。新生产发布仍需当前项/批次明确授权，原40a6864线上完整核验事实不变。


## 2026-10-30 Q-090目标编辑未传既有版本号，旧表单可覆盖他人新值（服务器确认，本地修复）

- 上轮当前Q-081至089发布40a6864及491文件/同SHA CI/健康核验已完成，为实质进展。用户恢复长期目标后本轮重读目标原文、AGENTS/质量文件/Git，起点干净HEAD fd3a335（仅比应用多发布文档）；接续已记录但尚未复现的目标并发版本线索，不沿用上批发布授权上线新问题。
- Q-090/P2真实本机55441/crm_iso_analytics_20261030_test：随机合成负责人，通过正常admin登录/正式upsert创建2026-06个人目标100，GET行含updated_at；模拟另一编辑者用该版本改200成功，同一旧表单带旧版本正式提交409，而不带expected_updated_at却200、SQL金额退回100。旧页面浏览器真实打开该接口行保存，捕获JSON确实无expected_updated_at（browser-red.log/red-payload.json）；不是只依据静态字段缺失认定问题。两个编辑次序由同一测试管理员会话顺序发请求，不冒称两个真实生产员工或并发线程；已足够复现旧表单丢更新触发条件。
- 原后端upsert_target已在行锁/刷新后比较版本并有明确409提示；列表也已有updated_at，但前端类型/表单原行状态/提交均漏接。唯一最小修复只接已有保护：SalesTargetRow.updated_at、upsert payload.expected_updated_at类型；打开已有真实目标时记原period/user/dept/version，提交同一期间/对象才带原版本。切换月份/个人/部门时不拿旧对象版本校验另一目标，无target_id的实绩补零行/新建及无旧字段响应不虚构版本。权限、后端策略/锁/数据库、输入/弹窗布局/统计规则不改，无新流程/依赖，已有409处理和用户输入保留复用。
- 修后浏览器捕获payload含正式GET的原始版本（browser-green.log/green-payload.json）。正式ASGI回放：先用最新版本把目标改200，再用捕获旧请求409；独立SQL金额/复购/updated_at逐项不变，成功审计数量不增。重新GET后用新版本保存300成功（api-green.log），说明保护不阻止正常更新。源记录仅本地合成，无生产数据操作或历史数据清洗。
- 永久confirmation_browser_smoke/合成fixture为已有团队和个人目标增加真实版本时间，断言正常保存精确传回原版本；模拟409时点名提示、保持输入123.45和编辑窗口、取消不重试覆盖。又验证编辑中改选下一月份，正式请求period变化且expected_updated_at省略，避免旧版本跨对象。第一次该新增月份测试仅等120ms就读到旧period而失败（slice.log），未删断言；增加等待可见Select已显示新月份后再保存，slice-second.log及最终完整回归通过，属异步选择渲染等待，不改产品延时/规则。
- TypeScript＋生产build通过（build.log，既有警告保持）；最终完整confirmation_browser_smoke进程exit0，新增BROWSER_TARGET_EDIT_SENDS_VERSION_AND_409_PRESERVES_INPUT=true、BROWSER_TARGET_CHANGED_PERIOD_DOES_NOT_REUSE_VERSION=true及原精度/团队/其余全部断言通过（full-browser-final.log）。后端应用未改，本轮不重复上批1574单测或冒称重新跑过全后端；真实路由/SQL/审计冲突及刷新绿例另行完成，diff检查通过。
- 只读SSH当前WEB标记40a6864，运行AnalyticsPage-Be_vwx_J.js SHA256 1f19b597454ee5e9e2a1312fb2b098eed36ab4980cb5f963ba58bc75d535484b，与已验证40a6864包相同文件完全一致；用固定包实际页面跑同一版本断言失败，payload仍缺expected_updated_at（deployed-browser-red.log/deployed-red-payload.json），确认当前服务器页面同一遗漏。未读取生产目标或写服务器。
- 证据ops/backups/target-edit-conflict-20261030/。本机专用PG已正常停止，浏览器/profile/server按finally清理。当前四个前端/浏览器/fixture改动及质量记录保留本地，未提交/推送/部署；本项新发现不自动纳入上一批已结束的部署许可。Q-069/Q-075原待决定保持，长期目标未完成。
- 边界：仅保护已有版本且未切换作用域的编辑；切换到其它期间/对象仍遵守原upsert规则，不擅自改变成强制新建/移转，也未改变旧客户端省略版本的兼容规则。备注字段往返仍待独立验证，不以并发已修宣称目标管理完工。下一步可转其它未充分覆盖链路或实际验证备注保存，生产发布需本项/当前批次明确授权。


## 2026-10-30 当前分析/目标修复批次已部署并完成独立核验（40a6864）

- 用户当次“确定服务器上代码有你修复的问题的话就修复部署”及“这个做完”已落实。本批Q-081签单/Q-082回款/Q-085复购结账精度、Q-083发货漏算、Q-084已批准逐行发货舍入（含年度遗漏消费者）、Q-086分析整元精度、Q-087已批准保留范围的超限提示、Q-088团队目标身份和Q-089目标金额编辑均已发布。Q-069/Q-075未决方案、目标版本/备注静态线索不纳入，历史错误存档/目标不自动清洗或重算。
- 最终应用固定提交40a686422ea3a996c5482804721b0f4e7ba7f600，GitHub/Gitee两现有pending-acceptance已同步并逐一ls-remote核对。本批首次CI暴露的浏览器启动无日志及旧脚本Decimal对float比较，均按前两节保留失败证据并修正测试；未删业务断言/放宽容差/更改浏览器安全参数。最终同SHA CI41159819959（https://github.com/ytsbx/crm-sales-agent/actions/runs/41159819959）后端与UI两job均completed/success，ci-final.json已核实headSha和全部job，不拼接不同提交的成功结果。
- 最终CI包含空隔离PG迁移/种子、1574后端单测、静态/登记检查、完整接口清单（含旧check_target_actuals及新增金额/超限两套件）、前端类型/构建/Node/完整确认浏览器及后续UI冒烟。实际固定应用包完整浏览器已独立通过；诊断/旧期望两后续提交不改应用文件，最终491文件manifest与原已验证应用包逐项一致，无漏提交的新脚本/fixture。
- 最终固定包/tmp/crm-release-analytics-40a6864.tar.gz SHA256 9cc6b2f76e9d9ab6f2019535e76696b1e643fce5b4ca47181d528063c3148283；远端工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab及包SHA均匹配。预检验证全部旧app/迁移/依赖/配置/实际migration head/运行路径守卫；正式apply再次预检后备份切换，返回status=verified，备份/opt/crm-sales-agent/backup/unified-40a686422ea3-law4gmyi保留。没有数据库迁移、依赖升级、业务数据写入或正式配置调整。
- 独立发布后核验postcheck.py/log：DEPLOYED_COMMIT/WEB_DEPLOYED_COMMIT均40a6864，RELEASE.json verified；491文件全部SHA匹配；crm-sales.service active，直接8010/health status up/database ok，钉钉OA stream_connected true。当前index入口引用AnalyticsPage-Be_vwx_J.js，签单/回款/发货/复购/目标/差额及年度value_exact字段全在实际资源中。统一发布工具另验证公网首页/直接引用入口资源逐字节匹配；未用生产客户/订单造数据验收。
- 证据ops/backups/release-analytics-20261030/含前后服务器源哈希、固定包构建/原包manifest一致、发布工具21自测、失败CI诊断/旧断言红绿、最终CI/预检/apply及独立后检。隔离8097后端及专用PG此前已正常关闭，无遗留测试进程需要生产处理。
- 当前发布任务已完成。仅补本条及QUALITY_GOAL发布基线文档并同步两仓库，文档提交不同于应用SHA是正常状态，不再重启服务。残余：图表坐标/旧JSON数字仍浮点近似；历史错误数据保持原样；未穷尽生产角色、第三方故障和全部并发/性能。目标并发版本/备注等仍需复现后判断，长期质量目标不宣称完成，本次授权不自动扩大为未来新问题发布许可。


## 2026-10-30 同提交CI后端发现旧浮点断言，改为严格Decimal期望后继续发布

- 首次a6848d0 CI后端最终失败仅check_target_actuals四处：实际值Decimal('1234.56')/Decimal('2234.56')，旧期望为不可精确表示的float字面量，Python严格相等为False。该脚本直接调用服务读取内部结果；本批本来就让内部服务保留Decimal，JSON数字契约仍保留。其余接口套件，包括两个新金额/超限套件均通过，1574单测/静态通过。失败段ci-backend-failure-excerpt.log保留，不忽略失败或把它算绿。
- 先在明确本机crm_iso_analytics_20261030_test＋8097独立后端复现同四失败（actuals-expectation-red.log），仅将四个期望常量改Decimal('1234.56')/Decimal('2234.56')，所有业务数值、读取/修改存档/重算/归属/权限/清理断言不删、不加容差、不转float；完整该套件随后通过（actuals-expectation-green.log）。这会在服务回归成非精确float时继续报错，是更严格的金额比较，未改产品代码。
- 诊断提交1b9bafa的新CI41159116131前端job已完整success，原安全参数下Chrome成功启动，说明首次启动超时非稳定复现；首次根因因旧stderr被忽略仍不能断言，保留诊断补丁。该提交与a6848d0的491个应用文件/guards完全相同，包d9e4bcb8666fb7f17bb556a763a9aaed75b2698335abdef98d6fad8b558993e1预检通过，仍未apply。
- 新测试期望补丁固定新提交后，同新SHA重新完整CI/构建与预检再发布；不把不同SHA成功拼接冒充最终CI。当前本批授权保持，不新增业务/数据库策略，无生产写入或重启。证据release-analytics-20261030/，临时本机后端仅供隔离套件使用，完成后关闭。


## 2026-10-30 发布检查发现CI浏览器未启动，补充启动诊断后继续（未切换生产）

- 当前批次固定a6848d0已同步两仓库，统一构建包SHA256 24e549c647cf8190492d1476d40ace7bd02a52054f28f0cac87edfd19d59e328，491文件；实际包完整浏览器回归通过。服务器工具/包SHA一致，默认预检通过全部旧app、依赖/迁移/配置和运行守卫，返回preflight_only；未apply。
- 同SHA GitHub CI41158700686的前端构建/Node/静态步骤已走过，confirmation_browser_smoke在创建DevToolsActivePort前10秒超时，未进入任何业务断言，UI job失败；后端job仍在接口套件阶段。本机及固定包同检查正常，不能因此忽略远端失败或声称CI已绿。
- 原浏览器spawn完全忽略stderr，无法从超时确认原因。仅给测试脚本增加有界8KB stderr收集，超时时包含子进程exit/signal和原错误；浏览器安全参数、等待时间及全部业务断言不变，生产应用源码不变。用隔离本机假浏览器写launch-failure-probe后exit7，诊断输出正确包含标记/exit7；不把这个有意失败探针当业务回归通过。
- 将该测试诊断补丁与记录提交，后续同新SHA CI继续检查，发布包也从新固定SHA统一构建；原包不覆盖、不误部署。当前用户本批发布授权仍适用，未新增业务方案；不在缺证据时修改Chrome安全参数、放宽断言或直接重试掩盖错误。证据release-analytics-20261030/ci-ui-first.log及startup-diagnostics-probe.log。服务器服务未停，长期质量目标未完成。


## 2026-10-30 用户授权本批服务器确认后部署，发布准备开始

- 用户明确“确定服务器上代码有你修复的问题的话就修复部署”，本次覆盖当前已确认并完成验证的Q-081至Q-089补丁，取代这些项目此前先本地/不部署限制；Q-084逐行舍入和Q-087保留范围/明确提示均有具体选择。未决Q-069/Q-075及尚未复现的目标版本/备注线索不纳入，历史存档/目标不自动清洗或重算。
- 本轮重新只读核实生产前后端均8788a89，router/service/target_actuals/target_bases/targets五修改后端文件SHA与固定旧版本原文匹配；前端当前入口含同一旧AnalyticsPage的证据沿各项已记录，并由统一发布进一步验证基线全部app。两个现有远端pending-acceptance、本地HEAD均8957ab3，仅含上批发布记录，没有发现别的电脑新增提交需合并。
- 发布工具21项自测通过；当前改动此前1574后端、13项Node、真实PG/ASGI红绿和最终完整浏览器已通过，构建/TypeScript及diff通过。本轮将以明确新提交构建、等待同SHA完整CI、固定包预检后再apply，不将准备或已获许可写成部署完成。
- 证据ops/backups/release-analytics-20261030/。保留所有已完成测试与残余边界，既有旧数字字段/图表坐标仍有二进制近似、目标并发/其它字段未完成审查，不宣称全系统质量目标完成。现有GitHub/Gitee源码同步按先前明确授权执行，不新增仓库或推送目的地。


## 2026-10-30 Q-089目标编辑回填/提交转浮点，未改金额也会永久少1分（服务器确认，本地修复）

- 上轮Q-088团队目标身份修复与完整浏览器验证为实质进展。本轮重读目标/规则/质量文件/Git，继续目标编辑金额的独立往返路径。保留Q-081至088全部有效改动，未将列表整元显示已修推断为编辑也正确。
- Q-089/P2仅本机55441/crm_iso_analytics_20261030_test：真实admin登录、正式POST upsert在空2026-02建个人目标33，销售/复购目标均为合法99999999999999.99；正式GET原始记录有该Decimal事实，当前工作区响应已带Q-086的精确字段，但页面openTargetModal仍String旧数字，实际表单两金额都成.98。独立profile/本机HTTP/实际构建浏览器不改金额直接保存，捕获JSON两金额均为数值99999999999999.98；真实登录后原样回放到正式ASGI/PG，仍更新同目标33、源库两个字段均从.99变.98（api-red.log），排除只是显示误差或后端另有保护。
- 初次setup.log打印沿用旧团队探针的“department1”标签；实际创建payload、原始接口JSON/fixture和SQL均为user1/departmentnull个人目标，本轮按真实记录解释，未将日志文字当创建事实。已更正脚本后续打印。初始原始fixture另保留fixture-red.json，避免绿例覆盖红例源证据。
- 两个精度断点均验证：回填从旧number读，保存targetNumber又转Number。固定8788a89发布包页面，用可信CDP Input.insertText实际输入.99后，SAVE_VALUES两字段均.99，但请求仍两字段数值.98（deployed-trusted-browser-red.log/payload），证明不仅读取缺exact，用户重新输入也会丢。旧包和当前服务器实际入口资源匹配，未覆盖工作区源码。
- 最小修复仅前端：openTargetModal优先现有sales_target_exact/repeat_customer_target_exact；共享number-input增加parseOptionalDecimal，复用原parseOptionalNumber的空/有限数/无效校验，仅对十进制文本保留字符串（含原来允许的正负号/科学计数法）；其它原来能解析的数值写法继续旧数值，不扩大格式能力。targetMoney只用于销售/复购金额，两字段API类型允许现有后端Decimal已支持的number|string；新客计数仍targetNumber。无金额舍入、上限/币种/权限/表结构/事务或新依赖调整；空金额仍按原规则0，非法输入仍点名阻止，不变null或静默清零。
- 正常录入兼容：运行现有SalesTargetUpsert实际Pydantic模型，3.0300/1e2/+1.01/.50/-0.00均按原约束接受（schema-compatibility.log）；负数和超范围仍由现有ge0/16位2分约束拒绝，没有放宽这些规则。原13项Node金额/输入测试通过（input-unit-green.log，新增两项decimal输入断言），新helper没有改变其他表单的原parseOptionalNumber。
- 绿例重新在空2026-03正式建新个人目标34，避免拿已被红例改坏的目标当“未改值”对照。真实目标行接实际构建，打开两字段均.99，不改值保存时为十进制字符串.99；真实ASGI回放更新同34，SQL前后两个字段都精确.99（browser-green.log/green-payload.json/api-green.log）。这不是重写生产/历史数据，也未声称浏览器同时直连PG：浏览器API回放与真实后端请求验证是两步。
- 永久confirmation_browser_smoke及合成fixture加入大额个人目标：打开精确回填、输入abc不发请求且明确提示、改正后一次保存两个精确字符串、个人userid与空部门保留；原团队取消/保部门和全部年度/目标/人员金额断言保持。第一次full-browser.log和随后full-browser-complete.log重试未发请求而失败，未删断言。定向同页序列调试后读取本机Semi ModalFoundation源码，确认确认按钮leading-only 100ms防连点；测试在错误提示后几十毫秒内就改正并再点，落在去抖窗口。永久测试改为可信浏览器输入事件/等待渲染，并在重试前跨过120ms窗口；产品组件/防连点不改，所有金额/次数断言保留。源码片段modal-debounce-source.txt、slice-debug.log保留，不把这两次自动化时序失败记新产品Bug。
- 最终独立分析片段通过（analysis-final-green.log），完整confirmation_browser_smoke全部通过、进程exit0（full-browser-final.log），新增BROWSER_TARGET_EDIT_PRESERVES_EXACT_AMOUNTS_AND_REJECTS_INVALID_INPUT=true。TypeScript+生产构建通过（build.log，既有警告未擅自重构），Node13项及diff通过。本轮后端应用未改，沿用上轮1574项，不重复或声称本轮新跑过整套后端；真实upsert/list/SQL已另验。
- 只读SSH当前WEB标记8788a89，从当前index解析出的AnalyticsPage-xjx14egF.js SHA256仍0361406cbcda3985e18b62d9ea47af4742b193d0f7077da1ef9d1a575b33f179，与用于重放的固定包完全一致（server-readonly.txt及Q-088包比对），确认当前服务器页面含同一读写断点。未读取生产目标、改生产配置/重启或发布。
- 证据ops/backups/target-edit-precision-20261030/。测试完成正常停止专用PG；源码/测试/fixture/记录保留本地，未提交/推送/部署。本项依赖Q-086已在工作区提供的目标exact字段，旧响应无exact时只能回退旧数字，无法凭空恢复已丢精度，未来需与本批后端统一发布，不能只发前端宣称精度完整。
- 未自动恢复任何历史错误目标/审计，红例仅保留本地合成证据；目标并发版本、备注等其它往返字段仍待独立实际验证，本项不宣称整个目标管理完工。下一步可对旧表单覆盖新目标值做真实并发/冲突对照，再核对其余字段；Q-069/Q-075原选择保持，长期目标未完成。


## 2026-10-30 Q-088编辑团队目标丢失部门、误写全公司目标（服务器确认，本地修复）

- 上轮Q-087获批边界提示/验证为实质进展。本轮恢复目标/规则/质量文件/Git，接续前轮目标编辑静态线索，优先验证常见的团队目标身份丢失，不把金额/版本其它线索一起记已确认。保留所有Q-081至087有效补丁，未部署。
- Q-088/P2真实接口＋隔离浏览器：仅本机55441/crm_iso_analytics_20261030_test，正常admin登录，选无现有目标的2026-01，通过正式POST upsert建销售部department1目标31（销售100/复购200/新客2），正式GET返回target_id31、department_id1。将真实目标行接独立profile/本机HTTP的实际构建页面，点销售部行“编辑”后不改对象直接保存；捕获的真实浏览器请求department_id=null、user_id=null，模态框也回到全公司。browser-red.log/red-payload.json断言失败留存。
- 同一旧浏览器请求再经正常登录回放到真实ASGI/PG：后端200新建全公司目标32，而非更新原团队31，原团队值不变（api-red.log）；排除后端另有机制拦住的可能。浏览器捕获和后端回放是两个步骤，不冒称浏览器同时直连真实PG。没有生产账号/客户/目标操作。
- 根因AnalyticsPage.openTargetModal(row)重建整个表单时只回填user_id/period/金额，漏掉已有department_id字段；提交逻辑按表单部门决定公司/团队，undefined/null自然落为全公司。唯一最小修复只加department_id: row.department_id一行。沿用已有团队选择框和提交规则，准确保存用户正在编辑的原对象，不改变目标权限、范围、字段限制、事务/锁/后端或用户操作流程，无新业务取舍。
- 修后真实构建页面同操作请求department_id1/user_idnull，定向浏览器断言通过（browser-green.log/green-payload.json）；真实ASGI回放返回原target_id31，公司目标数量仍1（先前错误红例生成的32保留），原团队金额保持100/200，证明不再新建/覆盖公司目标（api-green.log）。不擅自清理历史错误目标；本次错误公司记录只是隔离测试证据。
- 永久ops/fixtures/analytics-whole-yuan.json增加该合成团队目标与必要部门/用户选项；现有confirmation_browser_smoke在年度精度场景后验证团队目标打开/取消零写入、重新打开保存一次、department_id和period准确保留、user_idnull。TypeScript＋生产构建通过（build.log，既有警告保持），完整浏览器全部通过、进程exit0（full-browser.log），新增BROWSER_TEAM_TARGET_EDIT_KEEPS_DEPARTMENT_AND_CANCEL_DOES_NOT_SAVE=true，原年度/目标/人员精度断言仍通过。fixture和测试已同工作区保存，未来提交需一起纳入。
- 后端应用未改，不重复上轮刚通过的1574项或声称本轮重新跑过；本轮验证真实登录/upsert/list/SQL及前端构建/完整浏览器，diff通过。此项不代表未传版本号/目标金额编辑等其它静态线索已验证或修复。
- 服务器实际WEB标记8788a89，重新从当前index→入口JS解析出唯一AnalyticsPage-xjx14egF.js，SHA256 0361406cbcda3985e18b62d9ea47af4742b193d0f7077da1ef9d1a575b33f179；与已验证固定发布包解出的同文件完全相同。使用该固定包页面执行同一团队编辑测试也返回部门null、断言失败（deployed-browser-red.log/deployed-red-payload.json），确认当前服务器运行页面含同一问题，不靠残留frontend/src或旧资源glob。
- 证据ops/backups/target-team-edit-20261030/含正式建目标/原始列表、受控浏览器红绿、请求原样回放SQL结果、当前生产入口只读证据、构建及完整浏览器。仅本机命名隔离库，测试完成正常停止PG；无源码提交/推送、生产发布/重启或配置更改。Q-081至088本地有效改动保留，长期目标未完成。
- 下一步：可继续对目标金额打开→提交→PG持久化做真实精度验证，另核对同作用域并发编辑是否遗漏已有expected_updated_at。当前这些仍静态线索，不自动扩大本项修复为已完成；Q-069/Q-075原具体选择保持。


## 2026-10-30 Q-087获批保留存储上限，结账超限明确提示已本地完成（未部署）

- 上轮Q-086显示精度修复、Q-087独立超限复现为实质进展。本轮先恢复目标原文/规则/质量文件/Git，独立静态检查目标编辑；随后用户明确选择“保留上限，明确提示（推荐）”，优先完成本项，QUALITY_GOAL已同步。没有把这个选择延伸为扩大字段/迁移或生产发布许可。
- 最小实现仅target_actuals：保留期间锁、既有已结账拒绝、重算原因检查及原顺序，在新增/改写/删除任何存档前对全部汇总和明细做内存范围检查；Numeric(16,2)按PG到分舍入后溢出的边界为绝对值≥99999999999999.995。非有限值同样拒绝。超限用既有PARAM_ERROR/HTTP422，说明期间、指标、汇总/明细及上限，本次未保存。没有截断/改写金额、加SQL、改锁/隔离或扩表；增加一次线性内存校验，不伪称其完全零CPU成本。
- 新增15项永久test_target_freeze_limits：合法正负上限/.994、±.995/超限/非有限、首次/重算×汇总/明细四组合，确认整批预检在任何存档get/add/delete/execute/flush之前拒绝。首次误用AppError.status_code而实际属性为http_status，修正测试后才有正确状态断言；又因仓库未装pytest-asyncio而四个async测试没执行，改用现有依赖下asyncio.run同步入口，不添加依赖/跳过用例。unit-green.log及unit-complete-green.log含这两次测试编写失败并保留，最终unit-final-green.log为15 passed。
- 永久已登记check_target_signed_precision复用新增check_freeze_limits阶段：随机合成账号/客户，空历史期间单合法上限订单先正式ASGI结账成功，再加合法0.01订单使汇总达100000000000000.00，正式refreeze 422；完整旧汇总/明细的数值、行ID、口径、冻结时间/人/说明均不变。另空期间首次结账超限422且无任何存档；仅把合成额外0.01订单置取消恢复上限后，正式显式重算200。没有改生产或已有真实业务数据，认证为测试CurrentUser覆盖，不冒称真实管理员登录。
- 集成红例独立进程只回放git 8788a89的freeze_period AST、其余精度修复保持，合法上限结账先成功，超限重算实际撞PG NumericValueOutOfRangeError（permanent-red.log）；未覆盖工作区。当前同一永久阶段2011-04/05通过（limit-final-green.log），整套六金额场景+上限阶段也已通过（permanent-green.log）。
- 原Q-086实际合成大额源数据2014-05正式freeze补验：从500变422/code40001，消息明确“2014-05 的签单额汇总金额超出可保存范围…本次未保存”，前后该期间存档仍0（http-green.log）；源金额和历史期间不重算。PG独立SELECT CAST AS Numeric(16,2)对照确认±99999999999999.994可舍为±99999999999999.99、±.995确实溢出，新护栏同样接受/拒绝（pg-boundary.log），不是凭Python默认half-even猜测数据库行为。
- 完整后端1574 passed in15.77s、进程exit0（full.log）；应用/脚本/新增单测pyflakes、diff通过。该轮新增15项pytest已计入1574，真实PG脚本阶段另行运行不混计。本轮未改前端源码，Q-086最后完整构建/浏览器证据保持，不重复不相关前端测试，不冒称远程CI。
- 只读SSH实际部署8788a89，target_actuals.py SHA256 5454a21e42b0ee5a12da9f96e9811425e5e7516dd21fb41b367cfbf68c83301b、model.py f07f5bdeae7bb14ba6c11dde539df77302c5c03d196705681c95d87ec5046a1c，与该固定提交一致，确认当前服务器原函数缺护栏、模型声明仍Numeric(16,2)。未查询生产数据库实际列或业务数据，不宣称线上已触发超限。
- 证据ops/backups/target-freeze-limit-20261030/，测试仅本机两个命名隔离库，完成后正常停止专用PG。Q-081至087本地有效补丁、单测/永久PG套件/前端浏览器记录保留，尚未提交/推送/部署，不改生产配置/重启或清洗历史。Q-087原待决策已被本节授权/实现覆盖；Q-069/Q-075原选择保持，长期目标未完成。
- 下一步可恢复目标编辑链路：当前仅静态看到金额从row数字填表再targetNumber转Number，以及编辑团队目标未回填department_id、未传expected_updated_at；尚未对这些新组合做真实表单/保存/并发复现，不先记已确认、不提前实施。须分别核对是否已有其它机制阻止，保留先前精度和版本冲突业务规则。


## 2026-10-30 Q-086分析文字整元显示因浮点多进位1元（服务器确认，本地修复）

- 用户恢复长期目标后，本轮重读目标原文、AGENTS/质量目标/进度/Git，保留Q-081至085有效补丁。此前仅静态的年度float桶进一步沿真实接口→AnalyticsPage Math.round追踪；没有因上一轮半分回归通过就认定年度大额正确。
- Q-086/P2：仅本机55441/crm_iso_pdf_20261030_test，合成self账号75/客户及两张各自合法CNY订单99999999999999.74、99999999999999.75，2014-05精确合计199999999999999.49；独立PG SUM确认。真实ASGI年度value/目标旧数字字段均为199999999999999.5，目标精确字段为.49。原页面Math.round因此显示¥200,000,000,000,000，按既有整元规则应为¥199,999,999,999,999，多1元；不仅是分位精度没有展示。原固定8788a89发布包真实页面接同一API回放失败（browser-red.log/json），包SHA256仍03b89327906e5b689e4ad7bc6cca86b9c61c3e95c6aa1413e5dbc2f8e8c59ad9，与已验证发布记录一致。
- 最小修复：target_bases年度金额桶保持Decimal，四个金额序列保留旧value数字、增加value_exact；计数仍int。money.ts增加formatWholeCny，精确字符串用BigInt取整，普通/旧数字输入沿原Math.round；负数半值仍向正无穷、负零及分组/¥/整元格式不变。AnalyticsPage年度文字、目标签单/发货/确认回款/复购实际优先精确字段，图表坐标继续数字；不改显示为小数、不引入库或外币能力。
- 定向浏览器进一步发现同页业务员表现和目标差额仍出现错误大额，原加强断言失败；没有删除失败断言。补齐service.sales_user_stats订单/回款exact字段、目标行销售/复购target精确字段及Decimal差额exact字段，前端对应实际/目标/差额文字优先读取，销售负差额原-¥位置保留。旧数字字段/比例算法兼容保留，范围、统计来源、查询条数、锁/事务/表结构及历史存档均不改。这些为同一真实数据在同页遗漏出口，按纯精度修复统一完成，不另造重复编号。
- 两笔合成订单再建立各自已确认合成回款，以验证同一精确合计的回款文字；独立PG订单/确认回款SUM均199999999999999.49。记录由ORM构造、ASGI CurrentUser覆盖，不冒称正式登记/确认全流程。14份真实隔离API响应回放到独立profile/本机HTTP/实际生产构建：最终年度签单与回款、业务员表现订单/回款、目标签单/确认回款/差额均显示正确整元。专用browser-complete-green.log/json通过；初次browser-final-green.log因差额仍错失败，文件名含green不代表通过，保留证据。
- 永久回归：ops/payment_allocation.test.mjs复用现有money测试装载方式增加整元大额.49/.50及普通/负数/负半值/负零/旧数字/无效输入两项，总11项通过；现有CI已运行该文件。两个已登记PG套件扩展年度signed/received/repeat_net/shipped精确值及业务员表现、目标差额/非零1.49公司目标。ops/fixtures/analytics-whole-yuan.json只含合成接口回放，confirmation_browser_smoke末尾增加年度/目标/业务员文字完整行断言，包含差额，未来CI可阻止再次漏接。新增fixture和脚本登记必须同批提交，当前均本地。
- 脏隔离库组合验证暴露测试期间选择不完整：发货套件原只查空快照/目标/发货，选中已有本次大额签单的2014-05，随后全公司结账触发Numeric(16,2)溢出（shipped-green.log，保留）；不是放宽金额断言就通过。仅加强该测试空期间护栏纳入订单和确认回款，不清旧数据/不改应用金额上限；新选择场景通过。该真实超限500另按Q-087待决定记录，不藏为纯测试噪声。
- 为排除历史夹具干扰，另建本机同端口crm_iso_analytics_20261030_test新空隔离库，完整alembic upgrade head/seed成功（fresh-migrations/seed.log），两套件最终共12组通过（fresh-signed-complete/fresh-shipped-complete.log），包含三指标大额/普通及六发货场景；测试只限两个命名隔离库，不触碰开发/演示/生产库。完整后端1559 passed in21.08s、进程exit0（full-complete.log）；最终前端TypeScript+生产构建、11项Node及pyflakes/diff通过。完整confirmation_browser_smoke新旧全部通过、进程exit0（full-browser-complete.log），最终新增BROWSER_ANALYTICS_ANNUAL_TARGET_AND_PERSONNEL_EXACT_WHOLE_YUAN=true。
- 本轮只读SSH后端/前端标记8788a89；target_bases.py SHA256 176d8d62edd8cd38b8361215379eb2e45b4c88a309110ceaa4c6096a6bec774d、service.py a085fcc80819f46693df74f73a77ab10d3bfe94aa11f26c9d9bd3d2bc56abd30与固定提交匹配；当前index直接引用AnalyticsPage-xjx14egF.js SHA256 0361406cbcda3985e18b62d9ea47af4742b193d0f7077da1ef9d1a575b33f179，未读sales_actual_exact/value_exact且25处Math.round，确认当前运行页面同路径。未用残留frontend/src或旧glob资源冒充运行版本，未查询生产业务数据。
- 证据ops/backups/analytics-whole-yuan-20261030/。本轮不提交/推送/部署、不改生产配置/重启，不自动清洗历史存档，专用PG测试后正常停止。残余：图表数字坐标仍浮点近似，旧JSON数字仍非大额无损契约；目标编辑/比例及其它分析KPI不能据文字显示修复推断全部精度完成。Q-069/Q-075原选择保持，长期目标未完成。

## 2026-10-30 Q-087结账汇总超过存储上限返回500（隔离复现，待边界处理决定）

- Q-086两个合法大额订单组合触发真实边界：单张订单均满足Numeric(16,2)，同一期间/范围SUM可超过99999999999999.99，而ActualSnapshot.actual_value仍Numeric(16,2)。在本机旧隔离库2014-05另有正常20元发货订单，正式freeze全公司sales合计200000000000019.49时出现NumericValueOutOfRangeError。此前仅静态担忧现由本轮套件失败和独立ASGI最小探针确认，不再标为纯猜测。
- 独立freeze-limit-probe.py先核实该合成期间无存档，使用合成管理员/all权限调用正式POST freeze，HTTP500/code50001仅提示服务器内部错误；独立SQL确认前后存档数量均0，事务整体回滚，没有部分结账或源订单损坏。数据为本地合成，不冒称正式生产角色或生产实际故障。完整输出freeze-limit.log保留，不在生产注入大额数据。
- 方案A建议：保留现有存储上限，在写入前明确拒绝并提示具体期间/指标汇总超限，避免笼统500；无法完成这类极端期间的结账，但不迁移表/重算历史。方案B：扩大汇总及必要明细金额字段，允许更大期间，需要数据库迁移、兼容/回滚及更广边界验证。不能只按单笔合法就默认获准扩大存储，也不截断/压缩金额来伪造成功。
- 按AGENTS业务边界/数据库结构先人工决定，记录后请求选择；本项尚未修改任何处理分支/字段上限。Q-086显示精度已经验收，不因本项待决策阻止其它独立审查；长期目标不满足全局blocked条件。若获方案授权先本地验证，生产发布仍需当前批次明确决定。


## 2026-10-30 Q-084补齐年度发货消费者（沿用已批准算法，本地完成）

- 上轮Q-085复购精度修复验证为实质进展。本轮重读目标/规则/质量文件/Git，保留所有当前补丁，检查annual_bases与targets三口径一致性。发现Q-084上一轮仅修目标汇总/下钻，遗漏年度分析发货消费者；本轮作为同项遗漏补齐，不另造重复问题编号。用户已明确批准“发货汇总和明细统一按订单算法”，年度发货汇总沿用同一决定，未另选业务算法或扩大成发布许可。
- 新实证：仅本机55441/crm_iso_pdf_20261030_test，真实admin登录后只读既有合成2016-10事实（上轮正常建单/实发的62/20），GET sales-targets/bases?year=2016中10月shipped为0.03，同期targets为0.04，独立PG逐行round乘积SUM为0.04。两真实响应而非静态猜测，probe.py/red.log保留；未造生产数据或改变历史源订单。
- 最小补齐：将上一轮targets私有SQL逐行金额表达式移到现有target_bases.shipment_line_amount，由目标汇总、批次下钻、annual_bases发货聚合三处共用；年度从SUM(原始乘积)改SUM(逐行到分金额)，保持原查询/范围/日期/订单状态与聚合结构。BASIS_NOTE明确说明逐行到分，避免页面仍显示不完整计算说明。不顺手改变年度其它float桶或新增精确字段/前端逻辑，没有额外查询、数据库/锁/事务变更或依赖。
- 正式HTTP绿例同源事实年度与目标均0.04（http-green.log）。永久check_target_shipment_lines在原六场景增加普通/半分五场景的年度真实ASGI读取比较，含同价同量、不同价格、分批、半分单批/分批，2013-07至11通过；既有大额第六场景目标/下钻/存档通过（2013-12），年度大额仍是旧JSON float契约，明确不拿普通/半分断言宣称年度大额无损。
- 永久红例独立进程仅回放git 8788a89的annual_bases函数AST，保留当前目标修复和共用表达式；前三整分场景通过，第四半分场景在年度金额对期望断言失败（permanent-red.log），说明新增年度断言能抓住真实遗漏，不靠删除旧断言或人为放宽来通过。完整后端1559 passed in15.09s，进程exit0（full.log）；两应用文件/永久脚本pyflakes及diff通过，无前端改动/远程CI/提交/推送/部署。
- 另一静态线索未记Bug：annual_bases回款查询没有显式取消过滤，目标有；但专用订单取消接口在订单锁内按order_id统计任何已确认回款并422拒绝取消，不能正常得到“已取消订单+已确认款”。与进度既有2026-10-29回款统计排除证据一致；本轮只复核当前守卫和此前证据，未新执行该完整财务流程，也不造非法组合/擅自加过滤或更改现金口径。历史异常数据是否处理仍需另证据/决定。
- 证据ops/backups/annual-shipment-rounding-20261030/。服务器target_bases.py原文已在Q-085只读核对为实际8788a89同SHA，当前旧annual函数本地回放也确认同一缺口，无生产写入。测试结束正常停止专用PG。
- 边界：年度序列仍实时按业务单据计算，历史结账存档保持原值；原有错误存档与修正后的实时年度结果可能不同，本轮不自动重算存档。年度金额float精度及前端图表/整数展示消费者仍需独立证据，不声称整个分析模块完工。Q-081至085当前修复仍本地待当前批次发布授权，Q-069/Q-075原具体选择保持；长期目标未完成，下一步可转年度金额精度或其它未充分验证业务链路。


## 2026-10-30 Q-085复购业绩结账大额丢分（服务器确认，本地修复）

- 上轮Q-084获批发货算法/验证为实质进展。本轮重读长期目标/规则/质量文件/Git，转到先前仅静态的复购目标→期初老客池→repeat_net下钻/结账链路；保留Q-081至084补丁，不把这些验证泛化成所有年度分析都正确。
- Q-085/P2：仅本机55441/crm_iso_pdf_20261030_test，随机合成用户/客户，先造年初前1元非取消订单建立真实老客资格，再造当年合法CNY大额99999999999999.99；选未冻结且无当年订单的空年份，先提交源事实，再让正式目标读取自动冻结老客基准。2001-01真实ASGI实时repeat_customer_actual/下钻均.98，正式freeze后独立SQL repeat_net汇总及明细存.98，源订单仍.99（probe.py/red.log）。认证为测试进程CurrentUser覆盖、事实由ORM建立，不声称真实生产角色或正式建单全流程。
- 根因target_bases.repeat_net_by_owner将PG SUM转float，targets._repeat_actual消费/冻结读取继续float，repeat_net下钻也float；router以Decimal(str(...))已无法恢复。最小修复仅该按人复购聚合保持Decimal、targets复购取数/下钻/冻结读取保持Decimal，新增repeat_customer_actual_exact供router冻结收集优先读取。该函数只有目标服务消费者，没有顺手改annual_bases的通用_bucket或其它年度指标；期初客户集合、取消过滤、签单归属/范围、SQL条数、锁/事务/数据库结构及前端展示不变，无新依赖/口径选择，属唯一明确精度补齐。
- 复用已登记check_target_signed_precision扩展为签单/回款/复购三指标×大额/普通六场景。复购专选无BasisSnapshot且无当年订单的年份，先建立真实往年订单，再自动按正常基准流程冻结，不篡改既有老客池；新增同期间7.77元已取消订单，确认不计入。验证个人/公司实时与冻结精确字段、实时/存档下钻、原始订单与SQL持久化汇总/明细，普通3.03和大额.99均通过，最终复购2005-01/2006-01（final-green.log），原四签单/回款场景也通过。合成客户建档早于往年订单，避免用不必要的时间矛盾夹具。
- 永久红例独立进程仅加载git 8788a89的repeat_net_by_owner AST，保留当前其余修复；同脚本签单/回款四例先通过，复购实时精确值对.99断言失败（permanent-red.log），未覆盖工作区。修前真正存档丢分的独立probe和该源函数回退互相补强，不拿新增字段缺失制造假红。
- 新真实ASGI补验2007-01：源订单104仍.99，新存档repeat_net汇总及明细均.99，旧2001-01错误存档仍.98（http-green.log）。旧JSON数字字段因原数字契约及序列化仍可显示.98，精确字段为.99；没有宣称旧浮点字段能无损表示大额或前端全部消费者已修。现有前端整元目标展示不变。
- 完整后端1559 passed in15.36s，进程exit0（full.log）；应用target_bases/targets/router与永久脚本pyflakes、diff检查通过。永久脚本增强发生在同应用实现的全测试期间，最后fixture取消对照另行运行通过，不混计pytest项数；无前端源码改动，不重复已有浏览器或假称远程CI成功。
- 只读SSH实际部署8788a89，target_bases.py SHA256 176d8d62edd8cd38b8361215379eb2e45b4c88a309110ceaa4c6096a6bec774d、targets.py fc954fb37807b996f739e9401b3a5b8337a6f0a783cf902e9f5add62123da007、router.py abad460acd5e2d754431886c11e16e2d34120a54e767ee9590a082e0252cda67，均与该固定提交一致（server-readonly.txt），确认服务器同转换路径，未读取生产客户/订单或声称实际历史已损坏。
- 证据ops/backups/target-repeat-precision-20261030/。专用PG测试后正常停止；Q-081至085均本地验收，尚未提交/推送/部署，历史存档不清洗。本轮仅证实精度问题和取消过滤对照，不确认“退货重复扣减”问题，也不声称完整退货/退款流程已回归。年度annual_bases的金额桶仍float，需真实响应/消费者复现，当前仅待调查。Q-069/Q-075原具体选择保持，长期目标未完成，后续可转年度分析及未覆盖权限/业务链路；发布仍需当前批次明确授权。


## 2026-10-30 Q-084获批统一订单算法，发货舍入/精度本地修复验证完成（未部署）

- 上轮Q-084正式接口/固定线上源码复现及结账证据为实质进展。本轮先重读目标、质量文件/Git，因用户起初未答算法，先独立检查新脚本与CI执行方式；随后用户明确选择“统一按订单算法（推荐）”，才开始实现。该批准仅先本地验证/不部署，未扩大为生产许可；QUALITY_GOAL已同步。
- 最小修复analytics.targets新增共用_shipment_line_amount SQL表达式：PG numeric round(shipped_qty*unit_price,2)，存储数量已是三位，与订单core.money.line_amount逐行ROUND_HALF_UP一致。个人/公司月度累加与批次下钻SUM都消费该表达式，Decimal保持到冻结收集；补shipped_actual_exact并优先冻结、冻结读取保留Decimal。router同步优先精确发货值；Q-081已有存档精确明细复用。原定义注释即要求line_amount，本次纠正不符实现，未另改指标来源/状态/日期/归属口径或历史basis_version。
- 未增加查询、JOIN、事务/锁策略、表结构/索引/依赖、外部请求或前端布局；Q-083错误去重删除保持。原JSON数字字段保留兼容，新增精确字符串用于后端存档；前端整元展示未改变。不改考核主口径/差额/比例、不清历史数据。SQL舍入与Python订单helper用真实PG对照验证，不把SQLite浮点当PG证明。
- 正式API绿例：实际admin登录→建客户/订单62，两条1×0.015和3×0.005→排批次20→正式登记实发2016-10，原始乘积和0.03，订单0.04、目标汇总0.04、下钻0.04；正式freeze后SQL汇总及明细均0.04。旧2016-11错误存档SQL仍汇总0.02/明细0.03，明确证明没有自动重算历史。http-green.log及probe-green.py保留。
- 永久check_target_shipment_lines扩展为六场景：同价同量单批20、不同单价单批21、相同单价分批20、两条半分单批0.04/分批0.04、大额99999999999999.99。每组检查个人/公司实时与冻结精确值、实时/存档批次下钻及合计、SQL汇总/明细，旧整分JSON数值断言保留，大额使用新增精确字段且仍要求旧total为JSON数字。2013-01至06全部通过（permanent-green.log）。既有check_target_signed_precision的签单/回款大额和普通四组也通过（signed-received-green.log），未将它们计入pytest项数。
- 全后端1559 passed in15.41s、进程exit0（full.log），三应用文件/两个永久脚本pyflakes、diff检查通过。应用改动后未运行远程CI，也没有前端改动，不声称CI或浏览器回归已完成。本轮原问题红例沿用本轮前已确认的固定线上源码/正式API证据，不把半分决策前的调查结果当作本次新失败。
- 本轮起初CI兼容检查结果：.github/workflows/ci.yml实际服务为本机127.0.0.1:5432/crm_iso_test，满足两脚本本机PG及一次性库护栏；清单顺序执行独立进程，合成认证覆盖在finally恢复；新增清单与脚本在最终提交时须同批纳入，目前均在工作区，未假称已经登记到远端。此为配置/执行链静态核对，本轮未启动另一个完整CI等价服务或空库全套。
- 证据ops/backups/target-shipped-rounding-20261030/已含正式红例、固定部署8788a89函数AST复现、错误冻结落库及本次正式HTTP绿例/历史保留/六场景/签单回款/完整后端日志；服务器同文件SHA已由Q-083只读确认，无生产写入/重启/配置变更。本轮完成后专用PG正常停止。
- Q-081/082/083/084均本地完成验收，尚未提交/推送/部署。既有错误历史存档仍按显式重算/原因流程另行处理；分批舍入可能与整单有合理尾差，本次不擅自引入最后一批补差。尚未全面验证部门范围矩阵、极大行数性能和所有分析消费者，不以六场景覆盖全系统。Q-069/Q-075仍待原具体方案，长期目标未完成；下一步可转复购/分析金额或其它未充分覆盖链路，发布需当前批次明确授权。


## 2026-10-30 Q-084发货半分金额的汇总/明细舍入不一致（已复现，待算法决定）

- 上轮Q-083错误去重修复验证为实质进展。本轮重读目标原文、规则和Git，调查尚未证实的发货舍入边界；本轮未修改任何应用或永久测试，Q-081/082/083有效补丁保留。
- Q-084/P2仅隔离55441/crm_iso_pdf_20261030_test真实登录/正式建客户、订单、排批次、实发：两行单价0.015数量1，2019-11订单52/批次8订单总额0.04，原始乘积和0.03，目标shipped汇总0.02，下钻0.03。为排除与Q-083同价同量去重混淆，再用不同数量/单价（1×0.015及3×0.005，乘积各0.015）于2017-11订单53/批次9复现同样结果；独立进程加载实际部署8788a89的targets_with_actuals/drilldown/_row函数AST、未覆盖工作区，2017-12订单54/批次10也完整复现，证明不是Q-083删除group_by引入。
- 结账影响补证：2016-11订单55/批次11同组不同数量/单价，通过正式freeze后SQL中shipped汇总永久存0.02、shipped明细永久存0.03，源订单仍0.04。不是前端纯显示误差，也不能以普通整分永久用例已通过推断半分正确。所有这些为本地合成历史事实，未触碰生产数据。
- 代码原因：order.service按core.money.line_amount（逐行ROUND_HALF_UP）存订单金额；targets汇总逐实发明细float+round，drilldown却SQL先按批SUM后float+round，两者舍入阶段不同，二进制近似还使0.015舍入到0.01。当前注释声称与line_amount同口径，实际没有调用。单纯换Decimal仍无法决定逐行还是逐批舍入，不能机械修复。
- 方案A建议：发货每条实发明细沿用订单line_amount先到分，再汇总至批次/月；示例统一0.04，与订单逐行规则一致，但会改变部分实时半分统计。方案B：整批原始金额相加后统一到分，示例0.03，汇总和明细能对齐，但允许与订单逐行总额尾差。两方案都保留历史冻结存档、不自动清洗；若未来重算历史须按既有显式流程另行决定，分批舍入仍可能与整单有合理尾差，不能承诺任何分批方式都等于订单总额。
- 已向用户请求该算法选择；用户答“没懂”，不代表批准。已简化为“是否让发货汇总和明细统一沿用订单算法，先本地修复测试，已有结账不动、不部署”，等待明确答复。依据AGENTS业务规则先决定，未提前实施A/B，也不将Q-081/082精度修复许可延伸为本项口径许可。
- 证据ops/backups/target-shipped-rounding-20261030/含probe.py/red.log、distinct-red.log、固定线上源码AST复现baseline-red.log、freeze-red.log及脚本。服务器对应文件当前哈希已在Q-083只读确认，未再次写服务器或查询真实业务。专用PG本轮结束正常停止；本轮无应用变更，不重复1559回归或冒称修复通过，diff检查通过。
- 当前仅Q-084该项等待人工算法决定，长期审查仍可转其它独立链路，不满足全局blocked条件；Q-069/Q-075原选择保持。已有Q-081/082/083均本地待发布，无提交/推送/部署。


## 2026-10-30 Q-083同批次同价同量的不同发货明细被汇总去重（服务器确认，本地修复）

- 上轮Q-082回款精度修复/验证为实质进展。本轮重读长期目标、AGENTS、质量文件与Git，保留Q-081/082工作区；转查发货逐行/批次金额。没有把float静态线索直接当Bug，本轮实际确认的是普通金额就能触发的独立漏算。
- Q-083/P2正式API复现：仅本机55441/crm_iso_pdf_20261030_test，真实admin登录→建客户→手工建单两条不同订单明细（各SKU1、数量1、单价10）→排同一批次→登记实发2019-09-15，所有写入为正式应用接口。订单46/批次1独立SQL源金额20，实时shipped下钻20，目标汇总却10。首次探针误取订单详情items字段导致KeyError，正式明细路由为/orders/{id}/items；修正探针后才得到有效红例，初次输出probe-initial.log保留，不归因应用。
- 根因analytics/targets.py的shipped_stmt没有聚合表达式，却按月份/负责人/批次/数量/单价group_by，两个不同批次明细碰巧数值相同就合为一条。批次明细→订单明细→订单/批次均为主键外键一对一连接，天然一条实发明细一行，无需去重；删除这处7行group_by即可同时修好个人/公司查询。SQL过滤、权限范围、实际发货日归月、逐行舍入/浮点处理、锁/事务/表结构/前端均不改，不重算已有错误存档，无新增查询/依赖，属唯一明确的遗漏事实修复。
- 正式API绿例：新订单47/批次2、空2019-10，源金额20、汇总20、下钻20，见probe-green.py/green.log。永久check_target_shipment_lines.py同次登记ops/check_suites.txt；业务配置导入前强制本机命名隔离PG，关闭消息/调度，只造随机合成账号/订单/实发事实与空历史期间，不清库。三组为同价同量单批20、不同价格单批21、相同价格分两批20，分别验证self范围个人/全公司实时及冻结汇总、批次下钻、SQL存档汇总/明细一致；永久测试认证为合成CurrentUser覆盖、数据由ORM建立，不冒称其本身也是正式登录/建单流程。
- 永久PG红例通过独立进程只加载git 8788a89的targets_with_actuals函数AST，未覆盖工作区，其第一例明确报AssertionError expected20/actual10（permanent-red.log）；当前实现三例2018-01至03通过（permanent-green.log）。旧码红例停在实时汇总断言，未再结出错误冻结数据；历史正式红例保持可复查。
- 完整后端1559 passed in15.09s、进程exit0（full.log），新脚本/targets.py pyflakes及diff通过；新PG套件单独运行不冒充新增pytest计数。本轮没有前端变更、远程CI、提交/推送或生产发布，不把本地通过当作已上线。
- 只读SSH实际应用8788a89，targets.py SHA256 fc954fb37807b996f739e9401b3a5b8337a6f0a783cf902e9f5add62123da007与该固定提交原文及上一轮证据一致，确认服务器含同一group_by；未读生产订单/发货数据，不声称线上已经发生具体漏算。证据ops/backups/target-shipped-lines-20261030/含正式API红绿、永久PG旧码红例/当前三组绿例、全后端回归、服务器只读哈希。测试完正常停止专用PG。
- 当前Q-081/082/083均保留本地未发布，历史存档不自动改写；Q-069/Q-075仍待原具体方案决定。发货大额float、半分多行逐行/逐批舍入仍需单独证据与口径核对，本项只修错误去重，不宣称整个发货金额精度已完成。后续可调查这些边界或转其它未充分覆盖业务链路，长期目标保持未完成。


## 2026-10-30 长期目标恢复，Q-082回款结账精度复现并本地修复（未部署）

- 上轮Q-081完成本地验收为实质进展；本轮用户恢复长期目标，重新读取目标原文、AGENTS/质量目标/进度/Git后，选择相邻但此前仅静态怀疑的回款结账链路。保留Q-081全部有效改动，不将恢复目标当成生产发布许可。
- Q-082/P2：仅本机55441/crm_iso_pdf_20261030_test真实PG及ASGI，随机合成账号/客户/CNY订单与已确认回款；合成原回款99999999999999.99。2010-09实时received_actual、考核assess_actual和received下钻均变.98；正式freeze后独立SQL确认received汇总与明细永久存.98，源payment仍.99。合成事实由ORM建立，认证为测试进程CurrentUser覆盖；不冒称正式登记/财务确认全流程或生产角色浏览器测试。证据ops/backups/target-received-precision-20261030/probe.py及red.log。
- 根因targets.received_actual及company_received_actual将PG SUM提前float、received明细同样float，router再Decimal(str(...))无法恢复；冻结读取也把已存Decimal再次float。最小修复沿Q-081现有精确传递方式：个人/公司回款桶与明细保持Decimal，统一返回received_actual_exact，冻结收集优先该字段，已冻结回款读取保留原存值。确认时间归月、已确认/取消过滤、归属与范围、锁/事务/数据库结构和历史数据均不变；考核差额/比例及前端整元展示不调整。不增加查询、依赖或业务能力，属于唯一明确的金额精度修复。
- 复用已登记check_target_signed_precision.py扩展签单/回款两指标×大额/普通四场景，避免另立重复套件；真实ASGI个人/公司实时与冻结汇总、实时/存档下钻、SQL源事实和落库明细逐一核对，旧JSON数字字段保持兼容。修前扩展脚本在缺received_actual_exact处失败（permanent-red.log），真正丢分的持久化红例是上述独立probe，不拿缺字段断言代替金额错误证明。修后首次2011-01至04通过，补实时下钻后最终2011-05至08全部通过（final-green.log），普通1.01+2.02=3.03。
- 完整后端pytest进程exit0，1559项通过（full.log；双quiet配置省略统计行，逐项通过输出1559个点），应用三文件/永久脚本pyflakes和diff检查通过。PG接口回归单独执行不混入pytest计数；无前端代码改动，未重复前端构建或伪称远程CI通过。
- 只读SSH当前实际部署8788a89，targets.py SHA256 fc954fb37807b996f739e9401b3a5b8337a6f0a783cf902e9f5add62123da007、router.py abad460acd5e2d754431886c11e16e2d34120a54e767ee9590a082e0252cda67，与git show 8788a89逐一匹配，确认服务器含同一回款转换路径（server-readonly.txt）。未查生产回款或存档，不能声称真实线上历史已丢分。
- Q-081与Q-082均本地完成验收，尚未提交/推送/部署；原2010-09错误合成存档保留，不自动重算历史。专用隔离PG测试结束后正常停止。Q-069/Q-075原待决策保持；长期目标未完成。下一步可以独立调查发货/复购金额，但必须尊重逐行舍入与既有统计规则，不能机械全局替换float；后续若发布需本批明确授权，不沿用Q-080单项许可。


## 2026-10-30 用户要求做完当前项，Q-081签单结账精度修复与验证完成（本地未提交/部署）

- 用户在暂停后明确“做完这个”，本轮仅完成当前Q-081，不扩展其它审查或沿用Q-080的单项上线许可。已读取质量目标/暂停记录/Git，确认待验证三文件补丁未被其它工作覆盖，复核函数返回类型注解后继续验证。
- 最终最小实现analytics/targets.py签单个人/全公司桶和signed下钻保留PG Decimal，冻结收集前不再转float；有目标行、补零行和已冻结读取统一提供sales_actual_exact，明细/合计补精确字符串。router收集优先精确签单值，target_actuals读取存档补原登记金额精确值；旧JSON数字字段兼容保留。SQL范围、订单取消/归属/统计口径、事务锁、数据库结构、金额上限和前端整数元展示均不变，无历史清洗或新依赖。
- 新增永久backend/scripts/check_target_signed_precision.py并同次登记ops/check_suites.txt：导入业务配置前强制本机一次性PG防呆、关闭推送/调度；用随机合成账号和空历史期间构造大额99999999999999.99及普通1.01+2.02，通过真实ASGI结账、独立SQL核对个人/公司汇总与signed明细、本人范围下实时/冻结读取及精确合计，兼容旧JSON数字类型。认证为该集成进程合成CurrentUser覆盖，明确不冒称真实角色登录；不清库、不覆盖既有期间，合成记录留存可复核。最终2010-07/08两组通过。
- 永久脚本的红例验证：独立进程加载git 8788a89中的targets_with_actuals/drilldown/_row函数AST（不覆盖工作区），同脚本于2010-03真实存出.98、对.99断言失败；当前实现通过。首次旧代码加载器未加scripts路径导致_test_support导入失败，修正后才得到有效红例，原输出red-loader-initial.log保留。扩展本人范围对照时曾因合成读者缺已有customer:view而403，仅补齐测试账号本来所需权限，不改应用门禁；该初次失败permanent-final-green.log保留，不因文件名带green宣称通过。
- 正式登录/建订单/结账HTTP补验：隔离订单24原金额.99，2025-08新存档汇总和signed明细均.99、原订单不变；2025-07原错误存档仍.98，证明不偷偷重写历史。脚本/输出http-green.log沿用同一专用库55441/crm_iso_pdf_20261030_test，与本轮永久集成数据均完全隔离于生产。正常完成后专用PG已停止。
- 永久脚本安全护栏另测无DATABASE_URL、本机正式库名、远程伪装测试库三个负向输入，均在业务模块/数据库连接前明确拒绝；仅支持本机PostgreSQL隔离目标，guard-check.log保留。
- 完整后端pytest1559项通过（15.16秒，进程exit0），三个应用文件/永久脚本pyflakes及diff检查通过；永久PG脚本另行运行通过，不混计为新增pytest项。脚本已登记但本批尚未提交，也未跑远程CI，不能写成远程流水线已通过。前端源码/布局未变，无理由重复前端构建或已有整套浏览器。
- 本轮只读SSH实际部署8788a89，analytics/targets.py SHA256 fc954fb37807b996f739e9401b3a5b8337a6f0a783cf902e9f5add62123da007、target_actuals.py 5454a21e42b0ee5a12da9f96e9811425e5e7516dd21fb41b367cfbf68c83301b、router.py abad460acd5e2d754431886c11e16e2d34120a54e767ee9590a082e0252cda67，逐个与git show 8788a89原文匹配，确认同一早转float路径存在。未查询生产存档，不声称线上已发生错误历史数据。
- 证据ops/backups/target-freeze-precision-20261030/含正式API红绿、永久PG旧代码红例/最终绿例、完整pytest、服务器哈希；本地工作区保留三应用文件、永久脚本/登记及质量记录。未提交、推送、部署、重启生产或改正式配置。Q-081本地修复验收完成；若要上线仍需本项明确发布授权，不能把“做完”自动扩大成部署生产。
- 边界：只确认/修复签单结账链路；发货/回款/复购上游金额转换仍是待调查，不宣称全系统金额路径完成。既有历史错误金额需沿现有明确重算/原因流程另行决定，本轮不处理。用户当前项已完成，未开始新的审查；长期目标未宣称完成，Q-069/Q-075仍待原具体选择。


## 2026-10-30 用户暂停长期目标，Q-081签单结账精度已复现、修改尚未验证

- 用户通过目标状态明确暂停，停止继续审查/实施。本轮已恢复长期目标/质量文件/Git，接续业绩结账真实验证；当前服务器应用仍最近已验证发布8788a89，本轮未部署、提交或推送。
- Q-081/P2仅本机55441/crm_iso_pdf_20261030_test：正式API建CNY订单15，真实PG原金额99999999999999.99；仅把该隔离订单建档时间设为历史2025-07，正式POST sales-targets/actuals/freeze后，独立SQL签单汇总sales与明细signed均存99999999999999.98。正常读取不修复它，原订单金额不变，证明是存档事实丢分而非纯展示。初次2025-06探针已成功存出同样错误汇总，但误把明细指标查成sales而非signed导致脚本断言失败；首轮输出保留，改正查询指标、换空历史期间后完整红例成功，不把探针错误归因应用。
- 根因已核对：targets.sales_actual/公司桶先把PG SUM转float；签单drilldown也float(amount)，freeze收集再Decimal(str(...))无法恢复精度。已应用但尚未验证的工作区修改仅analytics/targets.py、target_actuals.py、router.py：签单桶/明细保留Decimal，提供签单精确字段及精确明细/合计；冻结收集优先精确值。尚未运行本批修后回归或永久新用例，不能记为已修复验收通过，不得直接提交/部署。
- 修改不含发货/回款/复购上游float路径的全面修复；那些仍仅静态线索。前端金额原本按整数元展示，本轮未改变该UX或擅自新增分位展示；历史错误存档没有被清洗或重写。也未变更锁、事务、迁移或正式配置。
- 证据ops/backups/target-freeze-precision-20261030/probe.py、http-first.log及http-red.log；该隔离库2025-06/07错误存档保留，可用于恢复时证明历史不会被偷偷改写。暂停收尾仅记录当前事实并正常停止专用PG，没有继续审查。工作区三份应用补丁及本记录均未提交，线上不受影响。
- 恢复后第一步：重读本节/Git确认未被其它会话改动；补真实PG永久签单结账回归（普通/大额、明细与汇总、存档读取、历史原件保留），跑红绿及相关/完整测试，检查函数返回类型注解；必要时修正未验证补丁，不把暂停时的实现当最终方案。生产同类代码尚未就本项SSH核对，不能宣称已确认服务器包含Q-081。Q-069/Q-075仍待原选择，Q-080已上线。长期目标仅暂停，未完成。


## 2026-10-30 Q-080 用户单独批准后发布完成（8788a89）

- 用户对本项上线询问明确选择“部署这项修复（推荐）”，按就绪固定应用提交8788a89a323e26261199a00fe9e2546766acb6e9执行，本次生产权限已明确，不扩展后续新问题。接到答复前本轮仅开始analytics.targets/结账收集调用链的静态阅读，未创建结账数据或修改应用，不记该方向已确认Bug，随后优先完成本项发布。
- 再次核对本地工作区应用无未提交变化、发布包SHA256 03b89327906e5b689e4ad7bc6cca86b9c61c3e95c6aa1413e5dbc2f8e8c59ad9及同SHA CI 41032570633成功。1559项本地回归/真实PG金额红绿/实际API摘要浏览器回放证据仍有效；前端web哈希与上一版完全一致。
- 正式统一发布--apply先重跑完整预检，随后备份、短暂重启、切换与公网/全文件核验，返回status=verified；回滚备份/opt/crm-sales-agent/backup/unified-8788a89a323e-uhtp2556保留。未迁移数据库、修改requirements/正式配置、重写历史金额或生产业务数据，也没有调用真实模型或发送业务通知做测试。
- 独立发布后核验进程exit0：前后端标记均8788a89，RELEASE.json verified，全部491个发布文件重算SHA256一致，service active，8010/health为up/database ok，钉钉OA stream_connected true。前端入口仍CustomerDetailPage-BedCw4t5.js，符合本次无前端代码变化；新金额字段/Decimal摘要由已验证后端包提供，不靠变更浏览器缓存伪装修复。
- 应用提交8788a89此前已同步GitHub/Gitee；本次只更新QUALITY_GOAL/本条发布记录，再同步两个既有仓库。文档提交不代表又改变运行应用版本，工作区应用与部署版本保持一致。证据ops/backups/customer-summary-precision-20261030/deploy.log、postcheck.log及同目录前置验证完整保留。
- Q-080发布任务完成，原等待记录作为历史保留；Q-069/Q-075仍待原具体方案决定。业绩结账金额审查仅静态线索（targets/target_bases早转float、freeze再Decimal），尚未真实复现存档误差，不提前确认或改动。下一步可在隔离库构造历史期间/正式结账与独立SQL对照，长期质量目标保持未完成。


## 2026-10-30 Q-080 发布准备全部通过，等待本项当次上线决定

- Q-080固定应用提交8788a89a323e26261199a00fe9e2546766acb6e9已同步GitHub/Gitee，工作区应用源码无其他未提交变化；后端1559项、真实PG/正式API红绿和实际API摘要浏览器回放均通过。GitHub同SHA CI 41032570633两项job均success（后端6m7s/UI4m27s），完整接口套件及前端构建/冒烟通过，最终run/jobs记录已保存。
- 固定提交发布包SHA256 03b89327906e5b689e4ad7bc6cca86b9c61c3e95c6aa1413e5dbc2f8e8c59ad9，491文件；前端web全量哈希与当前已发布b82d075包完全相同，后端仅两文件改变。新包的客户总结浏览器回放也通过，不包含新前端交互或统计口径。发布工具源代码与上一轮同SHA，沿用已通过21项安全自测。
- 服务器仅上传临时包/工具并运行默认预检，返回preflight_only；全量旧后端app、依赖/迁移守卫和运行健康校验通过，真实运行仍b82d075。未执行--apply、未停止/重启服务、未改运行代码/版本记录/生产业务数据或正式配置。本次确认为“准备完成”，不是“上线完成”。
- 授权边界纠正：上一批b82d075的条件发布许可已执行完；用户长期原文禁止一次批准自动扩展到后续问题，本项为之后发现，故在实现、CI、包及预检全部完成后单独请求当次上线。此前拟直接沿用许可的说法已向用户纠正。后续必须收到本项明确批准才能执行apply，不把自动goal继续或沉默当授权。
- 证据ops/backups/customer-summary-precision-20261030/含完整代码证据/回归/CI及本次预检。隔离PG停止，diff通过；源码和本条记录同步现有两仓库，不改变服务器应用。Q-069/Q-075具体方案仍待原选择，未实施。下一步收到Q-080上线决定后执行已就绪固定包并核验，未批准期间可转其它独立审查，长期目标保持未完成。


## 2026-10-30 Q-080 客户概览/确定性摘要金额大额浮点丢分（服务器确认，本地修复，发布准备后待本项上线授权）

- 上轮b82d075批次发布与线上核验为实质进展。本轮恢复长期目标、质量文件/Git，工作区起点干净，应用基线b82d075、HEAD b04169a仅多发布文档；选择之前只静态阅读过的customer_overview→agent.customer_summary独立调用链。不把Q-079客户报价表的修复泛化成客户统计已正确。
- Q-080/P2真实ASGI/鉴权/正式API/PG：仅本机55441/crm_iso_pdf_20261030_test，禁用本进程模型叙述，不调用外部模型；正常创建CNY客户/订单。独立SQL SUM为99999999999999.99，GET customer overview数字字段及POST agent/customer-summary的确定性“累计成交”均成.98；两笔合法订单SQL合计199999999999999.98，摘要成.97。普通1.01+2.02为3.03对照正常。客户8/9/10红例，客户11/12/13绿例，完整脚本及HTTP输出留存。
- 根因customer.service聚合SUM已是精确Decimal，但转换float才交给摘要，insights又按该float格式化；recent_orders同样只返回float。修复仅两文件：保留旧数字键，新增amount_exact/total_amount_exact字符串，确定性摘要两处格式化优先Decimal精确字段，旧响应缺字段回退原金额。SQL查询/范围/权限/订单状态/统计币种和舍入规则不变，不清历史数据、不增加查询/依赖/数据库结构，属现有精度承诺补齐的唯一最小修复。
- 新增8项永久test_customer_summary_precision：真实内存SQL验证金额范围、无order:view不查/不返订单金额、all范围及大额最近订单；摘要精确字段优先、普通/零金额及旧响应回退。修前首轮6项中4失败/2普通对照通过，修后相关Agent/权限47项通过，扩充兼容对照后完整后端1559项通过（15.62秒，exit0）；pyflakes、Agent中文标签检查及diff通过。SQLite大额SUM自身用二进制，不拿它当PG精度证明；真正大额聚合由上述PG/HTTP验证。
- 真实接口绿例普通3.03、单大额.99、两笔合计.98均精确，最近订单精确字段与独立SQL逐条一致，旧数字字段仍按原契约返回。将客户12真实API摘要JSON接入当前已发布b82d075前端构建包的隔离浏览器：点击“客户总结”实际请求customer_id12，页面显示累计成交99999999999999.99 CNY，不显示amount_exact/未命名字段，证明现有文本消费者无需前端修改。该浏览器API回放为替身，不声称同时直连PG或使用生产账号。
- 只读SSH当前b82d075，customer/service.py SHA256 16753ae40ddd22ad75b5e8c8284415bda2c8c73f9e678f8725dafc4318742373、agent/insights.py 92189b4e0abbc92b424075c6d70f10ceffa8c4e820cd1d069cc55a45fc38cbfa，均与git show b82d075原文一致，确认服务器有同一浮点路径；未查询生产客户或订单。起初拟将用户最近的条件发布许可延续至本项；复核长期目标原文“一次批准也不能自动扩大为对其他问题、其他操作或后续类似改动的永久授权”，该许可已用于上一批b82d075，本项为之后新发现的问题，应在准备完成后单独请求上线。已向用户纠正授权理解，未执行--apply。
- 证据ops/backups/customer-summary-precision-20261030/含真实HTTP红绿、永久回归、真实API摘要/浏览器及服务器哈希。专用PG已停止；本项随后固定为8788a89a323e26261199a00fe9e2546766acb6e9并同步现有两仓库，固定包构建/预检/包前端回放通过；完整CI尚在运行，未部署。本地实现可审阅不等于生产上线获准。Q-069/Q-075仍待原具体方案选择，未实施。
- 范围/下一步：本项仅客户订单汇总/最近订单及确定性摘要精度，不保证可选外部大模型文本精度，也不宣称所有分析/目标/冻结统计或商机预计金额已修复；没有新增外币展示/换汇能力。继续发布前检查并保留已有业务统计口径，长期目标未完成。


## 2026-10-30 本批质量修复已部署并核验（b82d075）

- 用户当次授权“确定服务器代码也有同样问题的可以修复部署”已落实。本轮重新逐文件核对服务器确含同一问题，发布固定提交b82d0756f066031cbc4ca802540f17eaa02b1d49；当前本地应用源码、两个仓库该应用提交、服务器后端及前端均对应此应用版本。之后若仅提交本条发布记录，不代表线上又运行了另一份应用代码。
- 已发布范围Q-064至Q-068企微同步外部失败留痕/异常响应/令牌处理，Q-070/Q-071交接中断恢复及CRM-only重试，Q-072/Q-073报价PDF精度及已批准的长数字字号，Q-074报价Excel精度，Q-076归档当前权限/归属，Q-077双登记校验，Q-078上传取消安全清理，Q-079客户详情报价精度。Q-069/Q-075仍待原方案选择，未擅自纳入；普通文件共享/个人上传规则及历史文件保持既有口径。
- 发布前后端1551项再次通过（19.95秒），发布工具21项自测、app pyflakes通过；固定提交git archive＋冻结pnpm锁构建成功，实际包浏览器完整confirmation_browser_smoke进程exit0。GitHub CI 41030239345（https://github.com/ytsbx/crm-sales-agent/actions/runs/41030239345）同SHA两项job均completed/success，后端包含空隔离PG迁移/种子/1551单元/全部登记接口套件，前端含构建/逻辑静态检查/完整浏览器及实际UI冒烟。未用没有status条目冒称CI成功，已保存最终run/jobs JSON。
- 额外全量ops静态工具15通过/1历史失败的限制保持：check_quote_todo_source未维护成本待办来源/动作判据，已用服务器实际旧基线原文重现，无相关产品源码改动，不删检查/放宽断言。该工具未在当前CI登记，本次CI完整通过不等于该额外工具被修复；不将所有静态检查写成全绿。
- 发布包SHA256 a14569260b0f62d9a1ce6c04deee50f0f92e11f3b8cf84eb24e7477e6daeabea，上传后包与release.py哈希均核对。统一预检通过全量旧app源码、依赖/迁移/配置、迁移head及运行位置守卫；正式--apply备份后切换，返回status=verified。备份/opt/crm-sales-agent/backup/unified-b82d0756f066-pjoakz1u保留。
- 独立发布后核验：前后端标记均b82d075，RELEASE.json verified；逐一重算全部491文件SHA256匹配，service active，直接8010/health为up/database ok，钉钉OA stream_connected true。当前首页入口引用CustomerDetailPage-BedCw4t5.js，确认current_version_amount_exact存在；统一发布工具另已核验公网首页及其直接入口资源。无需生产客户/订单写操作或真实第三方故障注入。
- 独立后检脚本首次误用/api/v1/health而404，应用健康路由实际为/health（与发布工具常量一致）；校正只读探针后完整核验通过，首轮输出postcheck-initial.log保留，没有把探针路径错误当应用故障或为此回滚生产。
- GitHub/Gitee pending-acceptance已同步应用提交b82d075，服务器发布前后均验证；最终只补质量目标/本条发布记录并同步两个现有仓库，不再次改应用。证据ops/backups/release-quality-20261030/含源文件对照、失败历史工具基线对照、完整测试/CI、固定包、上传/预检/正式发布/独立后检及远端SHA。
- 残余范围：Q-078客户端约5秒取消保护不保证服务器端慢SQL同时终止，真实PG30秒注入延后结束的事实已记录；未修改全局SQL超时策略。未补Q-075通用归档下载完整性策略、未重写历史金额/原件，未穷尽真实生产角色、第三方故障或全部性能/锁图。用户授权的本批发布已完成，长期质量目标未宣称完成；后续可继续独立客户概览/分析金额消费者验证。


## 2026-10-30 当前质量修复批次获授权，服务器逐项核对后准备发布

- 用户明确“确定服务器代码也有同样问题的可以修复部署”，本次授权覆盖已确认且已验证的现有修复，取代对应项此前先本地/不部署限制。沿用用户已批准同步现有GitHub/Gitee仓库的授权，不扩大未决业务选择。
- 发布范围Q-064至Q-068企微失败留痕/异常响应/令牌、Q-070/Q-071交接底账与CRM-only重试、Q-072/Q-073报价PDF精度与长数字字号、Q-074报价Excel精度、Q-076归档当前归属可见性、Q-077双登记校验、Q-078上传取消补偿、Q-079客户报价子表精度。Q-069/Q-075未实施，不纳入发布；不清理历史数据、不更改数据库结构/依赖/正式配置。
- 本次重新只读SSH核对前后端标记均46be2876d374473bd559f995de964af6bcf575b0；9个修改后端文件逐一SHA256与git show 46be287原文一致。当前入口引用的WeComPage/CustomerDetailPage资源另存证据，客户报价render未取exact，与此前复现相同。未用残留frontend/src当运行基线，统一发布预检还会核对全部后端app及迁移/requirements守卫，拒绝覆盖未知热修。
- 发布检查：后端1551项再次通过（19.95秒）、统一发布工具21项自测通过、app pyflakes通过。额外运行全部16个ops静态工具，15项通过；未在当前CI登记的check_quote_todo_source有两条既有失败（cost_incomplete来源登记缺项、把锚点提示当作按钮文案检查）。抽取实际部署46be287的三份原文件到独立临时目录后原样复现相同失败，相关产品页面/待办源码本批均未改；不删断言/放宽检查来制造全绿，不把它算本次回归。当前CI仍须针对发布提交完整通过，历史静态工具维护问题保留。
- 证据ops/backups/release-quality-20261030/；本节为发布准备，不等于发布完成。当前重新运行后端单元/发布工具自测、静态检查，后续以固定提交构建、同SHA CI及正式统一发布结果记录，不将尚未执行事项写为通过。


## 2026-10-30 Q-079 客户详情报价表遗漏已有精确金额字段（服务器当前页面确认，本地修复）

- 上轮Q-078获批修复、取消/超时/真实SQL错误及1551项为实质进展。本轮重读长期目标、质量文件/Git，恢复独立客户金额消费者审查。Q-051报价主列表已修，但客户详情quotes标签页仍formatMoney(v, currency)，未读取同一Quote类型已有current_version_amount_exact，是该批遗漏消费者的新调用链；不把已修主列表重复报为新Bug。
- Q-079/P2真实隔离浏览器复现：当前前端先完整build，独立profile/本机静态HTTP/内存API同时给current_version_amount=99999999999999.98及exact='99999999999999.99'。访问/customers/9?tab=orders，订单行精确.99对照通过；访问同客户quotes页，实际DOM报价行EXACT-QUOTE-121显示.98，期望.99断言失败。用户可见同一报价在报价主列表与客户报价标签不一致，不涉及数据库金额被改写。
- 实际ASGI/鉴权/PG补证：仅本机55441/crm_iso_pdf_20261030_test，正常登录，从隔离报价1读取真实customer_id，再GET quotes?customer_id=该ID&page_size=50，既有数字字段.98、精确字段.99，证明客户过滤的真实接口已提供正确值。浏览器为API替身，PG为独立真实读取验证，不声称浏览器同时直连PG或使用生产业务账号。
- 首次真实API探针误假设报价1归属客户1，返回列表未命中、StopIteration而退出；没有应用异常或业务写入。改为从该隔离报价读取真实customer_id后成功，上述最终HTTP证据见api-green.log，初次输出api-result.log保留，不用错误过滤条件制造应用缺陷。
- 唯一最小修复CustomerDetailPage.tsx该金额单元格：formatMoney(record.current_version_amount_exact ?? v, record.currency)，与报价主列表已有实现同源；无字段/接口/权限/统计/舍入/币种/布局变化，旧响应无exact继续原数值回退。普通订单标签既有精确处理不改。此为读取已有正确金额的纯精度补齐，无新业务/UX取舍，不重复索要批准。
- 永久ops/confirmation_browser_smoke在既有精确报价场景增加客户quotes标签导航和DOM金额断言；新定向浏览器绿例客户订单对照保持正常，报价行显示99,999,999,999,999.99。TypeScript及生产build通过，既有大chunk/lottie等警告保留，不擅自重构；完整confirmation_browser_smoke进程exit0，新增客户报价精度、原主列表/版本对比/转单/订单分期及其余既有场景均通过。后端未改，不重复未变应用1551项回归。
- 只读SSH当前应用46be2876d374473bd559f995de964af6bcf575b0；先glob看到多个历史CustomerDetail资源，不能凭历史文件认定当前运行。进一步从当前web-dist/index.html解析入口index-CDG1mhxe.js，确认其直接引用CustomerDetailPage-BgcMbNBX.js（SHA256 54eaedbccd2eaa66e785e24d4be1671d1f116b5f943b5313a61b519bcd0186f7），对应render:(e,t)=>g(e,t.currency)，未读exact，确认当前生产页面含同一遗漏。未读取生产客户或修改服务。
- 证据ops/backups/customer-quote-precision-20261030/含构建前后、定向浏览器红绿/完整回归、隔离真实API脚本/结果及当前入口引用证据。diff通过，专用PG已停止，无生产写入/外部发送、提交推送部署；与本批其它补丁一起保留本地。
- 范围/下一步：customer/service.customer_overview的聚合金额与Agent确定性摘要另有float格式化路径，但客户详情页面并不调用该概览API，不能混为本次DOM问题或直接认定其展示已修；尚未对其建真实复现。本次验证客户报价标签页，不扩张为所有分析/目标/冻结汇总精度完成。Q-075/Q-069仍待原选择，长期目标未完成。


## 2026-10-30 Q-078 用户批准取消与超时方案，共用清理已本地修复（未部署）

- 上轮Q-078两个真实上传入口取消复现及提交成功对照为实质进展。本轮先恢复长期目标、质量文件/Git，并开始独立金额汇总调用链阅读；随后用户明确选择“修复并验证取消和超时”，优先完成该批准事项。金额汇总本轮只静态阅读，尚无新复现，不记已确认Bug。Q-078决定同步QUALITY_GOAL，不扩展Q-075/Q-069未答复选择。
- 最小实现仅file/service.discard_unregistered_upload：使用项目已有AnyIO，在5秒move_on_after(shield=True)内完成原事务rollback、按本次object_key查询登记、回滚该只读查询事务归还连接，最后仅在确认无登记时精确删除。原事务不提交，已提交文件保留；查询失败沿用日志，超时新增明确日志并保留待核对文件；两路由继续原CancelledError/异常传播，无新表、锁、依赖或外部请求。
- 新增test_upload_cleanup_cancellation五项有真实异步检查点的永久回归：持续取消时清无登记文件/保已登记文件、rollback挂起和query挂起的有界超时、查询异常；无关原件不变，原取消继续传播。用git HEAD函数AST加载至独立测试进程（未覆盖工作区）重跑，4失败/1保留原件对照通过；当前实现与存储边界共14项通过。完整后端最终1551项通过（16.39秒，exit0），pyflakes/diff通过。
- 实际ASGI/鉴权/正式multipart/PG绿例：本机55441/crm_iso_pdf_20261030_test，通用上传与正式订单/应收/待确认回款的凭证上传分别在已写盘/已flush审计阶段持续取消；补偿均entered→finished，数据库文件增量0/磁盘增量0，回款无凭证关联，其他文件相对路径/SHA映射不变。真实commit成功后取消的对照中仅1条已登记原件保留，正式download内容/校验正确，正常DELETE测试文件后回到基线。三个正常取消路径无此前连接终止/GC警告。
- 实际5秒超时：在补偿登记查询前，用同一AsyncSession真实执行SELECT pg_sleep(30)，其余处理不替换；调用方约5.12秒返回原取消，补偿超时日志留存，文件表回滚、未核实文件保留，最终仅由测试脚本独立确认无登记后精确移除，其他原件不变。AnyIO期限约束异步等待，不是同步磁盘系统调用的硬中断保证；记录实际耗时，不伪称墙钟精确5.000秒。
- 超时残余证据：附加“期限后50ms数据库query应消失”的强断言失败（timeout-pool-green.log保留，尽管文件名含green不表示通过），连接池checkedout已0但PG的pg_sleep仍active；驱动终止时有CancelledError日志。随后独立观察同一30秒测试query在调用方期限后约25.08秒自然结束，客户端连接池checkedout始终0，未见无限残留；不将客户端等待上限包装成服务器端SQL硬超时，不隐藏该边界、不擅自新增statement_timeout或改第三方驱动。
- 证据ops/backups/upload-cancel-fix-20261030/含旧函数红例、五项/完整回归、通用/凭证/已提交对照、真实PG查询超时及后续观察脚本输出；Q-078上一节只读服务器同SHA证据保持，本轮未写服务器。尚未提交、推送或部署，仍按用户“先本地验证、不部署”批准范围。
- 最终补验：旧有普通审计失败/真实提交后模拟回执丢失的正式凭证HTTP脚本切到本轮独立PG重新运行，回滚清理、已提交原件保留、下载/拒绝覆盖及正常删除全部通过。将只读登记查询的rollback放在finally后，真实PG SELECT 1/0错误场景也明确日志、保留未核实文件、pool checkedout0、原取消继续，测试精确清理后磁盘基线不变。最终代码完整1551项回归及pyflakes/diff通过；隔离PG已停止。
- 下一步：Q-078本地实现及取消/超时验证完成，服务端强制语句超时属于尚未实施的新策略，残余驱动终止日志和慢查询延后结束已明记，不能宣称服务器端查询5秒硬终止。后续金额汇总/客户概览仍需真实金额与前端消费者验证，不能将静态float转换直接记为Bug。长期质量目标未完成，Q-075/Q-069仍待原选择。


## 2026-10-30 Q-078 上传登记期间持续取消打断文件补偿（服务器确认，待清理时限决定）

- 上轮Q-077六场景/真实HTTP双校验修复与1546项为实质进展。本轮恢复长期目标、质量文件/Git，转查上传已经写盘后的取消补偿，新增的是持续取消作用域而非历史普通异常、单次CancelledError或multipart解析断开证据。Q-075/Q-069原待决定保持，不重复请求。
- Q-078/P2真实ASGI/鉴权/正式multipart/PG：仅本机55441/crm_iso_pdf_20261030_test及专用file_root，正常admin登录后在通用上传write_audit阶段（磁盘已写、真实files flush已完成）用AnyIO CancelScope取消当前任务，并await真实取消检查点。observe包装只记原补偿进出、仍调用原discard_unregistered_upload；记录为cleanup entered→cleanup interrupted: CancelledError。独立asyncpg确认files数量回到基线，但磁盘多1份、无登记。finally核实本次object_key无登记后仅删除该合成文件，完整目录相对路径/SHA映射恢复基线。
- 独立回款凭证入口验证：正常正式API创建CNY订单/应收/待确认回款，再在attach_voucher审计阶段同样取消；真实files记录回滚、payment.voucher_file_id仍空，磁盘多1份，补偿相同位置再次被取消打断。finally仅清本次无登记合成文件，其他文件映射不变，证明两入口同一共用补偿的缺口，不只是静态推断。
- 提交成功对照：真实PG commit完成后再触发持续取消；files只新增1条，字节/size校验一致，正式通用download200、原件内容匹配，未误删已提交原件；最后恢复所有钩子并正式DELETE测试上传，磁盘及files行数恢复基线。取消时另有SQLAlchemy连接终止/回收警告，作为本次过程证据留存，未量化持续连接泄漏，不单立已确认新Bug。
- 根因：两路由分别捕获CancelledError后await共用补偿，补偿先await rollback再await查询登记，无取消保护；持续取消会在任一await再次打断，甚至未执行精确unlink。原规则先确认未登记才删正确，应保留；不能直接删、不能把整个原事务shield提交。现有测试/历史HTTP证明的普通异常和multipart半传成功不覆盖此窗口。
- 推荐待决定：仅给既有回滚/重查/精确清理加最多5秒取消保护；成功提交文件保留，不能确认或超时只日志，不盲删，取消仍向上抛出。收益为多数取消场景补偿可完成；代价是取消后后台资源可多占用最多几秒，需验证超时、数据库异常、反复取消及已提交原件。已按AGENTS事务/性能取舍要求异步请求用户选择“修复并验证取消和超时”或“保持现状”，先本地验证、不部署。尚未答复，不提前改应用。
- 只读SSH当前部署46be2876d374473bd559f995de964af6bcf575b0，file/service.py f1726bdd6f1b1dbb0ac6d04f07a0dd2b649a937386c1fa1fe4adacf9b8c144e7、file/router.py 2f0a9174b2c64b4280badada2fa41bc2e236f9758320b2f0d901cf0cbc1812f9、payment/router.py 93f83bc2bd28ee91498fc64810610b0d7967aebf7e80bd4f84c719db80a65f8c，与本地对应未改文件完全一致，确认服务器含相同补偿实现，未查询生产凭证或写生产文件。
- 证据ops/backups/upload-cancel-cleanup-20261030/含通用/凭证两个真实ASGI取消脚本和结果、真实提交后取消对照及服务器只读哈希。仅本机测试进程内取消注入，不声称真实浏览器断网或生产网关会按同样时序取消任务；普通上传客户端断开不必然自动触发该持续取消。本轮无应用补丁，无理由重复未变全量，最近1546项见Q-077；diff通过。PG已正常停止，无生产/外部写入、提交推送部署。
- 下一步：Q-078获明确选择后补共用清理的最小实现，并验证两个入口取消前后数据库/磁盘守恒、提交回执不明不误删和清理超时边界。未批准可转向其它独立高风险链路，单项等待不代表全项目阻塞。长期目标保持未完成；Q-075/Q-069仍待既有选择。


## 2026-10-30 Q-077 归档读取漏查文件表已登记校验值（服务器确认，本地修复）

- 上轮Q-076获准修复、正式交接及1540项通过为实质进展。本轮恢复长期目标、质量文件/Git，确认无其他运行Agent；沿Q-075所记独立待调查点验证双登记值，不把Q-075通用入口性能选择或Q-069界面提示视为已批准。
- Q-077/P2触发条件：已归档原件字节仍与biz_docs.file_sha256一致，但files.checksum变为另一值。load_archived_file文档明确两处登记任一被改动应可发现，实际使用doc.file_sha256 or record.checksum只检查第一处。永久六场景回归中该场景修前失败，其余五项正常/另一路不一致/单登记值对照通过。这是异常登记一致性检验缺口，不声称正常生成会写出两种不同值。
- 真实ASGI/鉴权/PG复现：仅本机55441/crm_iso_pdf_20261030_test和专用文件根目录，选择此前正常生成隔离档案2，先核对原件SHA256与两登记值一致；只改隔离files.checksum为固定错误值，正式biz-doc download仍200。改档案登记值的对照410，单独任一有效登记值的历史兼容均200。所有登记值finally还原，磁盘字节全程未改。
- 唯一最小修复bizdoc/service.py：同一次读取/同一次hash后逐一比较所有非空已登记校验值，任一不符沿用原missing/410提示及审计，不做数据修复、静默重建、权限/事务/布局改动，不增加IO或依赖。只恢复该函数已明确的校验承诺，符合无实质取舍直接修复条件。缺单值保持既有回退；两值均空的历史行为未借机收紧。
- 修后六项回归与完整归档/文件权限专项共31项通过。真实HTTP重跑：正常200，文件登记不一致410，档案登记不一致410，只有一个有效登记值均200；每步确认登记不被自动更改、原件字节不变，最后还原两登记值，专用PG停止。完整后端1546项通过（16.34秒，进程exit0）。
- 只读SSH当前部署46be2876d374473bd559f995de964af6bcf575b0，bizdoc/service.py SHA256 5821b3a9a5193756abc6467611fc161de74b892b39866adf2cd6170d35ee6082，与本轮修前本地完全一致，确认服务器含相同漏查代码；未查询生产档案、未声称线上存在登记异常。证据ops/backups/archive-dual-checksum-20261030/含六项红例、专项/全量、真实HTTP红绿及只读服务器哈希。
- 应用文件pyflakes通过；原测试文件pyflakes报告一处既有带noqa的storage导入（HEAD原文亦有，本轮无新增该警告），不为消除工具提示擅自删原夹具加载。diff通过，无生产写入/配置/服务改动、外部发送、提交推送部署。
- 边界/下一步：不自动清理历史不一致登记，不代表通用files入口已做完整性检查；Q-075仍待既有性能选择。系统生成路径正常同步登记两值，未找到正常业务操作产生差异的证据，不扩张为已发生的数据破坏。可以转查其它独立高风险链路；长期目标未完成，当前补丁均本地待发布。


## 2026-10-30 Q-076 获准按现有权限统一归档原件访问（本地修复，未部署）

- 用户解释后明确答复“可以，不用把权限卡死”，按Q-076原提案实施，不扩大到额外维护权限、审批或角色调整。已同步QUALITY_GOAL；下方待决定记录为历史。Q-075完整性校验和Q-069界面提示仍未获决定，未实施。
- 最小修复file/access.can_access_file：保留任一显式可见BusinessFile优先放行；否则反查BizDoc.file_id，仅取类型及owner两列，复用has_doc_permission/scoped_owner_ids，与档案现有类型权限/归属一致。有关联但无权不再退回上传人放行；真正无业务/档案关联的上传仍按上传者原规则。部门、含下级及all范围保留，不要求全部关联都可见，不增加权限码、表结构、事务锁或文件读取/校验。
- 永久test_archive_file_access新增10项真实内存SQL回归，涵盖报价/下单/打样三种类型交接前后、查看权撤销、all和无归属、显式客户共享、部门/下级及孤立文件/不存在文件。修前4项失败；修后与既有文件授权共26项通过。全量首次1538通过/2失败，原因旧隔离夹具未建新增只读依赖biz_docs表；共用内存建表补BizDocTemplate/BizDoc后1540通过，无删测试/改业务断言。
- 真实ASGI/鉴权/正式API/PG验证：仅本机55441/crm_iso_pdf_20261030_test及专用文件目录，独立新建两个业务员、客户6/报价6/档案6/原件6，正常审批生成后正式客户改派。原JWT旧负责人对档案下载/通用元信息/通用下载均403，新负责人均200，成功下载SHA256与交接前原字节一致。新负责人DELETE原件422，文件登记仍在，原件保护未绕过。测试库正常停止，无真实外部调用或生产写入。
- can_access_file消费者已核对：通用元信息/下载/预览、改名/删除、挂载授权及合同/产品关联入口；本次只补已归档文件关联可见性，原件维护保护仍由inspect_file_usage处理。xlsx预览原本不支持，本轮未冒称浏览器预览已验收；显式共享可见是保留的业务口径，不视为一律撤销所有共享。
- 最后仅取档案类型/owner两列以避免加载快照后，再跑完整后端1540项通过（16.78秒、exit0），新建客户7/报价7/档案7再次完整正式HTTP验证通过，最终代码与结果一致。专用PG已再次停止。
- 证据ops/backups/archive-owner-fix-20261030/含失败用例输出、26项定向结果、完整回归日志和真实HTTP脚本/结果；pyflakes/diff通过。服务器同一问题代码确认沿用上一轮只读SHA证据，本轮未再登录服务器。未提交、推送或部署；仍遵守本项先本地验证、不部署范围。
- 残余风险：未穷尽下载进行中同时交接的时序，已发送字节不可收回；本次每次判权可能增加一次档案关联查询，未做生产大数据性能基准，不新增索引。Q-075/Q-069仍待原选择；本项修复完成后可继续其它独立审查，长期目标未宣称完成。


## 2026-10-30 Q-076 归档原件通用文件入口沿用上传人导致交接后范围不一致（服务器确认，待权限决定）

- 上轮Q-075跨入口完整性真实复现为实质进展。本轮恢复长期目标、质量目标/进度/Git，Q-075性能选择尚未批准，未擅自实施，选择独立“生成归档原件交接后的权限”链路。此前普通业务附件改派/逐项交接证据不覆盖无BusinessFile挂载的BizDoc.file_id原件。
- Q-076/P1实际ASGI鉴权/正式API/PG：仅本机55441/crm_iso_pdf_20261030_test，新建两个self范围salesperson，旧负责人正常建客户5、商机、CNY报价5（版本5），录入完整SKU/明确0运费、提交并批准，再正式生成报价档案5及文件5。SQL确认该file无BusinessFile行，files.uploaded_by=旧负责人；客户/报价/档案按正常customer transfer转给新人后，独立SQL确认quote与BizDoc.owner_id均已变新。
- 两账号沿用原JWT，旧负责人GET档案download403，但GET files元信息200、通用download200且SHA256和原件完全相同；新负责人则档案download200、通用元信息403/download403。证明旧上传人能绕过档案当前范围读取原件，新接手人的通用入口反被拒。不是猜ID即全员可见或所有附件都泄露；仅确认有原件ID且是历史上传者的这条链路。
- 根因file.access.can_access_file只反查BusinessFile，无此挂载就视为孤立个人上传，只按uploaded_by；生成归档由_archive_original直接用BizDoc.file_id引用，不建立普通挂载。BizDoc正式下载正确按当前档案权限/owner判范围，客户单据迁移也正确更新归档owner，漏在通用文件可见性判据。
- 建议通用入口对系统生成的归档原件按档案既有类型查看权限及当前owner范围授权，真正未关联的个人上传继续原规则；要保留文件多处显式共享的既有任一可见判据、普通附件和原件维护保护。代价为旧上传者不再保有交接后原件访问，新接手人按已有档案权可用通用入口，属于实际权限口径取舍，按AGENTS请求人工决定，不自动修改。
- 用户对建议回复“啥意思”，已解释张三客户转李四后，张三打不开报价档案却能凭旧链接下载的例子；这不是批准。当前等待是否按现有档案权限/归属统一，未应用权限变更，先本地修复验证、不部署范围保持。Q-075/Q-069也未因本项问答获得批准。
- 只读SSH当前生产46be2876d374473bd559f995de964af6bcf575b0，file/access.py e8222960dff344f27a564f53baf631c89e94ff43abd95a7e126902d56f1eedaa、bizdoc/router.py 5de14cb80a2cadb9ff3b68eb8d8d018f8e95b94566be9f3869257071db868a2b、customer/documents.py aebe5114d85ac9fa931abc61bb198339a89ea468824ad58de40390188e7f7055均与本地HEAD一致，确认同一现有判据/迁移路径。未查询生产客户或下载生产原件，不声称线上曾发生该访问。
- 证据ops/backups/archive-owner-scope-20261030/含完整合成正常API脚本、六项交接后状态/字节结果及服务器哈希。专用PG已停止，diff通过。无应用改动，不重复未变应用1530项完整回归；无生产写入、外部发送、提交推送部署。待决定Q-076/Q-075/Q-069，其余已验证补丁本地未发布。
- 下一步：用户明确选择后建立原归属/交接/撤权/多挂载共享/真正孤立文件/不同doc_type的回归，并复核列表/下载/预览调用can_access_file的消费者。未批准可继续独立调查，长期质量目标未完成，不把单项权限等待当全项目阻塞。

## 2026-10-30 Q-075 归档损坏校验可由通用文件下载入口绕过（服务器确认，待性能取舍决定）

- 上轮Q-073获批修复/真实PDF视觉及1530项为实质进展。本轮重读长期目标、质量文件/Git现状，恢复上一轮受答复打断的独立归档完整性调查。历史套件已覆盖biz-doc原件丢失/重建，本轮新增的是同一损坏原件跨通用files入口的真实HTTP对照，不重复同一套件当新Bug。
- Q-075/P2真实ASGI/PG：仅127.0.0.1:55441/crm_iso_pdf_20261030_test与其专用file_root，正常登录后选已正确生成的隔离报价档案doc2，严格核对文件路径在专用根目录及原SHA256==files.checksum==biz_docs.file_sha256。正常两个下载入口均200且字节一致；本地将该测试原件改为固定合成损坏字节后，biz-docs/2/download返回410、日志及download_blocked审计留存，而files/{id}/download返回200且内容恰为损坏字节。不是权限越界或对生产文件破坏。
- 丢失对照：仅unlink本次明确测试原件，biz-doc返回410、通用files返回404，两者均未重建或写替代件；恢复原始字节后两个入口200、字节与原始一致，原SHA256恢复。finally再次精确写回原字节，未改文件/单据业务登记；审计新增是正常测试请求留痕。原件已还原，专用PG正常停止。
- 调用链：bizdoc.service.load_archived_file读取实际字节并对登记SHA256；file.router.download_file做当前可见性、成本门禁和存在性后直接FileResponse，未反查该文件是否为受归档校验保护的BizDoc原件。只确认通用下载，xlsx预览本就不支持，不将它冒称本轮已验证的预览绕过；合同、图片、其它普通附件也未扩展为已确认。
- 待决定建议：仅对通用下载中已归档对外单据的原件补同源完整性校验，损坏时拒绝，普通附件规则不变。收益为各入口不会继续把无法证明的字节当原件返回；代价为下载增加读取/计算校验耗时，大文件需评估，具体实现还需保留现有响应/下载能力。按AGENTS性能取舍要求已异步请求“归档原件统一校验”或“保持通用下载现状”，先本地修复验证、不部署。未收到答复，未提前修改应用或自选新的性能策略。
- 只读SSH确认服务器仍46be2876d374473bd559f995de964af6bcf575b0，file/router.py SHA256 2f0a9174b2c64b4280badada2fa41bc2e236f9758320b2f0d901cf0cbc1812f9、bizdoc/service.py 5821b3a9a5193756abc6467611fc161de74b892b39866adf2cd6170d35ee6082，均与本地未改对应文件一致，确认生产包含同一路径差异，不声称线上原件实际损坏。证据ops/backups/archive-integrity-entry-20261030/含脚本、真实HTTP/警告输出与哈希。
- 本轮无新应用改动，不重复未变应用全量；最近1530项见Q-073。diff检查通过，无生产写入/配置/服务变化、真实第三方请求、提交推送部署。Q-075及Q-069待决定，其余本地已验证修复待发布，不把单项等待当全项目阻塞。
- 下一步：获批后建立针对损坏/正常/非归档/不存在/权限的红绿回归并评估大文件开销、响应契约；未批准期间可继续独立校验。另一个静态待调查点是load_archived_file注释要求两登记值任一改动可发现，而实现用doc.file_sha256 or record.checksum取一个；尚未模拟登记字段不一致，不记已确认Bug、不清洗历史记录。长期质量目标仍未完成。

## 2026-10-30 Q-073 用户选择仅长数字缩字号，修复与真实PDF验收完成（本地未部署）

- 上轮Q-074实际归档精度修复为实质进展。本轮已恢复长期指令、质量文件/Git并开始归档完整性入口核对；随后收到用户对Q-073明确选择“仅长数字缩小字号（推荐）”，优先完成获批工作。该批准范围先本地验证、不部署，不扩展Q-069提示或其它布局。选择已同步QUALITY_GOAL。
- 最小实现quote/pdf.py新增_fit_numeric_cells，用实际CN_FONT及目标字号测量字符串宽度，超过列宽减既有左右各6pt padding时，仅给该数字单元格追加更小字号；数量/单价/行金额、费用金额、汇总金额均覆盖。固定列宽、表头/普通单元格字号、对齐、文本/精度/币种及所有金额业务规则不变；不截断、换行、重算或缩小整张表，无新依赖。
- 永久test_quote_pdf_fit四场景：大额商品、费用、大数量和普通报价。用实际reportlab Table渲染后的字体/内边距测量每个数字单元格，断言文本宽<=可用宽；并核对单价/行金额文字不改、普通字号严格9/10pt不变。修前goods/quantity失败、fee/normal对照通过；修后4项全部通过，与精度/审计相关回归通过，完整后端1530项通过（进程exit0及日志1530成功标记），pyflakes/diff通过。
- 复用本机PG55441/crm_iso_pdf_20261030_test，真实登录/GET PDF重新下载同一批报价版本1-4（非新造不同编号来模糊对照）。独立pypdf核对四文件均1页，原大额合计99999999999999.99及四位单价999,999,999,999.9999完整；普通文件33333.30正确，逐段visitor_text记录的文字、文本矩阵位置与字号和修前基线完全相同。
- 按此前已读取PDF技能继续原生PDFKit渲染，四张PNG均实际查看：goods单价/行金额各自在自己的列内，数量100不再被重叠；物流/其他费用及普通报价仍正常，未发现本批样本新截断/重叠。极端数值字号变小为用户已批准的取舍，不宣称所有可能长文本或多页布局均验收。测试PDF位于ops/backups/quote-pdf-fit-20261030/，不替换生产文件。
- 证据该目录含4场景红绿/完整后端、真实HTTP下载脚本/输出、PDF和原生PNG、pdf-validation.log。专用PG已停止，无SSH、生产业务/配置/服务变化、真实外发、提交推送部署。Q-073本地已修复，与此前Q-064至Q-068/Q-070/Q-071/Q-072/Q-074一起待发布；Q-069仍待原界面选择。
- 未完成独立调查：通用files下载/预览与biz-doc原件下载的校验入口差异目前只有代码阅读，尚未构造文件损坏/丢失复现，不记已确认Bug。本轮受用户答复转向Q-073，未声称原计划完整性测试已执行。下一步可在隔离文件上继续该跨入口验证，保留旧原件、明确恢复；长期质量目标未完成。

## 2026-10-30 Q-074 对客报价Excel大额金额经float丢分并固化为归档原件（服务器确认，本地修复）

- 上轮Q-072真实PG/PDF补验与Q-073视觉列重叠证据为实质进展。本轮重读长期目标、质量文件/Git状态，保留Q-073/Q-069待界面选择，转查独立Excel归档消费者；不把直接PDF修复泛化为Excel已正确。没有擅自调整工作簿布局、数据类型或历史文件。
- Q-074/P2实际ASGI/PG复现：复用专用127.0.0.1:55441/crm_iso_pdf_20261030_test，在正常已批准报价版本1上正式POST biz-docs/quote→GET original下载。独立SQL确认input_snapshot为精确字符串99999999999999.99，但实际xlsx ZIP/XML的明细金额、小计和合计全部为99,999,999,999,999.98；第二次下载字节完全相同。不是仅页面格式问题，错误被归档原件稳定保存。来源链build_quote_doc/_jsonable保持Decimal字符串正确，误差在bizdoc/xlsx.py的_cell_text(amount)与_money两个float转换处产生。
- 唯一最小修复仅xlsx.py：两处改Decimal(str(value))直接按原千分位/两位小数格式化，_cell_text补InvalidOperation以保留原非法文本回退。单价既有format_unit_price、数值文本单元格类型、公式注入保护、模板/列宽、业务计算/舍入、权限/成本/主数据/运费校验、快照和内容哈希规则不变。无新依赖/数据库/性能查询或数据清洗，符合无实质取舍精度修复授权。
- 永久test_quote_xlsx_precision调用项目实际渲染器，再用stdlib ZIP/XML回读最终单元格文本；Decimal/字符串来源×大额/普通1.01及费用组合共6组，修前4个大额场景失败、两个普通场景通过；修后全部通过。与quote_doc_freeze/quote_cost_gate共91项通过，后端完整1526项通过（进程exit0及日志1526成功标记），应用与新测试pyflakes、diff通过。未新建手工制表实现或改变现有openpyxl导出架构。
- 真实接口修后新生成三类归档：goods doc2、logistics doc3、other doc4，input_snapshot精确值保留，xlsx实际文本完整.99，重复正式original下载字节一致。独立历史检查doc1下载内容仍为原错误.98且SHA256等于文件表原checksum，证明本次修复只影响新渲染，没有偷偷重建/覆盖历史原件。修前xlsx临时同名下载被绿例覆盖，但完整红例单元格输出、数据库doc1及原归档文件仍保留，历史复验再次确认原件。
- 只读SSH当前生产仍46be2876d374473bd559f995de964af6bcf575b0，bizdoc/xlsx.py SHA256=0f783b8b7e68921247dde0b6f16963dccd33d73fd6ff9835bf348227f910beb5，与本地HEAD修前一致，确认服务器包含同一float路径；未查询生产业务档案、未声称线上实际存在错误历史件。证据ops/backups/quote-xlsx-precision-20261030/含红绿真实接口、6组永久回归红绿/相关全量、XML文本/测试xlsx、历史checksum复验与只读哈希。
- 专用PG已停止，仅本机隔离数据，无真实第三方流量、生产写入/服务变更、提交推送部署。Q-064至Q-068/Q-070/Q-071/Q-072/Q-074本地待发布，Q-069/Q-073仍待已有选择。
- 边界/下一步：不自动修复历史归档，若实际生产发现需按原件保留/纠错版本流程决定；没有核对所有历史文件、Excel实际打印布局或所有归档PDF类型。当前Excel本就输出金额文本，不把本次描述成支持原生Excel超过15位数值精度。可继续归档下载/快照一致性或其它独立链；长期目标未完成。

## 2026-10-30 Q-072真实PG/接口/PDF补验及Q-073长数字列重叠（待排版选择）

- 上轮Q-072最小修复及1520项回归为实质进展。本轮恢复长期目标、质量文件/Git现状，补其明确未完成的独立PG、正式接口与PDF视觉证据，不把上轮Table采集当真实文件验收。按PDF技能读取与渲染流程检查实际下载文件，未提前修改排版。
- 不修复已不完整的旧/tmp PG目录。在Git忽略的ops/backups/quote-pdf-http-20261030/pgdata新建本机独立集群，仅127.0.0.1:55441，库crm_iso_pdf_20261030_test；从空库完整alembic至a1b2c3d4e5f6并运行种子，沿用隔离配置关闭外部推送/调度/Stream。专用run.py覆盖DATABASE_URL/FILE_ROOT，不输出JWT或连接凭据，不调用生产库。
- 真实ASGI登录/正常API：管理员确认已有SKU本地主数据→建客户/四商机与CNY报价→明细/明确运费/提交审批（需要时走正式approve）→正式GET PDF。goods/logistics/other三报价版本1/2/3独立PG total均99999999999999.99；普通33.3333×1000版本4为33333.30。成本/主数据/运费门禁均实际执行，无绕过替身或直接改approved状态，四PDF响应200并落测试证据文件。
- 独立bundled pypdf提取实际四文件，三份大额完整含99999999999999.99、不含99999999999999.98，普通合计33333.30；均1页。证据extraction.log与各*-extracted.txt，证明Q-072不仅Table文本正确，也能在真实PG/正式接口下载的实际文件保留分位。没有操作真实客户或外部发送。
- 视觉渲染：bundled Poppler缺Adobe-GB1语言映射，出现Unknown font/no font警告，不能据此断言应用中文缺失；改用macOS PDFKit原生thumbnail，四份实际PNG均逐张查看、中文可见。普通、物流/其他费用页面无所见数字列重叠；goods极长单价与行金额跨列挤入数量，不能宣称整体视觉已通过。没有调整字体或安装新依赖来掩盖问题。
- Q-073/P2已确认：quote/pdf.py明细列宽固定数量22mm/单价25mm/金额25mm，纯字符串Table不按内容收缩/换行。实际goods-native.png出现相邻数字重叠；同字体9pt stringWidth另证两长数宽度均超过25mm减左右padding12pt可用宽，column-widths.log归档。此列宽不是Q-072新改，原服务器同源pdf.py保留该布局（上轮哈希已确认），本轮未再SSH或声称生产真实案例。
- 可选方案：仅放不下的数字缩小字号可保留完整单行数值和普通页面原样，但极端值字号变小；或允许长数字换行，可保字号但一笔金额分行、行高变化。按AGENTS界面取舍规则，已请求用户选推荐的局部缩字号或换行；尚未答复，不把沉默当批准。未改变当前PDF布局、金额或业务规则，Q-069原提示问题也未获选择。
- 证据ops/backups/quote-pdf-http-20261030/含初始化/迁移/种子日志、run.py、正式API脚本/result.log、四PDF/提取文本、原生PNG与render.swift/log、列宽检查。四文件是测试样本，goods存在已记录排版问题，不作为无瑕疵交付文件。专用PG已正常停止，diff检查通过；无新应用修改，不重复未变应用1520项全量。无生产写入/服务变更、真实外发、提交推送部署。
- 下一步：收到Q-073选择后做最小排版修复，验证长数字及普通样本、PDF提取和原生渲染；等待期间可转查独立档案精度/生命周期入口。Q-064至Q-068/Q-070/Q-071/Q-072仍本地待发布；本轮未获新发布授权，长期质量目标未完成。

## 2026-10-30 Q-072 直接报价PDF大额快照浮点转换丢分（服务器确认，本地修复）

- 上轮内部报价撤回/重提真实闭环为实质进展。本轮重读长期指令、质量目标/进度和Git状态，转查对客PDF消费者，与已有前端exact/Excel冻结测试区分。保留企微Q-064至Q-068/Q-070/Q-071及Q-069待决定状态。
- Q-072/P2代码与红例：download_pdf把版本subtotal/charge/discount/total转float，items/charges直接使用数值型序列化字段；renderer对logistics/other又显式float。合法Numeric(16,2)快照99999999999999.99在实际reportlab表格文本变成99999999999999.98。独立SQLite fixture+实际download_pdf函数、实际reportlab渲染（仅Table包装采集最终字符串，不替换其绘制实现），goods及other路径修前精确合计断言失败；有效PDF字节已生成。该证据是路由→渲染器表格输入，不声称独立PDF提取或真实PG/HTTP全链。
- 修复仅进程内PDF投影：保留版本Decimal合计，item数量/单价与既有line_amount十进制结果、charge.amount直接送renderer；summary保留原标签/公式但运费和其他费用用原Decimal。renderer不再float运费/其他费用，数量用十进制普通格式去尾零，避免g格式科学计数和精度丢失。对外JSON接口/既有exact字段、成本/主数据/运费门禁、金额计算/舍入、权限/版本冻结/数据库结构均不变，无新依赖或业务口径取舍。
- 永久test_quote_pdf_precision覆盖商品行及合计、运费与其他费用条目和汇总三场景，保持成本校验、已批准状态与运费确认，只有无关的主数据确认夹具门禁以AsyncMock隔离。修后实际PDF生成且表格含完整精确金额；与quote_cost_gate、quote_doc_freeze、audit_risk_repairs共113项通过，完整后端1520项通过（进程exit0及日志1520成功标记），应用与新增测试pyflakes/diff检查通过。
- 夹具限制如实记录：首版logistics用例新增费用未设置logistics_confirmed_at，被原门禁正确422拒绝，后补明确确认时间，未放宽应用校验；新增费用ORM对象无强引用时SQLite重新加载Numeric会自身经过浮点，后将测试对象保留强引用以单独验证原Decimal投影，不把SQLite的金额能力当PG证明。goods/other有直接金额精度红例，未把初版logistics门禁失败冒称精度红例。当前临时PG目录缺少完整集群文件，pg_ctl启动前拒绝；没有修补旧目录或连接生产替代，本轮只用全新内存库。
- 只读SSH确认服务器46be2876d374473bd559f995de964af6bcf575b0；quote/router.py SHA256 7dd3184cb991a9f5959c74e3c368be05e135d51ca18fc08df8b5670d300e028f、quote/pdf.py e47f77a93b8cd1e59017f709529750b61e2456facb2508c1699444698bf0833e，均与本地HEAD修前完全一致，确认线上包含相同float路径。证据ops/backups/quote-pdf-precision-20261030/含红绿/完整后端/只读哈希；无生产写入、提交、推送或部署。
- 边界/下一步：不泛化为普通小额都丢分；尚未做PG真实API大额报价下载、独立PDF文本提取/页面视觉核对或大额长数字布局验收，未声称这些完成。历史PDF/Excel档案不改写，归档文件的JSON数字/Excel浮点精度另需独立证据，不能因本次直接PDF修好即认定所有对客档案已覆盖。可在全新专用隔离PG补验或转独立消费者，长期质量目标仍未完成。

## 2026-10-29 内部报价撤回、改价、重提与旧审批隔离真实补验（未发现新缺陷）

- 上轮Q-071修复/正反对照及完整1517项为实质进展。本轮重读长期目标、质量目标/进度和Git状态，转查内部报价审批，区别于Q-034钉钉打样/合同旧轮次回调。保留所有本地企微补丁与Q-069待选择状态，无新应用修改。
- 仅本机PG55439/crm_iso_local_e2e_20261027_test及真实ASGI鉴权/API，正常建商机→CNY报价→SKU1数量10/单价1→明确零运费。成功轮报价53/版本60，第一轮审批226 pending时单行改价422锁定；正式撤回后旧审批withdrawn、版本not_submitted，单价可改0.9；再次提交创建独立审批227 pending。
- 对旧审批226再发approve/reject均非成功业务拒绝；独立PG确认新审批仍pending、版本pending、主单pending_approval，旧/新ApprovalRecord数量均与尝试前基线一致，没有写伪审批记录。之后正式通过新审批，实例/版本/主单均approved；SQL核对quoted_price0.9×quantity10=9、版本total_amount9。通过后的单行PATCH及整版items/batch均422，原明细价格/数量保持，旧审批仍withdrawn。
- 首次夹具未提供运费被既有明确校验422拦住，随后通过正式charges接口显式确认0运费，未关闭校验或改配置。第二轮已完成审批链后，独立SQL误读quote_items不存在的amount列而中断；核对模型确认行金额由quantity×quoted_price派生，修正探针并另建成功轮，同时核对真实版本合计。两次探针输出保留，不把夹具/查询错误记应用Bug。
- 证据ops/backups/quote-withdraw-resubmit-20261029/含实际ASGI/PG脚本、两次初次探针及完整成功结果。无新确认Bug或应用改动，不重复未变应用全量；后端最近1517项仍见Q-071，不冒称本轮重跑。diff检查通过，专用PG已停止。无SSH、生产业务操作、真实外部审批/消息、提交推送部署。
- 范围/下一步：本轮为顺序内部审批闭环，不证明内部审批/撤回并发全部锁图、实际浏览器、对客邮件/PDF快照或所有规则分档。可转查对客导出与审批版本冻结的尚未充分覆盖消费者；Q-064至Q-068、Q-070/Q-071仍本地待发布，Q-069等待既有选择，长期质量目标继续。

## 2026-10-29 Q-071 中断后的CRM-only交接可能误入企微重试（本地补齐，未发布）

- 上轮Q-070底账账号补齐及红绿/完整1515项为实质进展。本轮恢复长期目标、质量文件和Git状态，专查恢复后的显式外部操作选择，不把上轮无企微关系夹具的“禁止客户端”证据泛化为有关系时也安全。
- Q-071/P1边界：初始清单将所有wecom_relation记pending，仅在下一阶段transfer_wecom=false分支才改skipped并二次提交；恰在首次commit后中断时，重试会把该项视为待转接。Q-070补齐账号后此路径可真正到达外部调用。原服务器还有Q-070提前422阻挡，故不能将本轮完整误调用链声称为未修改服务器已可复现；这是恢复能力补齐后必须同时修好的边界，未部署过这组本地改动。
- 真实ASGI/PG红例：正常API建用户/客户/订单/文件，隔离Session仅补一条合成外部事实及跟进关系（无真实企微同步），POST transfer明确false；AsyncSession.commit在首个真实提交后抛错，job/逐项落库，HTTP500。恢复后正式retry200，本机FakeClient记录get_follow_users及transfer_customer各1次，零调用断言失败，证明显式“不转企微”被遗忘。全程替身接住外呼，无真实第三方操作。
- 最小修复：在初始WeComTransferItem创建时，wecom_relation按已明确的transfer_wecom选择写pending或skipped；false同时保留既有“本次未要求转接企微关系”原因。非关系项仍not_applicable，原后续skip分支/提交时点/次数/锁序/业务权限不变，不添加自动重试策略或历史清洗。落实请求明确选择，无合理业务取舍，不需要用户再决定是否允许忽略false。
- 永久test_wecom_transfer_ledger新增true/false两个首commit中断对照：false修前pending!=skipped红例；修后3项全部通过，true仍pending、false为skipped且原因完整。完整后端1517项通过（pytest进程exit0，日志1517成功标记），pyflakes和diff检查通过；前端未变未重跑。
- 实际PG绿例任务65（客户45/订单45）：首次commit后同样中断，正式retry200，FakeClient调用0，原企微关系仍active，CRM按原逐项接手人完成、文件访问和字节正确。反向对照任务66（客户46/订单46）明确true，同位置中断后retry200，恰调用核对/转接各1次，关系transferred、CRM正常；未通过一律禁用外部转接来让false通过。红例任务64只在隔离库发生替身转接，保留证据不改真实数据。
- 证据ops/backups/handover-crm-only-retry-20261029/含实际HTTP/PG红绿、true反向对照、脚本、永久单元红绿和完整后端。专用PG55439/crm_iso_local_e2e_20261027_test已停止，进程内commit/get_client故障替身finally恢复；门禁只在本测试进程设置，未改持久配置。无SSH/生产变更、真实外发、提交、推送或部署。Q-064至Q-068、Q-070/Q-071本地待发布，Q-069待界面选择。
- 边界/下一步：不推断历史pending关系是否获过外部转接授权，不清洗历史任务；真实外部调用未知结果、进程强杀、全并发锁图仍未穷尽。若处理历史记录需另定口径。当前补丁保障新建任务的显式选择与恢复一致；可转查其它独立模块，避免继续重复同一首commit中断输入，长期质量目标仍未完成。

## 2026-10-29 Q-070 交接底账漏存双方账号导致中断后无法重试（服务器确认，本地已修复）

- 上轮CRM逐项拆分交接正常/重复重试实证为实质进展。本轮恢复长期目标、质量文件、Git状态，沿独立失败恢复分支调查；没有重复声称正常交接测试覆盖中断。Q-064至Q-068补丁保留，Q-069仍待界面选择。
- Q-070/P2证据：transfer_relations先提交job与WeComTransferItem底账，再执行CRM/外部操作，但detail只在最后finish_job保存；retry_transfer依赖job.detail.handover_id/takeover_id。实际ASGI/PG中在订单_move_owner_if_still注入RuntimeError（初次底账已真实提交、随后客户变更尚未提交），HTTP500后job/两项pending留存、客户及订单归属回滚；正式retry返回422/40001“没有留存双方账号”。本轮新建任务被当成旧版本记录，不能按既有逐项计划恢复，不是仅静态怀疑。
- 唯一最小修复backend/app/modules/wecom/service.py：在既有首次commit前赋job.detail=dict(detail)，保存已验证的双方ID/姓名和逐项配置。逐项真实目标仍以原WeComTransferItem.to_owner_id为准；不改提交时点/次数、锁序、隔离级别、表结构、权限、业务分配/外部调用规则，不新增重试策略或清洗历史任务。实现原底账“中断后可恢复”的既有承诺，属无实质取舍遗漏补齐，未要求重复选择事务方案。
- 永久test_wecom_transfer_ledger：独立内存SQLite，计划落库后客户端工厂抛错，rollback后查底账账号及逐项覆盖目标。修前job.detail=None的TypeError红例，修后相关企微89项通过；完整后端1515项全部通过（pytest进程exit0，日志1515成功标记），pyflakes与diff检查通过。前端未变，不冒称重跑前端。
- 实际PG绿例：新任务62（客户42/订单42）同样中断后正式retry200，retried2/remaining0，分别恢复原客户/订单接手人，文件访问/字节/归属正确。再独立任务63（客户43/订单43）中断后通过正式users接口停用订单接手人：retry200但remaining1，客户成功转默认接手人，订单仍旧owner且逐项failed明确“已停用”；正式启用及新登录后retry仅1项、remaining0，再重试0项。成功客户的owner_history数量不增加；原sales_owner_id/created_by和文件字节保持。此前旧JWT不因本次测试绕过启停校验。
- 环境与证据：仅本机PG55439/crm_iso_local_e2e_20261027_test及专用file_root，故障替身均finally恢复；测试进程开启交接门禁并transfer_wecom=false，外部客户端工厂禁止调用，无真实企微流量。ops/backups/handover-ledger-retry-20261029/含真实HTTP红绿、停用恢复、永久单元红绿、完整后端及脚本。专用PG已停止。
- 只读SSH核对生产仍46be2876d374473bd559f995de964af6bcf575b0，wecom/service.py SHA256=03edc0ed397504c9b17c0b5dab7ec91a5c045102e743abc2dfc399922c40027c，与本地HEAD修前文件完全一致，确认当前服务器包含同一遗漏。无生产写入/服务变更、提交、推送或部署。Q-064至Q-068及Q-070均本地待发布；本轮恢复审查不自动获得新发布授权。
- 边界/下一步：不修复历史detail缺失任务，不证明真实企微外部转接未知结果可安全重放；两类问题需独立证据/决定。中断任务保留running及原异常HTTP500沿用现状，没有借此改失败分类。可继续其它未充分覆盖的接口契约/故障恢复；长期质量目标未完成，Q-069仍等待已有选择。

## 2026-10-29 CRM离职逐项拆分交接与文件范围真实补验（未发现新缺陷）

- 上轮普通客户正式改派及文件访问证据为实质进展。本轮恢复长期指令、质量文件/Git现状，转查与普通改派不同的离职逐项路径：客户和其订单指定不同接手人，随后重试已完成任务。不重复旧直接改归属矩阵或此前服务层交接归属测试；Q-064至Q-068保留，Q-069未获选择仍未改。
- 本机PG55439/crm_iso_local_e2e_20261027_test及专用file_root，真实ASGI登录/正式users创建三名self业务员，创建客户40/订单40并分别上传客户、订单附件。仅本测试进程settings.wecom_transfer_enabled=True，正式请求transfer_wecom=false；wecom_client.get_client设置禁止调用断言，任何尝试取外部企微客户端均失败。未改持久配置、未调用真实企微/停用真实账号。
- 正式transfer-preview清单包含目标customer/order；POST transfer默认客户接手人甲，item_assignees明确订单交乙。实际job60 success，独立PG确认客户owner=甲、订单owner=乙；sales_orders.sales_owner_id及created_by与交接前原值严格相同，逐项order记录to_owner=乙、crm_status=moved。
- 三名账号沿用首次登录JWT，在交接前、交接后、完成任务retry后各验证两文件元信息/下载/预览及业务附件列表：原负责人最初可见两份，完成后均403；甲只可访问客户文件，乙只可访问订单文件。合计54次文件读取及18次列表验证，成功下载/预览的字节匹配。首次完成后的retry返回retried0/remaining0，逐项记录内容不变；文件原上传人不改，内容与SHA256保持。
- 脚本/成功结果归档ops/backups/handover-split-files-20261029/。首次禁止外部调用钩子误指service.WeComClient而非实际wecom_client.get_client，环境校验后即AttributeError、尚未登录/建数据；修正实际工厂后全程通过，初次输出保留。没有改应用或放宽业务/权限断言。
- 无新确认Bug，无应用修改，不重复未变应用全量；后端最近1514项/前端完整回归见Q-068/Q-064。diff检查通过，专用PG已停止，无SSH、生产写入、真实外部请求、提交推送部署；Q-064至Q-068仍本地未发布。
- 边界/下一步：本轮仅CRM-only顺序逐项交接，不证明真实企微继承、失败项外部未知结果、并发重试全部锁图、所有单据类型及生成档案均覆盖，也没有扩张为业绩报表重算证明。可转查未充分验证的交接失败项恢复或独立业务入口；Q-069继续等待既有选择，长期质量目标未完成。

## 2026-10-29 正式客户改派与附件访问范围真实补验（未发现新缺陷）

- 上轮自动分配/删除真实竞争证据为实质进展。本轮重读长期目标、质量文件和Git状态，核对2026-10-27文件访问矩阵：历史测试通过夹具直接改归属，明确未验证正式交接接口。本轮补正式HTTP客户改派→单据搬迁→同JWT附件访问，非重复直接改owner矩阵。Q-064至Q-068保留，Q-069继续等待原界面选择。
- 仅本机PG55439/crm_iso_local_e2e_20261027_test和专用file_root，真实ASGI应用/登录/权限/事务。正式POST users新建两个self范围salesperson账号（角色通过隔离库查询确认），旧业务员正常建客户，管理员正常手工建CNY订单并指定旧业务员；旧业务员通过multipart分别上传客户及订单附件。测试成功轮客户39、订单39，未用生产账号或生产数据。
- 改派前旧业务员对两文件元信息/下载/预览及两业务附件列表全部200，新业务员全部403。管理员正式POST customers/{id}/transfer后，独立SQL确认客户与订单owner均为新业务员；两人继续使用改派前原JWT，旧业务员上述入口全部403，新业务员全部200。合计24次文件读取及8次业务列表检查；下载/预览响应实际字节均与原上传内容一致。
- files.uploaded_by仍是旧业务员，文件内容及SHA256完整，说明曾上传过文件不会绕过现有业务挂载范围，新负责人也无需重新上传。正式改派响应含document_transfer。该结果证明所列客户/订单挂载在真实交接后遵循已有范围，不额外收紧用户先前拒绝的改名/删除维护权限口径。
- 初次夹具让业务员直接手工建订单，被已有order:assign门禁403拒绝，尚未上传文件；核对明确“手工建单限主管”规则后改为管理员代建并指定旧owner，未放宽角色或应用校验，初次输出及隔离合成记录保留。证据ops/backups/file-real-transfer-20261029/含脚本、初次门禁输出及完整成功结果。
- 无新确认Bug或应用改动，不重复未变应用全量；最近后端1514项、前端完整回归沿用Q-068/Q-064。diff检查通过，专用PG已停止。无SSH、生产/真实外部操作、提交、推送、部署；本地待发布及待决定状态不变。
- 边界/下一步：本轮顺序正式交接不证明下载已经开始后能撤销已发字节，也不证明多进程并发改派/共享引用的全部锁图、离职逐项交接或所有单据类型。既有历史矩阵覆盖合同/打样/跟进另有证据，未冒称本轮重跑。后续可审查离职交接的不同接手人/共享文件等独立链，长期目标继续。

## 2026-10-29 应收删除与超收自动分配竞争真实补验（未发现新缺陷）

- 上轮专用凭证multipart失败补偿实证为实质进展。本轮重读长期指令、质量目标/进度与Git状态；历史2026-10-27已有人工分配/清空与删除四组真PG证据，故不重复该矩阵，补其明确未覆盖的“确认另一笔回款自动选中待删目标期”链路。Q-064至Q-068补丁保留，Q-069未获选择不提前改。
- 本机PG55439/crm_iso_local_e2e_20261027_test、真实ASGI登录鉴权/正式建单/建两期各100/登记150待确认回款（登记一期）。分别创建订单37、38。本次只在payment.service.lock_order真实获得订单锁后暂停首HTTP任务，第二HTTP同时运行；独立连接查询pg_stat_activity/pg_blocking_pids证明存在真实锁等待，再释放首任务。未替换锁实现、数据库或业务写入，所有钩子finally恢复；每组10秒边界内完成。
- 删除先行：空二期DELETE200、确认POST200；二期真实删除，确认回款仍150，自动分配一期100、待分配50，孤儿分配0，删除审计1。确认先行：确认POST200，自动分配一期100/二期50；随后DELETE422，二期保留，已分配150、待分配0，删除成功审计0。均独立SQL确认回款confirmed，并核对正式finance-summary金额守恒。
- 为避免读取端自动修正掩盖问题，未先GET milestones，而直接SQL检查写操作提交后的order_milestones：两组deposit均最早到账日2026-10-01；删二期后剩余一期收齐，payment为该日；保留未收齐二期时payment为空。说明本组自动分配/删除的持久化跟单事实一致，不只验证函数被调用。
- 脚本及两组成功结果ops/backups/receivable-delete-auto-confirm-20261029/。无新确认Bug、无应用修改，不重复未变应用全量；最近后端1514项/前端完整回归沿用Q-068/Q-064既有证据，未声称本轮重跑。diff检查通过，专用PG已停止；无SSH、生产/真实第三方操作、提交、推送、部署。
- 边界/下一步：两种受控HTTP交错不是大负载/全锁图证明，未覆盖在目标期直接新登记回款与删除同时竞争、进程中断或全订单删除；后续可转该剩余入口或其它独立模块，不能把保护正常的代码仅凭缺少新测试记Bug。Q-069继续等待既有界面选择，长期目标仍未完成。

## 2026-10-29 回款凭证登记失败与提交回执不明真实补验（未发现新缺陷）

- 上轮回款人工归属/应收调整实际ASGI与PG组合验证为实质进展。本轮恢复长期目标、质量文件、Git状态，查阅此前凭证并发及存储层测试，选择当时明确未覆盖的专用multipart入口失败补偿；不重复上传/确认顺序矩阵，也不实施未获决定的Q-069界面变更。
- 范围：本机PG55439/crm_iso_local_e2e_20261027_test与专用file_root，真实ASGI登录、API创建CNY订单36/应收/待确认回款25，真实multipart上传、PG事务、磁盘、下载和删除。仅两处本次进程内故障替身：payment.router.write_audit在attach_voucher阶段抛错；AsyncSession.commit先执行真实提交后单次抛错。均finally恢复，不改应用或正式配置，不称真实数据库网络中断/HTTP断流。
- 审计失败：钩子确认已写盘新增1个文件后抛错；HTTP500，独立PG确认files数量回到基线、voucher_file_id为空、attach_voucher审计0；完整隔离文件目录相对路径及SHA256映射等于之前基线，无半成品且未误删其他原件。
- 提交回执不明替身：真实PG提交成功后抛错，HTTP500；补偿经rollback重查已登记object_key，未删除原件。独立PG确认回款关联有效、files只新增1条、审计恰1条，原件内容/size/SHA256匹配。正式GET凭证200且字节一致，重试POST400拒绝覆盖、关联ID与文件数量不变。最后正式DELETE200，文件行/关联/磁盘全部恢复上传前基线。该结果证明保留已提交原件，不把500包装成成功，也不擅自添加上传幂等或自动重试策略。
- 证据ops/backups/payment-voucher-compensation-20261029/含脚本、成功输出与预期故障栈。最初环境校验将已resolve的/private/tmp与未resolve的/tmp比较而提前拒绝，尚未登录/写入；修正为两边均resolve后仍严格限制在专用测试根目录，初次输出保留。
- 未发现新确认Bug，无应用修改，不重复未变应用全量套件；最近后端1514项及前端完整回归见Q-068/Q-064。diff检查通过，专用PG已停止。无生产/SSH/真实第三方/提交/推送/部署；Q-064至Q-068本地未发布，Q-069仍待原问题选择。
- 边界/下一步：本轮真实ASGI multipart但非真实TCP上传中断，未证明进程强杀、磁盘持续故障、数据库无法重连时的补偿；不凭未覆盖就记Bug。后续可转查尚未充分覆盖的应收删除、业务文件共享引用或其它跨模块链，长期质量目标继续。

## 2026-10-29 回款人工归属与应收调整组合真实补验（未发现新缺陷）

- 上轮Q-069真实浏览器证明提示缺口并请求界面决定，为实质进展；本轮重读长期指令、质量目标/进度和Git现状，不把未答复当批准，保留Q-069待决定及Q-064至Q-068补丁。选择独立资金链，不重复之前ERP故障测试或只运行既有单元。
- 调用链核对：已确认回款由ensure_payment_pending拒绝再次确认/驳回，不存在任意撤销已确认金额的接口；应收PATCH调用rebalance_order_allocations，再重算全部有效期和跟单节点，人工完整决定（含空分配）由既有模式保护。既有test_payment_rebalance_order已覆盖服务层，本轮补真实ASGI鉴权/正式HTTP/PG落库的组合场景，未将静态疑点当Bug。
- 仅本机PG55439/crm_iso_local_e2e_20261027_test，实际登录、建CNY订单35、两期各100、两笔各100并确认。重复confirm与reject均400/40002明确拒绝，独立SQL回款始终恰2笔；finance-summary实收200/待确认0/已分配200/待分配0，未重复记账。
- 正式PUT把第二笔指定第二期100，再PATCH第一期降至50、第二期提前到期：实收200/已分配150/待分配50/未结应收0，人工行id、created_by、created_at完整保留，自动笔仅分第一期50。随后正式PUT第二笔空分配，再把第一期调到150：自动笔按新到期日抵第二期100，人工笔完整待分配100；订单实收200/已分配100/待分配100/未结应收150。再改备注触发重算后数值与分配不变，两期状态pending/paid，人工调整审计恰2条。
- 完整脚本及结果ops/backups/payment-manual-rebalance-20261029/。首次探针错误预期终态拒绝为422，实际ensure_payment_pending沿用AppError默认400及40002；核对实现后仅校正状态预期，不改应用或业务断言，初次订单34保留于隔离库并保留失败输出。成功轮订单35完成全部步骤；没有清库或非法直接改业务行造结果。
- 本轮无新确认Bug、无应用修改，不重复未变应用全量测试；最近后端1514项/前端构建和完整浏览器仍见Q-068/Q-064。diff检查通过，专用PG已停止。无真实外部消息/生产操作/SSH/提交/推送/部署，Q-064至Q-068仍本地未发布。
- 范围/下一步：证明该顺序组合的金额守恒、人工决定和审计，不扩大为所有并发、历史脏数据或性能证明。Q-069等待已有选择，不重复索要批准；后续可转查应收删除与凭证生命周期等尚未充分覆盖的独立链路。长期质量目标仍未完成。

## 2026-10-29 Q-069 订单ERP刷新未显示状态被拦截原因（已复现，待界面口径决定）

- 恢复质量目标、进度、长期指令、AGENTS.md及Git状态后，承接上轮ERP主动读取后端验证，转查订单详情真实界面反馈。保留Q-064至Q-068，不修改其补丁或发布状态。
- Q-069/P2触发：订单已取消且有ERP单号，对方回传Sent；后端refresh_status调用订单状态服务，捕获业务拒绝并返回changed=false及blocked_reason。代码证据backend/app/modules/erp/service.py:799-829；订单路由保留result，未丢原因。前端shared/api/order.ts的refreshStatus类型未声明blocked_reason，OrderDetailPage.tsx:593-609只判断changed，否则显示“状态没有变化（对方回传：Sent）”。其它禁止状态转换同样会丢提示；业务拦截本身没有失效，不涉及取消订单复活。
- 隔离真实浏览器加载当前frontend/dist，独立profile、内存API提供cancelled订单及上述明确响应；点击“同步履约状态”，确认确实发出POST请求，页面出现无变化提示且不含“已取消的订单不能再变更状态”。脚本及红例输出ops/backups/erp-refresh-ui-20261029/。这是实际前端代码与响应替身验证，后端取消保护的真实ASGI/PG证据见前两轮；本轮不声称前后端同时直连PG，也不声称生产已发生。
- 建议只在blocked_reason有值时显示该原因，正常已变化/未变化提示保留；不修改业务规则、数据库或同步操作。按AGENTS.md用户体验取舍要求，已请求人工选择“显示未更新原因”或“保持现有提示”，未提前实施。当前唯一refreshStatus调用点已核对；deprecated refreshErpStatus无调用者。
- 本轮无应用修改，无生产、SSH、数据库、第三方调用、提交、推送或部署。浏览器探针通过，diff检查通过；没有重复运行未变应用全量测试，最近后端1514项与前端完整回归见Q-068/Q-064。Q-064至Q-068仍本地未发布。
- 下一步：收到Q-069选择后按决定补最小界面变更并回归；若保持现状则记录例外。此单项等待不代表长期审查全部阻塞，后续仍可转向尚未充分覆盖的独立业务链；真实第三方协议、所有锁竞争/性能边界仍未穷尽。

## 2026-10-29 ERP主动读取失败留痕与恢复真实补验（未发现新缺陷）

- 上轮ERP回调重复/取消组合验证为实质进展。本轮重读长期目标、质量目标/进度和当前工作区，选择另一入口链：订单POST refresh-status与集成GET status在连接异常/明确拒绝后是否保留日志、恢复后只写一次履约事实。Q-064至Q-068补丁保留，无新增应用修改。
- 真实ASGI登录和API创建CNY订单33，再通过正式reconcile接口登记唯一合成外部单号；本机PG55439/crm_iso_local_e2e_20261027_test，无生产或外部系统操作。仅service.get_adapter在本次测试进程替换为三个读取故障/成功替身，路由、状态服务、会话提交/回滚、审计与PG均实际实现；不称真实ERPHTTP连接故障注入，也不绕过正式adapter能力门禁以尝试外发。
- 两入口各验证连接ConnectError和明确ErpError拒绝：按原约定分别409/unknown日志、502/failed日志，每次独立PG确认入站日志增量1且有原因，路由后续rollback不撤掉已提交的失败记录；订单仍pending，状态历史等于建单后的基线。没有把未知结果既有409口径擅自改为可重试写语义。
- 随后替身恢复正常读取Producing：订单入口200/changed=true写in_production；集成入口再读200/changed=false，状态历史只比基线多1条。独立SQL最终入站日志顺序unknown/failed/unknown/failed/success/success，六条全部保留。证据ops/backups/erp-refresh-20261029/含脚本与四故障、两恢复输出。
- 本轮未发现新确认Bug，无新应用/测试源码修改，不重复未变应用全量；后端最近1514项及前端完整浏览器/构建证据仍见Q-068/Q-064，未冒称本轮重跑。diff检查通过，专用PG已停止，无SSH、生产操作、提交、推送或部署。Q-064至Q-068仍本地未发布。
- 范围/下一步：适配器真实验收/外部字段协议、并发外部读取与订单取消、非本轮故障类别仍未由此证明；此前已有锁场景证据不等同全锁图。两读取入口失败事务已有独立证据，后续转查其他尚未覆盖的跨模块入口或真实界面错误反馈，不重复相同四种请求；长期质量目标继续。

## 2026-10-29 ERP回调重复事件与取消终态组合真实补验（未发现新缺陷）

- 上轮企微partial/重试实际数据守恒验证为实质进展。本轮重读长期目标、质量目标/进度及Git状态；回查既有ERP未匹配恢复和Q-038并发去重证据后，选择新组合：正常建单→同一生产事件重复→正常取消订单→旧事件重放/新迟到发货事件。保留Q-064至Q-068，不新增应用修改。
- 环境：本机PG55439/crm_iso_local_e2e_20261027_test，真实ASGI应用/登录/鉴权/回调密钥比对/PG会话；正常API创建CNY订单32。进程内合成随机ERP回调密钥、采用已有jushuitan状态映射，不调用外部ERP，不打印密钥或真实业务数据。
- 真实HTTP与独立SQL通过：无密钥/错误密钥403且integration_logs计数不变；合法Producing事件更新一次，相同请求replayed=true，不多写成功日志或状态历史。随后正常取消200；原生产事件重放仍replayed且不改订单，新Sent事件200但changed=false并有blocked_reason。数据库订单仍cancelled，取消后的历史计数不增加，入站日志恰success1/failed1；失败原因“已取消的订单不能再变更状态”，成功日志原始partner_trace完整保留。
- 初次探针错误把历史绝对条数预期为1，忽略正常建单已有初始历史，故在重复事件后停止。独立核查创建行为后修正为创建后基线+1，未更改应用、去重或生命周期断言；首轮订单31保留在隔离库，完整成功轮订单32。失败输出与最终脚本/结果归档ops/backups/erp-cancel-replay-20261029/。
- 本轮未发现新Bug，不新增镜像单元或重复未变应用全量测试，最近后端1514项、前端构建/完整浏览器仍见此前Q-068/Q-064；diff检查通过。本机专用PG已停止，无SSH、生产操作、真实外部调用、提交/推送/部署；Q-064至Q-068仍本地未发布。
- 限制/下一步：本轮顺序交错HTTP/PG不能证明所有并发；不同事件键的正常履约回退目前沿用订单既有纠错规则，回调请求模型无对方事件时间/版本，不能凭状态编号判断真实新旧或自行禁止所有回退。若要实现真实乱序排序需外部权威协议与业务决定，本轮未将该静态风险记为已确认Bug。可继续审查其它回调校验或订单刷新错误链；长期目标未完成。

## 2026-10-29 企微部分失败→恢复重试→重复同步真实补验（未发现新缺陷）

- 上轮Q-068无效token缓存修复及真实恢复证据为实质进展。本轮重读长期目标、质量文件及Git现状，用户继续后选择不同的新业务场景：同轮一名成员明确业务拒绝、另一名成功，随后恢复再同步，检查成功数据、人工关联和审计是否保持一致。保留Q-064至Q-068补丁，无新增应用修改。
- 仅本机专用PG55439/crm_iso_local_e2e_20261027_test及ASGI正常登录鉴权；HTTP MockTransport提供两成员、一名84061拒绝，另一名合法详情。随机唯一合成外部联系人原已bound到隔离客户4，normalization记录操作者1；没有调用真实企微或改生产数据。
- 第一次同步200/partial，success1/fail1，实际成功联系人的姓名/公司正常更新，失败成员未建新联系人。第二次解除替身拒绝后200/success2/fail0，补齐第二联系人且normalize_status=pending，不擅自绑定；第三次重复同步仍2条，无重复新增。三次都独立SQL确认原联系人bound/客户4/normalized_by1保持，原人工关联没有被同步覆盖。
- 独立PG核对三次job状态及成功/失败计数与HTTP一致，每个job各1条wecom_sync_external_contacts审计。证据ops/backups/wecom-partial-20261029/包含实际ASGI/PG脚本与三阶段输出。该结果证明Q-064至Q-068累计异常处理未破坏现有“明确单成员业务拒绝保留其它成功数据”的路径；不扩大为HTTP断网/响应不明也必定局部提交。
- 无新确认Bug、无新应用代码或镜像单元，不重复未变应用全量测试；最近完整后端1514项、前端构建/完整浏览器分别见Q-068/Q-064记录，未冒称本轮重跑。diff检查通过，专用PG已停止；无SSH、提交、推送、部署。Q-064至Q-068仍本地未发布，线上沿用上次核实46be287。
- 边界/下一步：本轮真实本机PG、HTTP响应替身，非真实企微网络故障/浏览器/并发重试验证。部分成功链已补充实证，后续优先转其它集成或跨模块回调状态链，避免继续重复同类响应错误/重试输入；长期目标继续。

## 2026-10-29 Q-068 无效企微令牌被缓存导致恢复后重试仍失败（本地修复，未部署）

- 上轮Q-067清单缺失/合法空列表真实HTTP与PG对照是实质进展。本轮重读长期目标、质量文件与Git状态，保留已有Q-064至Q-067，转查访问令牌缓存与故障恢复，不重复清单格式案例。
- Q-068/P2实际ASGI鉴权HTTP/PG复现：独立进程合成corpid/secret、HTTP替身首次gettoken返回access_token=null，以后可返回有效令牌；旧access_token把null转str('None')并缓存。两个同步请求均502，gettoken只调用1次、下游调用2次，证明第二次仍用无效缓存，即使上游恢复也不重新取令牌。缓存TTL沿用代码6900秒，未实际等待6900秒或声称生产曾发生；缺失字段还会KeyError绕过安全失败类型。
- 最小校验：access_token在缓存前要求非空白字符串；缺失/null/空白/非字符串均抛既有WeComResponseError('gettoken')，不写缓存、不把响应体或令牌值加入提示。正常字符串原样返回，缓存键/TTL/有效缓存命中行为均不变；不新增失效令牌自动重放外部写请求，不修改业务或配置。
- 永久回归增加通讯录/客户联系两类token×8类无效载荷共16组，拒绝后断言缓存空，下一次返回有效token可成功，再取一次命中缓存、总外部取token请求恰2次。修前缺字段KeyError红例；null缓存失败另有实际HTTP/PG红例。修后相关企微113 passed，完整后端1514 passed in15.21s，pyflakes及diff检查通过。
- 实际HTTP/PG修后：首次同步502明确响应格式异常，第二次200；gettoken请求2次，下游仅1次，证明异常值不被发送且恢复请求重新取值；独立PG确认最新两任务为success/failed，失败历史未丢。证据ops/backups/wecom-token-20261029/含红绿、真实PG脚本/结果及完整回归。令牌和下游响应均由MockTransport提供，不称真实企微故障注入。
- 仅本机PG55439/crm_iso_local_e2e_20261027_test和合成凭据，无真实第三方流量、生产访问/写入、提交/推送/部署；专用PG已停止。前端未变，沿用Q-064前端完整浏览器/构建结果。Q-064至Q-068均本地未发布。
- 边界/下一步：未调整有效但已被企微提前撤销的token自动失效策略、配置轮换缓存隔离或同进程并发取token；这些如需新增重试/锁或参数变化需单独证明并评估。当前仅阻止明确定义无效的响应写入缓存，不宣称完整认证故障恢复已穷尽。可转查其他独立集成或回调链，避免重复相同异常token输入；长期目标继续。

## 2026-10-29 Q-067 部门/成员清单缺失误报成功0条（本地修复，未部署）

- 上轮Q-066真实PG空详情覆盖复现及修复为实质进展。本轮重读原长期目标、质量目标/进度和Git状态，保留Q-064至Q-066，转查通讯录清单契约，与联系人详情覆盖场景不同。
- Q-067/P2实际ASGI鉴权HTTP/PG复现：MockTransport返回合法token，部门列表返回{'errcode':0}、成员场景先返回根部门再给缺userlist响应。list_departments/list_department_users均以payload.get(...,[])缺省，两个接口都200、job success/0成功/0失败；用户无法区分上游没有提供清单与确实空清单。未观察到原部门/成员丢失，不将误报扩大为删除用户。
- 最小读取校验：两方法要求清单字段确实存在且为对象列表；部门项id须为可解析十进制整数或字符串，成员项userid须为非空字符串；异常抛既有WeComResponseError，沿用已批准失败回滚/留痕。明确[]仍正常成功0条；正常非空内容原样返回。未强制可选名称/手机号等字段，不改账户创建、匹配/归属规则、角色权限或数据库结构。
- 永久响应测试增加两接口×7类缺失/null/对象/字符串/标量/坏元素/缺身份场景，以及各自合法空/非空4对照，共18项。缺失清单修前DID NOT RAISE红例；修后相关企微97 passed，完整后端1498 passed in14.45s；pyflakes和diff检查通过。前端无新变更，不重复未变前端验证。
- 真实PG修后四场景：部门/成员缺清单均502+一条failed任务；明确空清单均200+success0；独立AsyncPG确认每次任务增量1，全部部门及用户身份/姓名字段前后不变。仅本机专用PG55439/crm_iso_local_e2e_20261027_test与HTTP替身，无真实企微流量；证据ops/backups/wecom-directory-20261029/含真实HTTP/PG红绿、永久单元红绿、完整回归和脚本。
- 未SSH、提交、推送、部署或修改生产；专用PG已停止。Q-064至Q-067仍本地待发布，线上仍上次核实46be287。该修复区分异常与合法空响应，不引入新的业务选择，按无实质取舍的异常输入校验实施。
- 边界/下一步：未穷尽部门树环路/重复身份、每个可选字段、访问令牌结构或外部联系人列表的全部形态；没有将18组响应测试称为完整企微协议证明。下一步可转查令牌缺失/缓存异常边界或其它尚未充分覆盖的集成链，避免重复同一清单缺失案例；长期质量目标继续。

## 2026-10-29 Q-066 企微缺失联系人详情会清空既有外部联系人资料（本地修复，未部署）

- 上轮Q-065通用响应格式归类/真实PG留痕是实质进展。本轮重读长期目标、质量文件和工作区，保留Q-064/Q-065，转查顶层合法但联系人详情缺失的新场景，不重复网络故障或顶层JSON解析。
- Q-066/P2真实ASGI鉴权HTTP/PG复现：合成外部联系人isolated-q066初始name“原联系人姓名”、corp_name“原公司”；实际WeComClient经MockTransport拉成员→列表ID→详情HTTP200 {'errcode':0}。get_external_contact默认返回{}，list_all_external_contacts仅补external_userid，service把缺字段赋None。HTTP报告200/成功1条、job success，独立PG读姓名和公司均变None。故不是只读接口报错，而是异常外部响应清空已有企微事实；没有声称CRM customers/contacts表被该同步修改。
- 唯一最小修复：get_external_contact要求external_contact是对象且external_userid与请求对象一致，否则抛Q-065安全WeComResponseError，沿用已批准失败回滚/留痕。只验详情身份，不要求可选name/corp_name非空；正常合法详情中的空字符串/null仍可更新。响应不明不按企微明确业务拒绝处理，原部分失败边界未改，不新增重试/表结构/锁/依赖或清洗历史数据。
- 永久回归新增7组缺失/null/空对象/列表/字符串/缺身份/错身份拒绝及1组合法可选空值对照；修前缺失详情DID NOT RAISE红例，修后相关企微79项通过，累计后端1480 passed in14.31s，pyflakes和diff检查通过。
- 真实PG修后：同一隔离夹具恢复合成原始值后重跑，缺失详情502明确格式异常，旧姓名/公司完整保留，独立连接确认新增1条failed任务；随后有效详情重试200，正确记录更新为新有效姓名/公司。证据ops/backups/wecom-detail-20261029/含实际HTTP/PG红绿脚本/输出、永久单元红绿及完整回归。外部企微响应是HTTP故障替身，不是生产企微故障注入。
- 仅使用本机专用PG55439/crm_iso_local_e2e_20261027_test及合成原件事实；无生产访问/写入、真实第三方流量、提交/推送/部署。专用PG已停止。前端未变，沿用Q-064已完成浏览器/构建证据，Q-064至Q-066均本地未发布。
- 范围/下一步：只补缺失或错身份详情，未穷尽每个可选字段类型、部门/成员列表缺失、有效但不完整字段的业务语义；不把合法空字段都视为损坏。后续可审查部门/成员完整清单校验或转向其他尚未覆盖集成，避免反复验证同一种空详情。长期目标仍未完成。

## 2026-10-29 Q-065 企微通用响应解析异常绕过失败留痕（本地修复，未部署）

- 上轮Q-064前端刷新红绿与完整浏览器验证为实质进展。本轮重读原长期目标、AGENTS、质量文件与Git现状，保留Q-064补丁，选择不同场景：外部HTTP200但顶层响应格式错误。wecom/client.py在上轮服务器逐字节核对后未被其它人改动；本轮不新增生产访问或故障注入。
- Q-065/P2已确认：_call直接response.json→payload.get→int(errcode)，HTML/空响应抛JSONDecodeError，数组/null抛AttributeError，非法errcode抛ValueError/TypeError；这些不是企微业务错误或httpx网络错误，绕过Q-064失败处理。修前HTML测试明确抛原始JSONDecodeError，不是安全的显式外部响应异常；统一错误处理会给通用500，任务随get_db rollback消失。该类反例原本就不能成功同步，没有把合法空列表判错。
- 唯一异常归类补齐：增加WeComResponseError(RuntimeError)，对非法JSON、顶层非对象、errcode非整数/整数字符串或无法解析值抛安全格式异常，四同步路由纳入既有Q-064回滚/留痕。错误不含原响应体。不继承WeComError，避免把“响应不明”混同成企微明确拒绝；外部联系人按成员部分成功的catch WeComError边界保持，离职继承既有通用异常处理未改，不新增自动重试或宣称外部写未发生。
- 永久test_wecom_response_format.py覆盖10种无效形态及正常0/'0'/业务拒绝3对照；原test_wecom_sync_failure增加四类同步的response失败持久化与回滚断言。相关企微71项通过，累计完整后端1472 passed in14.19s，pyflakes及diff检查通过。无前端应用改动，沿用上轮前端完整浏览器和构建结果，没有重复未变前端测试。
- 实际ASGI鉴权HTTP+本机PG七组：HTTP MockTransport返回HTML、空、null、数组、errcode=null/非数值字符串/小数，均502明确响应格式异常；独立AsyncPG确认每次只新增一条failed任务、success0/fail1、错误与响应一致，部门事实全量前后相同，响应不泄漏合成secret-response。仅本机专用PG55439/crm_iso_local_e2e_20261027_test，外部响应为故障替身，不称真实企微故障；证据ops/backups/wecom-response-20261029/含红绿、真实PG脚本/输出和完整回归。
- 未新增锁/表结构/业务规则/依赖，沿用已批准失败策略、只补原本异常的可识别类型，自主实施；未提交、推送、部署或修改生产。专用PG已停止。Q-064/Q-065均本地待发布，线上仍上轮核实46be287。
- 范围限制/下一步：顶层对象合法但各接口必需字段缺失或嵌套形态不正确（如department不是列表）尚未由这份公共解析校验全部覆盖；没有把本次13组单元视为完整企微协议验证。可继续按读取接口定向核查，涉及外部写接口结果未知的处理必须单独分析，不能直接用业务失败推定安全重试。长期目标继续。

## 2026-10-29 Q-064 同步失败任务在页面及时展示与重试（本地补齐，未部署）

- 上轮用户批准方案后的失败留痕、真实PG回滚和1455项单元通过为实质进展。本轮重读原长期目标、质量目标/进度及Git现状，保留已有Q-064后端修复；转查WeComPage同步任务消费者，没有重复外部连接故障案例。
- Q-064配套遗漏已复现：syncMutation仅onSuccess调用refresh，onError只Toast；jobsQuery始终挂载且无轮询，所以收到502后新增failed任务不会立即获取。隔离浏览器模拟后端已保存一次失败任务并返回502，页面仍“还没有同步记录”，等待新任务的断言超时；不是仅由静态缺invalidate就推断的Bug。
- 最小补齐：syncMutation.onError在保留具体错误Toast后，只失效wecom-jobs查询。不新增页面/按钮，不改变权限/重试次数/同步规则，不对全量客户列表做无谓刷新；与已批准“页面说明原因、记录保留失败”的方案直接对应。
- 正式交互冒烟脚本ops/confirmation_browser_smoke.mjs增加失败任务夹具及永久断言：502后任务立即出现在现有表格，随后再次确认同步能请求成功；原四类同步的打开/取消不写、确认正确接口断言保留。定向探针修前失败、修后通过；完整交互浏览器冒烟退出0，包含打样/审批/回收/企微/客户/金额等原有场景。frontend TypeScript及生产构建通过，diff检查通过。
- 证据ops/backups/wecom-sync-failure-20261029/新增browser-red/green、定向脚本、full-browser及frontend-build日志。仅隔离无头浏览器和本机模拟接口，无DB或真实第三方流量；不称生产端到端验证。后端无新变化，沿用本轮前序1455项和真实PG证据，不重复未变后端全量。
- 未SSH、提交、推送、部署或修改生产，本机浏览器/模拟服务已退出。Q-064前后端修改均保留在本地；当前无需额外业务决定。下一步可转查外部异常JSON/HTTP拒绝在其它集成调用链的实际表现；进程强制终止、数据库失联无法保证失败记录等边界仍如上轮记录，不扩大本修复承诺。

## 2026-10-29 Q-064 用户批准失败留痕方案后本地修复验证（未部署）

- 用户首次“啥意思”后已解释断网点击同步的例子，随后明确“可以按你的来”；批准范围为明确外部失败提示、回滚本次数据且保留一次失败记录，先本地验证、不部署。本轮不改企微权限、已有部分成功/单条失败处理或离职继承未知结果的策略。
- 在四个同步路由入口复用_run_sync：捕获既有企微配置/业务异常及httpx连接、超时和HTTP拒绝；先rollback，再创建并提交一条failed任务，保留本次开始/结束时间、operator和安全错误原因，再返回原配置422/外部502语义。成功和服务层已经处理的partial结果仍由原路由提交，不被改成全批失败。连接/超时/HTTP错误不回显可能携带token/secret的URL原文。
- 永久回归test_wecom_sync_failure.py覆盖四接口×五类错误共20场景，实际内存SQLite中先改旧记录/插新记录再抛错，断言旧值恢复、新值消失、只有一条failed任务且在后续get_db rollback后仍保留；验证敏感字符串不出现在用户/任务错误信息。修前ConnectError未被捕获红例明确失败；修后相关企微测试54 passed，完整后端1455 passed in17.10s，pyflakes及diff检查通过。
- 真实隔离PG补验：实际ASGI鉴权HTTP，合成企微凭据、本机127.0.0.1:1真实连接拒绝；三同步由500/任务增量0变成502明确网络提示/任务增量1，独立连接确认failed/0成功/1失败。切换本机HTTP MockTransport返回合法部门后实际重试200，唯一隔离部门落库、原失败记录保留。额外在真实路由服务边界注入“修改该隔离部门并新增外部联系人后企微拒绝”，实际PG确认原部门名恢复、新外部联系人0、仅1条失败任务留存。
- 证据ops/backups/wecom-sync-failure-20261029/包含红绿测试、实际连接故障输出、真实PG回滚/重试脚本与完整单元结果。仅本机专用PG55439/crm_iso_local_e2e_20261027_test；无真实第三方调用，无生产写入。专用API/PG已停止。未提交、推送或部署，线上仍为上轮实际核实46be287。
- 边界：本轮网络连接拒绝为实际本机TCP，超时/HTTP拒绝为单元故障替身，重试第三方响应为MockTransport；未声称真实企微断网注入或所有锁竞争已覆盖。数据库本身不可写时无法保证保存失败记录，进程被强制终止/取消也未纳入本补丁；不把这些未覆盖情况吞掉为成功。下一步可独立检查前端失败后的同步记录刷新，以及其它集成异常JSON/分页边界，当前无待用户决定的Q-064实现项。

## 2026-10-29 Q-064 企微同步外部失败缺失任务记录与具体错误（已确认，待用户决定）

- 用户明确“继续查”“继续”，恢复审查；上一轮发布完成为实质进展。重读长期目标、质量文件和Git现状，本轮转查企微外部故障边界；未恢复旧未提交补丁或修改生产。只读SSH核对实际部署仍46be287，wecom/router.py、service.py、client.py三份哈希均与本地一致。
- Q-064/P2已确认：sync_departments/users/external_contacts均先start_job插running任务，再调外部API；路由只捕获WeComNotConfigured/WeComError并直接抛AppError，get_db异常回滚把任务一并撤掉，无法在同步历史查询到失败。client._call未归一httpx连接异常，连接拒绝进通用500，页面只得到内部错误。
- 本机隔离PG55439/crm_iso_local_e2e_20261027_test、调度和真实推送关闭。正常登录HTTP在未配凭据下，三接口均422明确配置错误，独立SQL无失败job；follow关系在无联系人时200成功0条属于未发外部调用对照，未据此误判网络处理。
- 进一步实际ASGI HTTP+真实PG测试：仅测试进程设置合成企微凭据、base固定127.0.0.1:1，实际httpx发出本机连接尝试并被拒；部门/成员/外部联系人三接口均500通用内部错误，每次独立SQL job增量0。无真实企微/第三方流量、没有生产写入。证据ops/backups/wecom-sync-failure-20261029/含脚本、连接错误栈和三组输出；首个探针误用系统Python缺httpx，切换项目venv后执行，没有调整应用或放宽断言。
- 建议：明确区分外部连接/超时错误，失败时回滚本次同步数据，再仅保留一次failed任务（时间/类型/原因），保持失败响应和可重试语义，不保存半批数据。相较现状增加失败记录写入，涉及事务取舍，尚未实施。另一选项仅修错误提示，历史仍不留失败记录；保持现状则排障仍需日志。用户首次回复“啥意思”，不视为同意；已用断网点击同步的例子解释，等待明确选择，未部署。
- 现有单条跟进关系失败保留旧关系、部分联系人同步错误汇总是另一套既有行为，本轮不擅自改成全批回滚。网络超时、HTTP拒绝、异常JSON及失败重试的完整验证仍待方案决定后定向补齐；不能把本轮连接拒绝结果称真实第三方故障注入。下一步是获批后先做修前红例、最小事务方案与PG回滚/重试证据，或独立审查其它不依赖此选择的边界。

## 2026-10-29 客户导入前端预览确认与错误反馈（已修复并发布）

- 上轮客户/线索导入接口真实HTTP/PG验证通过后，本轮检查CustomerListPage实际前端闭环。服务器只读核对仍为d71d711；当前web-dist入口引用的CustomerListPage资源只含“导入结果”，没有“导入预览”、preview_token或待裁定展示，与本地页面代码和浏览器复现一致，确认是服务器实际存在的问题。
- 原行为：选文件立即POST正式导入；返回的disputed_count/disputed不展示；HTTP 422只显示axios通用状态，丢掉后端具体原因。隔离无头浏览器实际复现了立即写入、结果漏待裁定、具体错误被隐藏。
- 按用户决定增加预览确认：选文件先POST preview=true并展示将新增、失败及疑似重复待裁定明细；点击确认再原样提交preview_token；冲突失败清掉旧预览并提示重新预览。结果弹窗补显示已进入待裁定的客户及候选说明；预览和正式失败均优先显示后端message。
- 前端浏览器回归通过：选文件只发1次预览请求，预览显示“待裁定1条”、撞单客户和坏行；确认后第二次请求携带preview_token，结果显示待裁定明细；模拟422显示“隔离文件校验失败，请修改CSV表头”。证据ops/backups/customer-import-ui-20261029/。
- frontend TypeScript检查与生产构建通过；Q-063专项20项通过；累计后端全量1435 passed in14.72s；git diff --check通过。修改仅CustomerListPage前端、Q-063后端计数及其回归、质量记录；无数据库结构/依赖改变。
- 用户已授权：服务器确认存在这些问题后修复部署，并同步Gitee和GitHub。提交 `46be2876d374473bd559f995de964af6bcf575b0` 已同步两个远端；GitHub CI 40985734217 的 UI 冒烟与后端回归均成功。统一发布安全测试21项通过；固定提交构建包 SHA256 `8711bab5ce5cb4b8c605ab4815de0a0af7e87a7d5bea33c18f2d4594c00c13d1`，服务器预检和正式发布均通过。服务器后端/前端版本标记均为该提交，RELEASE.json 为 verified，491份清单哈希匹配，服务 active，health status=up/database=ok，钉钉OA流连接仍为connected。备份目录为 `/opt/crm-sales-agent/backup/unified-46be2876d374-bv94c_yu`，证据ops/backups/release-46be287-20261029/。本次发布完成后按用户要求收尾，不把长期质量目标宣称全完成。

## 2026-10-29 客户/线索导入坏行与预览失效真实补验（未发现新缺陷）

- 上轮合同恢复生命周期真实证据为实质进展。本轮重读原长期目标、QUALITY_GOAL/PROGRESS及Git现状；回查既有成本预览失效、SKU保存点与负责人权限测试后，选择新场景：客户和线索预览后，另一真实HTTP请求先提交重复对象，旧令牌确认是否整批回滚。保留Q-063及用户原12项文档删除。
- 先确认既有规则：线索完全重复公司/联系人跳过；客户疑似重复仍新建并开撞单裁定，不把两类导入强行统一，也不改变用户允许线索查重回显名称/编号的决定。专用本机PG55439/crm_iso_local_e2e_20261027_test，推送/调度关闭，仅管理员正常HTTP合成随机唯一名称。
- 每种CSV均为合法行/真实列宽超限坏行/合法行：线索邮箱129字超String128，客户简称101字超String100。预览均created2/failed1，失败行精确第3行，独立SQL确认三行均未落库；实际SQL失败后第二条合法行仍能计算为新增，保存点隔离生效。
- 随后分别以正常POST单条创建首行同名对象：旧预览确认返回409，SQL确认仍只有外部先提交那1条，末尾合法行和导入尝试的重复对象全部回滚。重新预览后，线索created1/skipped1/failed1，客户created2/disputed1/failed1；正式确认与预览计数一致。再次使用已成功的旧令牌提交均409，最终线索2条、客户3条（含原有对象），没有额外新增。
- 独立AsyncPG最终核对每个唯一文件仅1条成功import审计，created/skipped/failed及failed_rows=[3]与响应一致；坏行均0条、末尾合法行各1条。客户仅实际已提交重复对象产生1条pending撞单案件，candidate为该外部先提交的同名客户；预览/失败重试没有遗留案件。证据ops/backups/entity-import-20261029/含HTTP/SQL脚本及输出。
- 本轮未发现新确认Bug，没有新增应用代码或镜像单元测试；没有把现有客户重复建档口径当成重试缺陷。含Q-063的最近完整后端1435项证据沿用，未重复未变应用全量测试。diff检查通过；本机专用API/PG已停止，无SSH、生产业务操作、提交、推送或部署；Q-063继续本地未发布。
- 边界：本轮为实际HTTP/PG顺序交错，未验证双请求同时查重的并发语义、浏览器上传/失败清单展示、大文件吞吐或任意字段组合。无预览令牌的直接导入仍沿用既有规则，未宣称具备通用幂等。下一步可针对真实页面导入失败清单与预览冲突重试补验，或转向尚未充分覆盖的集成故障边界；长期目标未完成。

## 2026-10-29 合同草稿/作废删除恢复与原件保留真实补验（未发现新缺陷）

- 上轮交期与订单取消真实HTTP/PG证据为实质进展。本轮重读长期目标、质量文件及Git现状，保留Q-063与用户原12项文档删除。回查10-27合同普通self访问矩阵和并发签署/作废证据后，选择不同的新场景：同一合同先草稿删除恢复、再作废删除恢复时，回签待办是否错误复活、原件是否损失。
- 启动前核对DATABASE_URL仅本机PG55439/crm_iso_local_e2e_20261027_test、FILE_ROOT仅专用/tmp目录；钉钉/企微推送及调度关闭，外部地址为本机不可连接端口。通过实际管理员登录HTTP上传唯一合成PDF字节并创建上传式合同2、原件43、自动回签任务2；没有模板绕行、伪造OA通过或调用真实OA。合成文件只检验存储字节链，未声称检验完整PDF阅读器渲染。
- 正常HTTP验证通过：同request_key重试只返回同合同及一条回签任务；原件下载字节相同，通用删除原件422。草稿DELETE后详情与专用下载均404、任务cancelled；restore后仍draft、任务恢复pending，原件字节不变。
- 随后void成功，任务cancelled，作废详情与原稿可查且字节一致；重复void和作废后sign按既有规则400拒绝。作废合同再DELETE/restore后仍void、任务仍cancelled，没有把作废任务复活为pending；原件继续受保护422。这里验证历史保留和既有原件保护，不新增权限或改变作废/删除展示口径。
- 独立AsyncPG及磁盘核对：同键合同1条、自动任务1条、contract_original挂载1条，最终void/未软删/未签署/原作废原因保留；任务无删除自动取消标记，不会误命中恢复条件。物理文件size/checksum/HTTP SHA256一致；审计generate1、void1、delete2、restore2、download4、sign0，非法重试没有伪成功审计。证据ops/backups/contract-lifecycle-20261029/含实际HTTP/SQL脚本及结果。
- 本轮无新确认Bug或新增应用修改，未重复未变应用单元/构建，含Q-063的最近完整后端1435项证据沿用。diff检查通过，专用本机API/PG已关闭；无SSH、生产读写、提交、推送或部署，Q-063仍仅本地。
- 边界/下一步：本轮为管理员正常鉴权HTTP+真实本机PG，不能代替普通角色权限矩阵、浏览器、并发或真实第三方异常验证；这些分别沿用已有窄范围证据或仍待覆盖。合同这条删除恢复生命周期已有实证，下一步优先转查未充分覆盖的客户/线索批量导入失败原子性及重试，不继续重复合同作废正常案例。长期目标未完成。

## 2026-10-29 交期待确认期间业务变化与订单取消联动（未发现新缺陷）

- 上轮分配页面失败/刷新浏览器验证为实质进展。本轮重读原长期目标、质量文件和Git现状，保留Q-063及用户原文档删除；转查order/schedule确认时重算、订单取消关闭待确认交期链，与上一轮金额场景不同。
- 启动前核对专用本机PG55439/crm_iso_local_e2e_20261027_test、企微/钉钉推送关闭、调度关闭。正常鉴权HTTP合成CNY订单30，首次访问六节点均无计划；创建初始交期变更不会先改计划，责任人确认后才写计划。
- 变更等待确认期间，正常API将签约节点登记完成、产前样确认节点标不适用，取消原第二批并新增第三批。确认时原预览保留前两批，实际生效快照改为第一/第三批；已完成/跳过节点原计划和实际事实保留，其余适用节点及批次按新交期重排，动态批次节点和批次计划一致。重复确认422，未多写确认审计。
- 再建待确认交期变更、100元应收及10元待确认款，取消订单后：应收cancelled、待确认款rejected、三批均cancelled、待确认交期cancelled，历史已确认交期仍confirmed。旧变更确认及新交期创建均422，订单交期仍为此前已确认值，无取消后复活。
- 独立AsyncPG核对真实表：三批记录仍保留，计划分别+45/+38/+49天（已取消的原第二批未被重排）；原预览和applied快照有正确差异。两次有效确认各一条审计、重复确认无新成功审计；随单作废交期及取消订单的应收/待确认款计数、最终回款状态一致。证据ops/backups/schedule-lifecycle-20261029/含HTTP/SQL脚本、结果与初次探针失败输出。
- 探针修正如实保留：首轮订单28因误写节点键pre_sample_confirm而KeyError；实际为pre_sample_confirmed。次轮订单29已通过确认但错误预期正常发货列表返回取消批次，核对现有查询明确过滤cancelled后，改断言为列表排除，并由独立SQL严格核对取消记录/原日期；未将这些探针错误算作应用Bug，也未放宽取消事实校验。
- 本轮无新确认缺陷、无应用改动，不重复未变应用全量测试；1435项仍为含Q-063的最近全量证据。仅本机合成数据，无生产访问/写入、提交、推送或部署。专用API/PG关闭后结束本轮，diff检查通过。Q-063继续本地未发布。
- 验证边界：真实HTTP和PG，不含浏览器或本轮并发锁测试；已有历史锁测试不能等同全系统锁图穷尽。下一步转查合同作废/附件历史访问或其他尚未覆盖状态链，避免继续重复交期确认已有场景；长期目标保持进行中。

## 2026-10-29 回款分配页面失败重试与刷新补验（未发现新缺陷）

- 承接上一轮真实HTTP/PG分配、统计与审计验证，本轮检查AllocationAdjustModal、OrderDetailPage及queryClient调用链，再用隔离无头浏览器实际操作现有frontend/dist。API由本机模拟服务提供，无真实业务账号、数据库或第三方请求；不称生产端到端验证。
- 保存失败场景通过：必填原因未填时禁止保存；输入50.00与原因后模拟422拒绝，弹窗、金额字符串和原因保留，无成功提示，模拟已保存值仍20.00。随后同一弹窗重试成功，提交精确字符串50.00及原原因，弹窗关闭并显示成功反馈。
- 成功刷新场景通过：捕获到payments、receivables和finance-summary三类重新读取；回款表刷新为未分配0.00；重新打开读取最新分配50.00且原因清空；取消不产生额外写请求；切换应收计划标签显示更新后的50.00。
- 排除静态误报：部分列表查询键未在弹窗中显式失效，不足以证明列表永久显示旧数据；现有默认staleTime及重新挂载会重新获取。本轮只证明上述订单详情已实际验证场景，不扩大为所有跨页/后台挂载状态已覆盖。
- 未发现新确认Bug，未新增应用代码或镜像单元测试。证据ops/backups/allocation-ui-20261029/browser-probe.mjs与result.log；浏览器及模拟服务已退出。沿用当前应用最近完整后端1435项通过证据，本轮没有重跑未变应用构建或后端全量测试。diff检查通过。
- 当前仍只有Q-063本地应用修复及对应回归，未提交、推送或部署；保留用户12项历史文档删除。无SSH或生产操作。下一步转向其他尚未充分验证的审批/撤销或删除恢复链，避免继续重复已验证的分配金额案例；真实角色生产浏览器、第三方故障和全系统并发性能仍未穷尽。

## 2026-10-29 人工分配后的跨期余额、分析统计与审计真实补验（未发现新缺陷）

- 上轮应收状态链真实HTTP/PG及累计回归补证为实质进展。本轮重读完整长期目标、质量文件与Git现状，保留Q-063本地补丁和用户原12项文档删除。检查了既有10-27手工分配服务/并发证据，选择不同的新范围：真实鉴权HTTP调整后，各页面消费者与审计实际返回是否同步，不重复仅调用服务的验证。
- 启动前核对仅本机PG55439/crm_iso_local_e2e_20261027_test，推送和调度关闭。正常创建CNY订单27两期各100，前期过期、后期未来；确认回款18共150自动分配100/50。每次人工PUT调整后，同时读取订单finance-summary、analytics/payments、analytics/receivables比较增量，排除隔离库其它合成订单影响。
- 九阶段验证通过：初始已分150/未分0/各期未结50/订单未收50；集中150到后一期时前期仍逾期100、各期未结100而订单未收50；仅分50给前一期时已分50/未分100/各期未结150/逾期50；清空人工分配时已分0/未分150/各期未结200/逾期100；恢复100/50后各期未结50、无逾期。这些不同汇总各有既有含义，不把应收期余额与整单现金差额不同误判为Bug。
- 负向真实HTTP：分配151超过回款150拒绝422；分到对照订单26的计划拒绝422；同一期重复两条在schema校验拒绝400。每次拒绝后原分配/模式和所有统计不变。初次探针把schema拒绝也预期为422而停止，核对统一错误处理实际400后按该明确契约修正探针；请求本身正确被拒，不记为应用错误，也没有扩大成功断言或接受任意状态码。
- 随后新增并确认20元回款19，自动分到后一期剩余欠款，原回款18的人工100/50归属保持不变。最终已分170/未分0/各期未结30/订单未收30、逾期0，各消费者一致。独立SQL确认两期paid/partial、人工100/50与新增auto20，未隐式重分配原人工记录。
- 独立AsyncPG连接还核对四条update_allocations审计：before/after分配合计依次150→150、150→50、50→0、0→150；操作人、理由、manual模式齐全，非法请求没有多写成功审计。证据ops/backups/reallocation-consumers-20261029/含真实HTTP脚本/阶段输出、SQL结果、独立审计校验脚本与结果。未新添镜像单元测试或重复未变应用全量测试，前轮1435项结果仍为当前应用代码的最近完整单元证据。
- 本轮没有发现新确认Bug或新增应用修改；本机API8031/PG55439已停止，无SSH、生产业务操作、提交/推送/部署，Q-063继续本地未发布。没有声称浏览器表单操作、真实第三方网络故障或任意并发锁图已验。
- 下一步：该正常分配统计链已有多类新证据，不继续重复同类金额案例；转向尚未充分验证的其它业务链或真实页面缓存/错误反馈边界，按代码证据选定方向。历史非法分配/取消且已确认款组合仍未清洗，业务口径不擅自改变，长期目标继续。

## 2026-10-29 应收/回款状态链真实HTTP与统计一致性补验（未发现新缺陷）

- 上轮Q-063真实复现、局部计数修复与回归为实质进展。本轮重读完整长期目标、质量目标/进度及当前工作区，保留Q-063与用户原12项文档删除；选择不同的新验证场景：资金状态变化后，应收责任汇总/账龄分析是否同步、取消保护是否挡住异常组合。
- 静态疑点排除边界：payment_stats的付款方式汇总未显式过滤cancelled订单，不能直接推断正常流程会统计已取消订单的确认回款；order.cancel明确在订单锁内拦截任何已确认回款。通过真实HTTP确认已收齐订单取消422，状态与统计不变，故不把缺少条件本身记为新Bug。历史脏组合是否需要过滤属于未验证范围，本轮不造非法数据或修改口径。
- 环境在启动前再次核对DATABASE_URL仅127.0.0.1:55439/crm_iso_local_e2e_20261027_test、第三方推送关闭、调度关闭。只用本机正常API合成CNY订单/计划/回款，无生产或第三方副作用。
- 真实HTTP八阶段：订单21/计划13金额100、已过期，计逾期1/100；登记待确认40后仍1/100；确认后1/60；第二笔60待确认仍1/60，确认收齐后0/0且计划paid。应收责任统计与账龄统计每一步计数一致，订单资金概览实收40/未收60正确。已确认回款后取消订单422，统计保持；订单22/计划14金额50只有待确认回款，取消后订单/计划cancelled、回款14 rejected，统计移除该1/50。
- Q-063新增交错：订单23/计划15金额80、未来到期，初始不计逾期；正常mark-overdue后1/80，确认30后重算仍1/50，确认剩余50后0/0。验证人工逾期标记在未来日期且部分收款的重算中仍保留、已收齐时移除，未把仅初始标记验证扩大成全部生命周期。
- 独立SQL核对最终订单/计划状态、两笔100分拆40+60、未来80分拆30+50，已取消订单的待确认款驳回且没有分配残留；首轮较短脚本数据订单19/20也独立留证，扩展轮21/22/23用于上述完整结果。没有清空隔离库或把生产数据复制进测试。
- 最终含Q-063补丁的完整后端单元1435 passed in 14.49s，diff检查通过；未新增应用改动或镜像测试。证据ops/backups/receivable-lifecycle-20261029/含正常HTTP可复现脚本、阶段断言输出、独立SQL及单元结果。专用本机API8031/PG55439已停止，无SSH/生产数据操作、提交、推送或部署。
- 边界/下一步：本轮是实际接口/PG、无真实浏览器操作或第三方故障注入；金额统计的历史异常组合、人工重新分配后的跨节点余额仍可继续独立审查。Q-063保持本地未发布，线上仍以d71d711为上次核实基线；不把本轮无新Bug或1435项通过当作长期目标完成。

## 2026-10-29 恢复审查，Q-063 人工逾期节点在回款分析漏计（本地已修复）

- 用户再次明确“继续”，恢复长期质量审查，上一轮交付/同步/停止为实质进展；本轮重读长期原目标、AGENTS、质量文件与Git现状。只读SSH核对前后端仍为d71d711，三个前轮相关文件哈希与该提交对应。用户原12项文档删除保留，无新的应用工作区改动被覆盖。
- 选择尚未充分验证的跨模块应收/回款分析口径。Q-063/P2已确认：analytics.payment_stats按共用is_plan_overdue已把未来到期但人工overdue的节点余额计入overdue_amount，节点计数却取逾期天数区间之和；未来/今天的人工逾期不在1天以上区间，导致“金额增加但节点数不增加”。AnalyticsPage直接显示该字段为“逾期节点N个”，不是仅未用内部字段。
- 真实隔离HTTP复现：正常CNY订单18生成计划10，到期日2026-11-08，金额100；经正常mark-overdue接口人工标记。analytics/receivables逾期数增加1，analytics/payments金额增加100但overdue_node_count仍0。独立SQL确认节点状态overdue、到期日未来、金额100。服务回归修前断言0!=1失败。没有到期日按共用规则本就不算逾期，未错误扩张此规则。
- 唯一一致性修复：payment_stats在原共用判据成立时，和金额一起递增节点数，返回此计数；原账龄分档、状态/余额规则、权限/负责人范围、金额和付款方式统计不变。仅补计数遗漏，无新查询、数据库结构、锁、依赖或产品交互，无业务取舍，按既有规则自主修复。
- 验证：参数化覆盖未来人工逾期、当日人工逾期、过去待收、未来待收、取消五组，均与receivable_stats的共用口径一致；allocation_chain与payment_consistency共39项通过。相同真实计划10修后GET payments节点数1，金额增量仍100；修复没有改动该计划。最初想在数据库夹具中构造无到期日，但实际due_date非空约束拒绝，已去掉这个不可写入夹具而保留共用判据不变，未放宽业务断言。应用pyflakes和diff检查通过。
- 只读SSH补核：服务器analytics.service SHA256 a8378bd4e43fd537220f32b735be388b9a372cccbe8d59292fc21a80d22c8a91，与d71d711源码完全一致，确认线上同源问题；未在生产造数据。页面字段消费只做代码调用链核对，未做真实浏览器操作或宣称生产角色端到端回归。
- 证据ops/backups/manual-overdue-count-20261029/。本机专用API8031/PG55439已停止，第三方推送/调度保持关闭；本轮无生产写入、提交、推送或部署，线上仍为d71d711。10-28发布授权已用于前批，不自动用于新Q-063。
- 范围/下一步：账龄区间仍按实际逾期天数划分，未来人工逾期只计总数/金额而不虚构天数；未增加新图表分类。继续核对取消/重开/部分收款与回款分析的其它消费者一致性，不因39项通过宣称整个资金统计无问题。长期目标继续，前轮停止状态由本次恢复说明覆盖。

## 2026-10-28 收尾发布完成，按用户要求停止后续审查（d71d711）

- 用户明确要求当前批次做完后停止，服务器确认问题后部署并同步Gitee/GitHub。本轮只读核实实际应用265cb11及三个相关后端文件固定提交哈希、491发布文件一致，逐项确认Q-053/Q-055至Q-062缺陷代码仍存在；Q-054按用户选择保持差额可见。没有在生产构造测试数据。
- 固定应用提交d71d7113616259d2921ca4b38854ba2230809b96包含当前全部已验证补丁、回归及质量记录；用户原12项文档删除未纳入。GitHub/Gitee原pending-acceptance分支均已同步该提交。无依赖/迁移/正式配置/权限范围/历史数据清洗。
- 验证：累计后端单元1430项通过，21项发布安全测试通过；固定SHA独立生产构建、固定发布包完整隔离浏览器回归通过，含费用/数量/单价/毛利差额精确展示与既有转单/订单/回款断言。同SHA CI 40671982400在apply前completed/success，前端冒烟和后端全量接口回归全部成功：https://github.com/ytsbx/crm-sales-agent/actions/runs/40671982400 。gh watch最后因api.github.com连接中断退出1，独立run view已在apply前取得完整成功状态并归档，不把观测断网当CI失败或跳过检查。
- 发布包SHA256 18c2dcd7f2a781113b1b122e2f395d0b7a58c439b555d1c23c9fadc2803b36af；工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab，远端逐一匹配。统一入口preflight通过后apply，返回verified，回滚备份/opt/crm-sales-agent/backup/unified-d71d71136162-ulubmymy。
- 发布后独立复核：DEPLOYED_COMMIT/WEB_DEPLOYED_COMMIT均为完整d71d711，RELEASE.json verified；491文件全部哈希匹配；服务器当前批次修复标记存在；服务active/running、NRestarts=0，后端health up/database ok，钉钉OA同步与Stream连接正常，公网首页HTTP200。未把文件/健康检查冒称真实生产角色全流程验收。
- 证据ops/backups/release-final-20261028/，包括服务器前后核对、固定包构建/清单/浏览器、发布安全与单元记录、同SHA CI最终状态、预检/apply及独立postcheck。此前各项“本地未发布”是历史阶段，由本条覆盖。
- 当前批次交付完成；按用户明确要求停止后续新问题审查，不自动继续扫描/修复/发布。长期质量目标未宣称全部完成，尚未覆盖范围仍保留在历史记录，只有用户再次要求时恢复。用户原文档删除保持工作区原状。

## 2026-10-28 当前批次收尾发布准备（用户要求完成后停止审查）

- 用户明确要求“做完这个不做了，确定服务器上的代码确实有你发现的问题就修改部署，并同步gittee，和github”，授权当前累计Q-053、Q-055至Q-062修复的核对、提交/两仓同步及生产发布；完成收尾后停止发现新问题，长期目标不冒称全部完成。
- 只读SSH确认服务器两端基线265cb117c62b75bebb0c792df56d4e5729e8579e，报价service/router与pricing.service三个文件SHA256与该固定提交一致。服务器逐项存在浮点费用判据、浮点利润差额、明细缺exact、利润率/底价/分项合计缺写入前guard、两条查价浮点回写、手工改价来源未清空，确认全部已复现问题同源；当前RELEASE.json verified，491文件逐项匹配，未在生产造测试业务数据。
- 发布范围仅累计已验证修复、测试和质量文档；Q-054按用户选择不隐藏差额，原12项文档删除不纳入提交。保留最终1430项后端单元通过的实际记录与前轮真实隔离HTTP/PG证据，21项发布安全测试通过，固定包/同SHA CI待后续完成后再apply。无依赖/迁移/正式配置或历史业务数据修改。
- 本条仅准备记录，不能据此宣称已上线。证据ops/backups/release-final-20261028/，最终发布结果/回滚备份与独立复核随后补记；完成后按用户要求停止审查。

## 2026-10-28 Q-061 统一查价写入精度、Q-062 手工改价被刷新覆盖（本地已修复）

- 上轮Q-060新建快照精度修复及真实PG/HTTP为实质进展。本轮重读长期目标、质量文件与工作区，转查不同调用链lookup_applicable_price→商机建报价/漂移检测/刷新价格，以及单行改价来源标记。既有有效补丁、Q-054用户例外和用户原12项文档删除保留；无生产访问。
- Q-061/P2真实HTTP确认：正常客户25专属价规则8为999999999999.9992，商机录入SKU1数量100后生成报价48/版本55，实际单价.9991、总额.91；price-refresh及cost-refresh后仍错误。Q-060只修calculate_price原值通道，这些路径先用lookup_applicable_price的float作为手工入参，损失已经发生，不能靠后一步恢复。
- Q-061修复：lookup_applicable_price增加可选内部snapshot_out，客户专属价、等级/通用指导价分别输出原始Decimal unit_price；pending初始为None。商机生成报价、价格漂移检测和刷新价格使用此精确值进行原有转换/比较，不增加公开字段，不改取价顺序、公海专属价规则或既有漂移阈值0.01。复用Q-060同类内部通道，不新增查询、依赖、权限、界面、数据库列或锁。
- Q-062/P2真实HTTP确认：同一系统带价明细正常PATCH为100元，再price-refresh，原价被覆盖回大额系统价；SQL确认price_source仍customer_specific。update_item重建快照并复制售价，却未在实际手工改价后清除来源标记，refresh_prices依赖该标记区分是否可覆盖。这违反函数/路由明确既有“手工价不覆盖”规则，不是新业务选择。
- Q-062修复：改单价请求确实改变原Decimal价格时，SKU行price_source改None；仅改数量且携带相同售价、仅改备注均保留原系统来源，定制行来源不变。手工价以后按既有刷新逻辑跳过；不回溯批改历史来源，不改变手工价的审批、成本刷新或数据范围。
- 新增服务回归：实际单行改价/不变售价与数量/仅备注三组，精确统一查价通用/客户专属两组；修前实际改价标记断言失败、查价缺内部精确输出。修后相关118+5=123项通过；补pending/approved×price/cost四组实际路由拒绝与原快照不变断言，最终完整后端单元1430 passed in 14.13s。首次编辑脚本将lookup片段缩进错误，测试收集即时失败；修正缩进后才运行上述绿测试，未部署错误中间版本。
- 真实修后HTTP：客户26规则9、报价49/版本56从商机、刷新价格、刷新成本各阶段均单价.9992/总额.92，source保持customer_specific。手工改单价100后刷新refreshed=0/skipped=1，SQL确认price_source=null、单价100、总额10000。锁版保护由服务/路由用例覆盖，未用SQL伪造审批当真实HTTP证据。
- 额外真实隔离事务补验：版本57两行同SKU售价100和0.0020、原成本20；通过正常成本PATCH临时改为30后cost-refresh，第二行亏损率超范围返回422，前面已经重建的第一行也回滚；重新GET独立比较两行ID/售价/成本/利润/利润率及总额均与刷新前一致。finally经正常API恢复隔离成本原四项并重新读取核对，未改生产成本。
- 应用pyflakes与diff检查通过；本轮无前端应用变更，未重复前端构建/浏览器。证据ops/backups/quote-refresh-integrity-20261028/含前后HTTP、SQL、服务/全量结果、回滚探针和脚本。专用本机API8031/PG55439已停止；无SSH、提交、推送或部署，线上仍为已验证265cb11。
- 残余：既有漂移提示按0.01阈值不变，不能据此称微小变化均会提示；其它调用统一查价的模块尚未逐个验证精度，不因本轮三个报价入口修复泛化。历史手工改价来源不清洗。下一步转向其它模块取价或资金统计链，累计本地补丁待后续发布授权与固定提交/CI/服务器核验，长期目标继续。

## 2026-10-28 Q-060 自动取价落库经过浮点造成四位单价/总额变化（本地已修复）

- 上轮Q-059底价校验及真实PG/HTTP、累计回归为实质进展。本轮重读完整长期目标、质量目标/进度及现状，追踪pricing.calculate_price到quote.build_item_snapshot。保留既有补丁与用户原12项文档删除，Q-054例外不扩大，未生产访问或部署。
- Q-060/P2真实隔离HTTP/PG确认：正常新建客户23、其SKU1专属价规则6（agreed_price/minimum_price=999999999999.9992），报价46/版本53自动取价，数量100。输入未传quoted_price，接口返回quoted_price_exact=999999999999.9991、total_amount_exact=99999999999999.91；独立SQL确认规则保持.9992，但实际报价、建议价/底价快照均.9991。应为.92的总额少一分，已经是持久化准确性问题，区别于先前只读显示路径。
- 原因：calculate_price用Decimal正确计算后，以_f把价格/成本/底价等转成公开响应float；build_item_snapshot又用Decimal(str(...))拿这些响应值写入，无法恢复原值。新报价审批信息虽从原始核价计算，但写入金额已变化；本轮未声称已证明额外审批绕过。
- 唯一精度修复：calculate_price增加仅内部调用可传的snapshot_out容器，计算完成后写入原始Decimal的商品成本、包装/运费、标准/建议价、人民币底价、计价成本、退税；不加入公开响应。build_item_snapshot直接使用这些同一次核价的定点值写价/快照与计算利润。自动取价仍要求既有指导/约定价，手工价格优先、成本完整性/来源/审批触发、字段范围/舍入和人民币规则保持，无新增查询、权限、字段、依赖、锁、迁移或历史数据改写。修正中间表示损失，不更改取价业务策略。
- 服务回归验证自动价、标准/建议/底价快照均等于.9992，公开返回仍为float且不含内部容器。初版用SQLite持久化大额价格规则，SQLite自身Numeric先丢精度，修后仍失败；改为原始Decimal规则查价替身，实际核价/快照构建继续执行。没有把该替身当真实存储证据，真实来源/目标写入另由PostgreSQL验证。Q-059模拟超底价的测试同时修改新的内部输出，保持原拒绝断言，不删除/放宽测试。
- 真实修后HTTP：新客户24/规则7、报价47/版本54同值输入，精确单价.9992、总额.92正确。独立SQL并排核对规则6/7同为.9992，修前版本53为.9991/.91、修后54为.9992/.92，建议价与底价快照同步准确。普通业务员与管理员POST /pricing/calculate保持原公开字段与成本脱敏；内部值容器未出现在响应，既有price:manage边界保持。
- 相关报价/成本/定制/核价脱敏回归118项通过，最终完整后端单元套件1421 passed in 14.10s；应用pyflakes与diff检查通过。无前端应用改动，本轮未重复前端构建/浏览器，未扩称所有真实API/PG或生产端到端完成。证据ops/backups/quote-pricing-snapshot-exact-20261028/含HTTP前后、SQL、公开接口验证、服务/全量结果和脚本。
- 专用本机API8031/PG55439已停止；无SSH、提交、推送、部署，线上仍为已验证265cb11，本地累计Q-053/Q-055至Q-060待后续当次发布授权、固定提交/CI及实际服务器核对。未清洗旧报价，已锁定历史快照不自动重算；未穷尽标准/建议价衍生超列范围与其它资金分析链。下一步转向刷新价格/成本与既有快照的重建边界或其他未覆盖业务模块，长期目标继续。

## 2026-10-28 Q-059 派生底价快照超范围报500（本地已修复）

- 上轮Q-058费用分项校验及累计回归为实质进展。本轮重读完整长期目标、质量目标/进度及Git现状，核对核价返回到quote_items快照写入链；保留既有补丁、Q-054用户可见范围例外、用户原文档删除。未部署或修改生产。
- Q-059/P2真实隔离HTTP复现：正常创建定制需求6，成本与拟售价均999999999999.9999、数量1（各自在Numeric(16,4)输入范围内），从需求生成报价500；日志明确minimum_price_snapshot=1000000000000.00超Numeric(16,4)，利润率0正常。原因是build_custom_item_snapshot按既有两位小数计算底价进位，派生值未核对实际快照列范围。失败后需求仍open，没有将“输入合法”误认作全部派生值一定可保存。
- 最小校验补齐：增加_assert_floor_in_range，对最终四位存储值检查绝对值<1e12，超限422“底价超出可保存范围，请检查成本和价格权限配置”。定制计算、SKU读取核价minimum_price_cny、当前口径重算三处使用；未改变底价公式、舍入位数、权限、快照列范围、旧数据、依赖或锁。SKU只提取原先即要写入的底价值，不改变核价来源。此为不可写入值的唯一明确参数校验，沿用现有存储规则自主修复，不选择扩列或截断/降低保护价。
- 红绿：底价上限/舍入进位四组及实际重算用例修前失败；补SKU派生底价超限的服务路径验证。相关报价/成本/定制及边界测试110项通过，累计完整后端单元套件1420 passed in 13.72s。SKU超限测试使用真实核价链并替换返回底价值，不称真实PG配置大成本复现；重算为服务验证。
- 真实HTTP修后同边界需求7返回422与明确提示、仍open；合法对照需求8成本/售价999999999999.9900生成报价45/版本52成功、需求converted。独立SQL核对成本/售价/底价均999999999999.9900、利润0.0000；证明没有把所有大额报价一概拦住。定制HTTP拒绝/对照为真实PG，其它两调用路径仅服务回归；不扩称全系统边界已穷尽。
- 应用pyflakes及diff检查通过；本轮无前端应用变更，未重复前端构建/浏览器测试，不将前轮证据写为本轮重跑。证据ops/backups/quote-floor-range-20261028/含HTTP前后、合法对照、SQL、异常日志、红绿及全量结果、可复现脚本。专用本机API8031/PG55439已停止；无SSH、提交、推送、部署，线上仍以265cb11为已验证应用基线。
- 残余风险：核价数值返回float再写入快照的精度、标准/建议价等其它派生列组合上限仍待独立调查；本轮只修底价超存储范围的已确认异常。下一步转查这些派生值或其他资金统计链，避免重复已修变更文字路径。Q-053/Q-055/Q-056/Q-057/Q-058/Q-059均仅本地待后续当次发布授权及固定提交/CI/实际服务器核对，长期目标继续。

## 2026-10-28 Q-058 费用/折扣合计超存储范围报500（本地已修复）

- 上轮Q-057经用户批准修复、真实接口与回滚验证属于实质进展。本轮重读原始长期目标、质量目标/进度与工作区，保留所有既有有效补丁和用户原12项文档删除；未扩大Q-054权限例外或Q-057数据库范围。
- 从组合金额存储检查发现recalc_version只查货款逐行/小计及最终总额，未检查同为Numeric(16,2)的charge_amount/discount_amount等拆分合计。Q-058/P2真实HTTP复现：隔离报价39先加费用60000000000000、折扣-60000000000000，均正常；再加同额费用时净总额60000000000000仍在范围，但费用合计120000000000000超列范围，500。报价40反向先折扣/费用再折扣，折扣合计-120000000000000同样500。日志明确Numeric(16,2)溢出，非静态推测。失败后两条费用与零总额完整保留，独立SQL核对，无半次新增。
- 唯一存储校验补齐：recalc_version在给版本赋值前，复用已有_assert_money_in_range校验附加费用合计、运费合计、其他费用合计、折扣合计。不改变原列范围、费用分类/折扣语义、总额公式、锁或事务、权限、依赖或历史数据；不可保存的值本来就失败，现在明确422而非500，属于同一金额范围既有规则的遗漏，自主修复。
- 红绿回归：新增正费用/负折扣合计两组修前未抛AppError、修后通过；合法正负60000000000000抵消且各合计可存的对照继续通过。报价/成本/定制及边界相关104项通过。真实隔离报价41/42重复同一流程，超范围分别422“附加费用合计/折扣合计超出可保存范围”，失败后各两条费用、charge=60000000000000、discount=-60000000000000、total=0.00由独立SQL确认。运费单条唯一且其它费用非负的正常路径也共用该检查，本轮未声称分别构造全部历史异常合计。
- 累计Q-053/Q-055/Q-056/Q-057/Q-058补丁完整后端单元套件1414 passed in 13.54s；套件使用现有内存/替身，不连接生产，不等同所有真实HTTP/PG或CI套件。应用pyflakes与diff检查通过；本轮仅后端改动，前轮前端构建/浏览器证据沿用，不冒称重新执行。证据ops/backups/quote-charge-aggregate-20261028/，含真实HTTP前后、日志、SQL、专项/全量结果及脚本。
- 本轮未SSH、提交、推送、部署或改生产业务数据，本机专用API8031/PG55439已停止。线上仍为已验证265cb11，本地累计补丁待后续发布授权与固定提交/CI/服务器基线核对。未穷尽成本/利润其它列上限、正式文件/分析统计等金额路径，长期目标继续；下一步转查核价快照组合上限或其他资金统计，不将本轮全绿视为全项目完成。

## 2026-10-28 Q-057 亏损率超存储范围改明确提示（用户批准、本地已修复）

- 上轮Q-056显示修复与Q-057实际500取证为实质进展。本轮重读长期目标、AGENTS与质量记录，核对既有Numeric(10,6)模型和初始迁移，未调整数据库。Q-053/Q-055/Q-056及用户原文档删除保留。
- 补充真实隔离HTTP：成本20、价格0.0001在单行PATCH、整版POST均500；整版先删除原行、再处理一条合法101和一条非法低价，失败后原行ID/售价100/总额100完整恢复，无半批新增。价格0.0020（利润率-9999）可保存，0.0019仍500，失败保留0.0020。正常定制需求4成本20/价格0.0001生成报价也500，需求仍open；这些是额外入口和边界的新证据。数据库健康，未改生产。
- 已说明可选方案：保留现有存储范围并明确拒绝超范围输入，或扩大列支持极端亏损率（涉及迁移与业务边界）。用户明确选择“明确提示，保留存储范围（推荐）”。本次仅执行该选择，不扩大列、不截断/钳制利润率、不改正常低价审批规则。
- 修复：quote.service增加共用_stored_profit_rate，沿用原六位小数量化后检查绝对值<10000，超范围返回PARAM_ERROR/422“亏损率超出可保存范围，请调整售价或成本”；SKU快照构建、定制快照构建、当前口径重算均调用。保持原舍入/利润快照算法、既有事务锁与回滚边界，无迁移、依赖、配置、权限或历史数据修改。
- 回归：新增六组可存边界、四组超范围（含舍入后进位溢出）、实际SKU快照拒绝及重算正反两例，相关报价/成本/定制对比测试共101项通过；修前新增边界/构建用例失败。真实HTTP单行/整版/定制三入口修后均422及准确提示，价格0.0020仍200；原明细ID/价格/总额和需求状态仍保持，独立SQL复核通过。重算入口由服务测试覆盖，未声称已穷尽所有刷新HTTP。后端应用pyflakes、diff检查通过。直接pyflakes测试文件报告既有导入pytest fixture的unused/redefinition模式（新增case参数亦沿用该模式），未将其说成无警告通过，未为清警告改变fixture组织。
- 证据追加ops/backups/quote-profit-rate-overflow-20261028/，含前后HTTP、SQL、回归及可复现脚本。本轮仅后端变更，前端未变；前轮构建/浏览器证据保留，本轮不冒称重新执行前端构建。专用本机API8031/PG55439已停止，无SSH、提交、推送或部署；线上仍为已验证265cb11，Q-053/Q-055/Q-056/Q-057均本地未发布。
- 残余范围：此次覆盖低价亏损率存储溢出，不代表成本/利润其他Numeric列与全部聚合路径已穷尽。下一步审查成本快照与利润金额的组合上限、现有金额范围guard是否在写入前覆盖，或转向其他高风险资金统计链；不重复修改已验证版本变更显示，长期目标继续。

## 2026-10-28 Q-056 报价变更明细显示相同值、Q-057 低价利润率溢出留证

- 上轮Q-054人工选择记录、Q-055真实复现/修复/回归为实质进展。本轮重读长期目标、质量目标/进度、Git现状，继续尚未核对的数量/单价变化记录。Q-054按用户决定保留，不再提请隐藏；Q-053/Q-055和用户原12项文档删除保持。
- Q-056/P2确认：version_comparison已按Decimal检出明细变化，但changes.before/after仍只传float。合法单价999999999999.9991/.9992，数量9999999999999.988/.989各自转换后相同，页面会显示“相同值→相同值”，无法说明真实改动。新增/删除行的价格也只传float。四组新增服务回归修前缺精确值失败；不是变化漏检，与Q-053费用判据问题分开。
- 真实隔离HTTP/PG：正常创建报价33两版单价.9991/.9992，报价34两版数量.988/.989、单价1；修前接口已检出对应field，但before==after。独立SQL确认原数量/单价不同且精确保存，没有通过SQL篡改构造非法值。
- 最小修复：给现有单价、数量、增加/删除行的变更记录补before_exact/after_exact，空侧保留null；旧数值字段保留。复用Q-053已改好的页面exact优先逻辑，无新增界面/操作、查询、依赖、数据库结构、权限、历史写入或金额口径变化，唯一展示准确性修复。
- 验证：报价/成本生命周期/混合定制及重复SKU对比等相关测试88项通过，覆盖新建/删除精确值和原明细匹配行为；真实HTTP同一报价33/34返回正确exact变化。TypeScript/生产构建、完整隔离假API浏览器回归通过，新增两个实际DOM变更文字断言，原费用/毛利差额/转单/订单/回款断言继续通过；diff检查通过，既有构建警告保留。证据ops/backups/quote-item-diff-20261028/。真实HTTP覆盖价格/数量变更，增加/删除由服务回归覆盖，未扩称完整生产端到端。
- Q-057/P2异常已复现、方案仍待调查：初次为数量边界选用SKU1、价格0.0001，正常API返回500，日志明确profit_rate_snapshot=-199999.000000超过Numeric(10,6)存储范围。随后同一隔离草稿37改数量为1、相同单价0.0001，仍500，证明并非大数量独有；独立SQL确认失败后item_count=0、total_amount=0.00，数据库健康，未留半批明细。此为真实应用异常，不将它误记为探针错误。
- Q-057代码链：build_item_snapshot、build_custom_item_snapshot与既有reprice函数直接量化利润率而未核对列范围；目前只证明SKU批量写入HTTP入口的500，其他入口为待调查，不能凭共享字段扩称都已复现。尚未决定拒绝超列范围的价格并返回明确提示，还是支持该极端亏损率并扩展存储；后者涉及数据库结构/业务边界，不能擅自实施。下一步核对现有报价范围约束、编辑/整版保存回滚和合理修复方案后按项目规则决定是否需要人工选择。证据ops/backups/quote-profit-rate-overflow-20261028/。
- 本轮未SSH、提交、推送或部署；本机专用API8031/PG55439已停止，线上仍按265cb11已验证基线。Q-053/Q-055/Q-056均仅本地；毛利率、其他金额汇总未穷尽，长期目标继续，下一轮优先Q-057实际异常，不重复扫描已修显示路径。

## 2026-10-28 Q-054 差额成本信息例外确认、Q-055 毛利差额精度修复（本地）

- 上轮Q-053真实费用复现、代码修复与回归属于实质进展。本轮重读完整长期目标、质量文件及当前工作区，保留Q-053和用户原文档删除；无生产访问、提交、推送或部署。
- Q-054/P1已确认、用户决定保持现状：真实隔离普通业务员创建报价29，管理员录入同SKU数量1、两版单价1000/1100。业务员GET对比中逐版cost_total/profit_total/margin均null，但diffs含profit_delta=100、margin_delta=0.001818，与管理员一致。管理员逐版成本20、毛利980/1080；利用报价和毛利率差额可近似反推成本20。代码strip_comparison_cost只处理versions，docstring原称diffs不含成本，实际上并非如此；既有check_batch7_repairs只有单版夹具，未覆盖非空diffs。未把单版绿测试当无泄露证明。
- 已向用户说明风险并建议按现有成本权限隐藏差额；用户明确“保持现状”。不实施隐藏、不把该项记录为已修复；仅修正文档注释并记录QUALITY_GOAL的窄范围例外。此决定不扩大版本汇总/报价明细成本或其他权限。修后实际业务员HTTP验证逐版成本仍null、差额仍可见且精确字段正确。证据ops/backups/quote-cost-diff-20261028/。首次临时探针把unit_cost输入当作主数据成本覆盖，实际被现有快照机制保持，差额为零；改用正常单价变化复现，不把初次探针失败记为应用问题。
- Q-053补充：真实HTTP正常创建报价30，产品总额99999999999999.99，两版经正常费用API录入折扣-99999999999999.98/-99999999999999.99，修后正确检出discount_amount及exact原值变化。补齐上轮仅正费用有真实HTTP、负折扣只有单测的证据边界；没有更改产品总额/折扣口径。证据追加原quote-fee-comparison目录。
- Q-055/P2已确认并本地修复：version_comparison的profit_delta仍用浮点profit_total相减。正常流程生成的隔离报价26三版真实PG利润99999999997999.9800000/.9900000/.9800000；修前真实HTTP正反差额均0.0，而实际应为+0.01/-0.01。新增五组服务回归覆盖大额正反/负利润、普通及零差额；修前大额数值断言失败，普通/零例仅失败于缺exact字段，不把普通值误报为旧算法错误。
- Q-055修复：保留同一次查询已算的Decimal利润汇总用于差额计算，继续归一两位小数；旧profit_delta数值返回正确结果，增加profit_delta_exact，页面优先精确值并保留旧接口回退。不改利润快照计算、舍入位数、费用口径、权限可见范围、查询数、数据库结构或锁；唯一准确性修复，无新增依赖，按授权直接处理。
- 验证：64项报价/成本生命周期测试通过；同一报价26修后真实HTTP精确及旧数值差额均+0.01/-0.01，独立SQL核对原利润快照不变。TypeScript/生产构建、完整隔离假API浏览器回归通过，新增“毛利 +0.01，毛利率”DOM断言，既有费用exact、报价转单、订单与回款断言通过；diff检查通过。既有构建警告保留。证据ops/backups/quote-profit-delta-20261028/。
- 本机专用API8031与PG55439已停止。线上仍为已验证265cb117c62b75bebb0c792df56d4e5729e8579e，本轮Q-053/Q-055仅本地未提交。尚未穷尽毛利率精度、逐版成本/利润展示及其他模块金额路径；Q-054明确保留，不再重复提请隐藏。下一步选择尚未验证的数量/单价变更显示或其他资金统计路径；生产发布仍需当次明确授权及固定包/CI/基线核验。

## 2026-10-28 Q-053 版本对比漏报费用/折扣一分钱变更（本地已修复，未发布）

- 上轮Q-052发布及独立线上复核属于实质进展；本轮重读长期目标、质量文件、Git现状，继续尚未验证的费用变化判据。用户原有12项文档删除保留，未重复修改已发布差额算法。
- Q-053/P2已确认：version_comparison先把charge_amount/discount_amount转float，再据此判断是否变化。费用99999999999999.98与99999999999999.99，或负折扣对应值，float相同，changes为空，页面提示“明细与费用没有变化”。内存真实服务回归中大额两例修前失败于未检出变化，普通金额对照仅失败于缺exact字段，不将普通金额误报为漏检。
- 真实隔离PostgreSQL/HTTP复现：正常创建商机/报价27，先录入费用99999999999999.98，新建V2后经正常删除/新增费用接口改为99999999999999.99。GET版本对比逐版费用amount_exact不同，但diffs.changes=[]；独立SQL确认两版charge_amount与total_amount准确.98/.99。没有直接篡改数据库制造数据，未访问生产。
- 最小修复：变化判据直接比较已查询版本的Decimal原值；变化记录增加before_exact/after_exact，旧数值字段保留；已有费用变更文字优先exact展示、缺字段回退。没有新增查询、依赖、数据库结构或锁；未改变费用分类、产品金额差额不含费用的既有口径、历史金额、权限或操作流程，属于确定遗漏的纯准确性修复。
- 验证：六组新增费用/折扣回归覆盖大额一分、普通一分、不变；报价/成本生命周期相关测试59项通过。相同隔离报价27修后真实HTTP准确检出charge_amount变化，exact原值.98→.99；旧float字段仍保持兼容。TypeScript与生产构建通过，完整隔离假API浏览器回归通过，实际DOM断言精确变更文字，同时既有报价转单/订单/回款断言通过。py_compile与diff检查通过，既有构建警告保留。
- 证据ops/backups/quote-fee-comparison-20261028/。真实HTTP覆盖正费用，负折扣由Decimal服务回归覆盖；浏览器为假API，未扩称生产真实账号端到端。本机专用API8031与PG55439已停止。未SSH、提交、推送或部署，线上仍为265cb117c62b75bebb0c792df56d4e5729e8579e。
- 残余范围及下一步：利润差额/毛利率、对比成本派生数据脱敏及报价逐项数量/单价文字仍需独立核对；不以本轮费用修复证明这些路径无误。全项目长期目标保持进行中；本轮新修复如需发布，先按当次授权核对实际服务器基线、固定包与同SHA CI。

## 2026-10-28 Q-052 已发布（265cb11）

- 按用户“服务器有这个问题就修改部署”完成只读核对后发布。实际服务器报价服务与7137866基线哈希相同，确认仍用浮点差额；应用固定提交265cb117c62b75bebb0c792df56d4e5729e8579e已同步GitHub/Gitee，不包含用户原有12项文档删除。
- 53项报价/成本测试、21项发布安全测试、固定提交生产构建和固定包完整浏览器回归通过。补充真实本机PostgreSQL/HTTP：正常创建报价26，三版产品金额依次99999999999999.98/.99/.98，接口exact差额及旧数值字段均准确返回+0.01/-0.01；独立SQL核对三版实际产品金额一致。隔离数据库/API已停止，未在生产造测试数据。首次临时探针把quote_id误写id、独立SQL误用表名，修正探针后重跑通过，属于取证脚本错误，非应用失败；PG首次未指定端口启动失败，按专用55439重启成功，没有操作占用5432的其它实例。
- 同SHA CI 40662586282在apply前completed/success，前端冒烟与后端全量回归均成功：https://github.com/ytsbx/crm-sales-agent/actions/runs/40662586282 。既有构建/CI平台警告保留。
- 发布包SHA256 f5e1bc820c5d9a04df2195ea04d72c13ac051c69f101a7366de03bafe6888acc，工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab，远端均匹配；统一入口preflight通过再apply，返回verified，回滚备份/opt/crm-sales-agent/backup/unified-265cb117c62b-bt_hhhok。无数据库迁移、依赖升级、配置、权限或历史业务数据修改。
- 发布后独立复核：前后端版本均为完整265cb117c62b75bebb0c792df56d4e5729e8579e，RELEASE.json verified，491个清单文件哈希全部一致，服务器已有amount_delta_exact；服务active/running、NRestarts=0，数据库健康，钉钉OA/Stream正常，公网首页HTTP200。
- 全部证据归档ops/backups/release-q052-20261028/。以下Q-052“本地未发布”为历史阶段，由本条覆盖。未执行真实生产账号有数据浏览器操作；费用变化判据、利润差额/毛利率及对比脱敏仍需独立审查，本次不代表全系统金额问题穷尽。下一步继续这些未验证边界，涉及业务/权限口径先记录再决定。

## 2026-10-28 Q-052 服务器确认及发布准备

- 用户明确“服务器有这个问题就修改部署”，授权本次发布。只读 SSH 确认实际前后端基线均为7137866c2df2544838c29c607c7f7a93d88fdbbe；报价服务SHA256 c0ae6d90003839401ef37f6fb418825236eec09760f6cf88e927a9f1ada901ee，与该提交完全相同。实际运行源码仍以浮点amount_total相减，确认与本地已复现Q-052同源，未在生产制造测试数据。
- 重新运行53项报价/成本相关测试与21项发布安全测试均通过，diff检查通过。将只提交Q-052代码、回归和质量记录，保留用户原有文档删除；从固定提交构建并验证浏览器与同SHA CI后再apply。无迁移、依赖、配置、权限、业务口径或历史数据变更。
- 本条为发布准备，尚不能作为已上线证据；发布结果、备份路径及独立复核将后续补记。现有真实PG/HTTP验证边界仍按下条保留。

## 2026-10-28 Q-052 版本对比相差一分钱却显示零差额（本地已修复，未发布）

- 上轮 Q-051 固定提交/CI/生产发布与独立复核为实质进展。本轮重读原始长期目标、质量文件、现有工作区及下一步，不把发布完成等同长期目标完成。审查另一个调用链：`version_comparison` 的版本差额原先用已转浮点的 `amount_total` 相减，再 round(...,2)。
- Q-052/P2：两版数量均100、单价分别999999999999.9998和999999999999.9999，逐版产品金额分别99999999999999.98和99999999999999.99（均在现有精度范围），原差额却为0.0，页面显示+0.00。反向也丢掉-0.01。实际运行服务函数得到反例，新增回归修前失败；普通金额对照先通过原数值断言但失败于缺exact字段。非仅静态推测，未在生产制造数据。
- 唯一准确性修复：以 Q-051 保留的 Decimal 金额字符串相减，归一为两位小数；旧 `amount_delta` 数值字段返回正确计算结果，新增 `amount_delta_exact` 原值字符串；页面优先展示exact，无字段时保留旧toFixed回退。未调整产品金额/含费用金额口径、利润计算、权限、锁、数据库结构、依赖或既有业务流程，故在授权范围内直接修复。页面正负号仍用正确数值差额，支持±0.01。
- 验证：四组新增差额回归（大额正负一分、普通正一分、零差额）修后通过；报价与成本生命周期相关测试共53项通过。TypeScript/前端生产构建、完整隔离浏览器回归通过，新增真实页面DOM包含“金额 +0.01，毛利”的断言；既有列表、报价转单、订单/回款断言继续通过。构建既有警告保留，diff检查通过。
- 证据 `ops/backups/quote-comparison-delta-20261028/`。服务测试为内存 Decimal 对象/查询结果替身；浏览器是假API，没有声称真实PG/HTTP或生产端到端验证。本轮无SSH、生产写入、提交/推送/部署，线上仍以Q-051的7137866为已验证基线。
- 残余范围：费用变化判据、利润差额/毛利率及对比权限脱敏仍需独立核对；本轮不证明其正确。下一步优先补真实隔离PG/HTTP两版差额契约及检查费用变化判据，不重复扫描已修展示入口；长期目标继续。

## 2026-10-28 Q-051 发布完成（7137866）

- 用户明确“服务器有这个问题就修改部署”。只读 SSH 核对实际基线 `7df01541ec1c6c87b844ba94de9d3b4a1fc3978a`；报价 service SHA256 与该提交一致。当前 RELEASE.json 登记的 QuoteListPage-CyWKfAN8.js、QuoteDetailPage-9gpXW-iu.js 哈希匹配，分别仍以浮点显示列表金额与版本产品金额，确认线上同源问题；未在生产制造报价测试数据。
- 固定应用提交 `7137866c2df2544838c29c607c7f7a93d88fdbbe` 已同步 GitHub/Gitee 原分支；只包含 Q-051 只读精确字段、两个页面展示、回归及记录，用户原文档删除保持未提交。49 项相关后端测试、21 项发布安全测试、前端构建和固定包完整浏览器回归通过。
- 正式发布前，同 SHA CI 40659027356 completed/success，UI 冒烟和后端回归通过，链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40659027356 。期间 gh watch 两次 TLS 超时均为查询失败，随后独立读取同一运行确认成功；未在 CI 未完成时 apply。首次本机解包因系统 Python 不支持 filter 参数停止，改用项目 Python 3.12 安全解包后通过，没有改发布输入。最初取证匹配到 CSS 后查找 JS 字段报错，已限定 manifest 内 .js 重取成功，不以旧资源列表作为当前版本证据。
- 发布包 SHA256 `b6ce3527d67788fd037f48d44c2722d818678b5a20a46efe5f7d39a1e0d29305`；远端包/工具哈希一致，统一入口预检通过再执行 --apply，返回 verified，回滚备份 `/opt/crm-sales-agent/backup/unified-7137866c2df2-xu5ul9p6`。无依赖、迁移、配置、权限或业务数据修改。
- 发布后独立复核：两端版本均完整 7137866，RELEASE.json verified，491 个清单文件哈希匹配；服务 active/running、NRestarts=0，后端 health up/database ok、OA/Stream 正常，公网首页 HTTP200；服务器两个 exact 字段均存在。
- 证据 `ops/backups/release-q051-20261028/`；以下“本地未发布”为历史阶段，由本条覆盖。未做真实生产角色有数据页面回归，不把文件/健康核验说成端到端业务验收。下一步仍为版本差额计算与费用/成本/毛利显示的隔离审查，长期质量目标未完成。

## 2026-10-28 Q-051 报价列表与版本对比产品金额丢分（本地已修复，未发布）

- 读取质量目标与进度后继续 Q-050 明确留下的列表/版本对比路径。确认 `serialize_quote` 仅返回浮点 `current_version_amount`，`version_comparison` 用 Decimal 计算产品金额后仅返回浮点 `amount_total`。对合法金额 `99999999999999.99`，原列表 `toLocaleString` 显示 `¥99,999,999,999,999.98`，原对比 `toFixed(2)` 显示 `¥99999999999999.98`。本机执行原格式化表达式及真实服务函数证明误差，不以静态搜索作为唯一证据。
- 最小修复：增加 `current_version_amount_exact` 与 `amount_total_exact` 两个只读字符串字段，保留旧浮点字段；列表及版本对比产品报价金额优先使用精确字段，缺字段兼容原数值。对比列仍展示原有产品金额，不换成含费用总额；未改数据库、权限、金额计算/舍入、成本脱敏、滑杆测算或历史数据。
- 红绿验证：新增真实序列化与版本对比服务回归，修前两项失败于缺少精确字段，修后报价专项 29 项通过；覆盖无版本返回 null、对比产品金额 .99 及脱敏后成本仍为 null。对比测试使用内存 Decimal 对象和查询返回替身，没有声称实际 PostgreSQL/HTTP 验证。
- 前端 TypeScript/生产构建通过，完整隔离浏览器 `confirmation_browser_smoke` 通过，新增列表/对比 DOM 两处断言；夹具同时提供有损 .98 与精确 .99，实际页面须显示 .99。原报价转单、订单/应收/回款断言仍通过。`git diff --check` 通过；既有构建警告保留。证据：`ops/backups/quote-list-comparison-precision-20261028/`。
- 无生产连接、写入、提交、推送或部署。当前应用发布基线仍按 Q-050 的 `7df0154` 记录，本轮补丁仅本地。下一步检查版本差额计算与费用/成本/毛利显示，并补实际隔离 HTTP/PG 契约；分析汇总和其他编辑入口尚未穷尽，不标记整个金额审查完成。

## 2026-10-28 Q-050 Q-049 报价明细金额/改价路径精度修复已发布（7df0154）

- 只读 SSH 确认服务器 `55a7750b14b81a58afee8c52651ef500530aa1c5` 的报价明细序列化仍只返回浮点 `quoted_price`/`amount`，与隔离复现一致。
- 固定提交 `7df01541ec1c6c87b844ba94de9d3b4a1fc3978a` 已同步 GitHub/Gitee `pending-acceptance`；发布包 SHA256 为 `c17e381de883df7ba20652ae4b310ffae6f26e72dd3a1a964d5819e5c8f4bb36`。
- 统一发布入口预检通过后执行 `--apply`，返回 `status=verified`，备份为 `/opt/crm-sales-agent/backup/unified-7df01541ec1-m3b65qe5`。无数据库迁移、依赖升级、配置修改或生产业务数据写入。
- 发布后独立核对：前后端版本均为完整 `7df0154...`，`RELEASE.json=verified`，服务 active，`/health` 返回数据库正常且钉钉 OA/Stream 正常，公网首页 HTTP 200；服务器报价序列化已包含 `quoted_price_exact` 和 `amount_exact`。
- 后续仍需核对报价列表、分析汇总和其他价格维护表单；本次发布不代表全系统金额审查完成。
## 2026-10-27 Q-048 Q-042至Q-047 精确金额修复已发布（55a7750）

- 只读 SSH 确认服务器 `fc01dac819f4f2e79d4f0de54e06bcc91d80db46` 的订单序列化确实只有浮点 `unit_price`/`amount`，没有精确字段；与 Q-047 隔离复现一致。
- 本地固定提交 `55a7750b14b81a58afee8c52651ef500530aa1c5`，包含已完成并通过隔离回归的 Q-042 至 Q-047 精确金额修复、测试和质量记录；用户原有文档删除未纳入提交。GitHub/Gitee 的 `pending-acceptance` 均已同步。
- 发布包 SHA256 为 `340564df70f5b52dbef1be79b86800a69fdfa750f8bd67e07c36d046c6f0cbbe`；统一发布入口预检通过后按用户授权执行 `--apply`，返回 `status=verified`，备份为 `/opt/crm-sales-agent/backup/unified-55a7750b14b8-d17tbnz6`。无数据库迁移、依赖升级、配置修改或业务数据写入。
- 发布后独立核对：`DEPLOYED_COMMIT`/`WEB_DEPLOYED_COMMIT` 均为完整 `55a7750...`，`RELEASE.json` 为 `verified`，`crm-sales.service` active，后端 `/health` 返回 `status=up`、`database=ok`、钉钉 OA/Stream 状态正常，公网首页 HTTP 200；服务器订单序列化已包含 `unit_price_exact` 和 `amount_exact`。
- 该发布不等同真实生产角色完整浏览器回归或第三方故障注入；既有构建警告和未覆盖的分析/其他金额编辑路径仍按后续审查范围保留。
- 同一发布提交的 GitHub CI `40638794859` 随后完成：UI 冒烟与后端回归均通过；仅保留 GitHub 关于 Node.js 20 运行时迁移的既有警告。

## 2026-10-27 Q-047 订单明细四位单价展示丢精度（已发布）

- 已确认触发条件：隔离库正常创建 CNY 订单，数量 1000、单价 `33.3333`，接口原有浮点字段返回 `unit_price=33.3333`、`amount=33333.3`；页面原有 `formatMoney(number)` 按最多三位小数展示，单价会变成 `¥33.333`，而行金额仍按实际四位单价计算，明细视觉口径不一致。该问题是展示精度问题，不改计算结果或价格规则。
- 修复：订单明细序列化增加 `unit_price_exact`、`amount_exact` 字符串字段，前端单价和行金额优先使用精确字段，旧数值字段保留作为兼容回退。没有数据库结构、权限、币种、舍入规则或写入逻辑变化。
- 验证：隔离 PostgreSQL/HTTP 创建订单后实际读取明细，确认 Decimal 原值为 `33.3333` 与 `33333.30`；后端专项测试 19 项通过；前端构建通过；完整隔离浏览器回归通过，确认页面显示 `¥33.3333` 和 `¥33,333.30`；脚本语法及 `git diff --check` 通过。前端构建仍有既有 lottie `eval` 警告，未将其误记为本轮业务故障。证据目录 `ops/backups/order-item-precision-20261027/`。
- 本轮隔离验证阶段只访问本机数据库和假 API 浏览器；后续 Q-048 已完成服务器核对和发布。Q-041 权限口径仍按用户要求保持现状。
- 范围边界：本轮只证明订单明细单价/行金额这一读取路径；报价列表、分析汇总及其他编辑输入仍需独立核对，不能据此宣称全系统金额精度已完成。

## 2026-10-27 Q-041 用户反馈与当前处理口径

- 用户回复权限选择问题：“现在是什么样的，没太懂，权限不用卡那么死”。已解释当前普通附件改名/删除仍要求file:manage且文件来源在可见范围；仅有来源查看权而没有file:manage不能操作；已签署/系统生成原件保护保持。
- 用户表达倾向宽松权限，未批准增加来源业务维护权限。当前保留既有行为，不实施Q-041收紧方案，不将此反馈扩大为可越过其他模块范围、原件保护或放开更多权限的授权。Q-041保留已复现的权限差异证据，状态为暂不收紧；不把本次解释写成已修复安全缺陷。
- 本轮仅补充记录，没有应用/权限配置变更或部署；Q-042至Q-046已验证本地修复保持，后续仍可独立推进质量审查。


## 2026-10-27 Q-046 补齐报价转单分期预览精确总额（本地已修复）

- 上轮goal turn为实质进展：订单生成分期已修复并真实落库补证。重读长期原目标/质量目标/进度及工作区，继续先前明确未覆盖的报价转单调用链，不将订单单点修复当整个分期流程完成；Q-041仍未获人工回复，不修改权限。
- 同一Q-046扩展范围：QuoteDetailPage原planTotal和ReceivablePlanPicker均取Number(version.total_amount)，picker渲染再用number金额，保留与订单分期相同丢分触发条件。报价serialize_version只有_f浮点总额，没有向页面提供精确原值。
- 修复：报价版本只读增加total_amount_exact，原字段保留；总额为None时保留null而非字符串None。转单主文案、计划判据、共用选择器及报价详情应付合计优先exact。选择器接收number|string并使用已有splitAmountsExact，数值调用方与默认CNY/原前缀显示策略保持，未补外币能力。无数据库结构、报价审批/接受/转单闸门、币种/金额规则、交互控件/业务功能或依赖变更。
- 实际HTTP/PG：正常创建隔离商机、报价版本22，再整版录入数量100×单价999999999999.9999的明细，GET版本返回total_amount_exact=99999999999999.99、旧浮点=99999999999999.98。只留草稿，没有绕过正式审批/成本完整性去转单。backend test_quote_cost_gate增加正常大额与空总额序列化回归，共26项通过。
- 浏览器新增报价主单/版本均已接受的隔离夹具，打开真实QuoteDetailPage的转单弹窗，确认总额.99，点击可见分期选项后默认30/70显示30000000000000.00和69999999999999.99。完整confirmation_browser_smoke通过，前轮订单/回款/分期断言仍通过。初始夹具主单pending与版本accepted不一致被正常闸门挡住，首次修夹具定位字符串未命中后已精确修正；随后input[value]选择器与Semi实际DOM不符，改为可见label点击后通过。均为测试夹具/选择器错误，无放宽应用闸门或断言。
- 完整后端pytest退出0/100%，最终26项报价专项、34项Node报价/金额测试、前端类型检查/构建、pyflakes、脚本语法及diff通过。证据ops/backups/quote-installment-exact-20261027/。浏览器是假API，真实接口另证数据契约，未称生产角色完整转单端到端。
- 未SSH/生产写入/源码提交推送/部署。Q-042至Q-046本地补丁保留待发布；Q-041待人工决定。报价列表/版本对比/其他分析金额和订单行单价、编辑路径仍需继续核对，不能以这次补齐声称全系统金额展示和写入均已验证。下一独立方向优先订单行金额与小数数量的真实契约，避免重复扫描已排除的入口。


## 2026-10-27 Q-046 大额订单分期预览与后端生成差分（本地已修复）

- 上轮goal turn为实质进展：Q-045登记回款写入精度修复/实际HTTP和浏览器验证。重读原始目标与当前记录/工作区，继续分期链路；Q-041无批准，保持未改，Q-042至Q-045保留。
- Q-046/P2确认：真实前端原splitAmountsLikeBackend接收Number(total)、将bigint分币再转Number[]。总额99999999999999.99、50%两期时实际原函数为49999999999999.99/49999999999999.99；后端实际split_amounts(Decimal总额)为50000000000000.00/49999999999999.99。后端结果正确，前端输入/输出转浮点丢分导致用户确认的预览不一致。旧代码用git HEAD原文独立转译执行留证，不只依赖静态推断。
- 修复：同一原整数/half-even/末期补差算法抽出splitAmountsExact，接受十进制总额并返回两位金额字符串；原splitAmountsLikeBackend保留number输入/输出兼容包装。订单详情生成弹窗与可提交判据使用order.total_amount_exact及exact分期结果；共享planBlockedReasons/isPlanValid支持精确总额。没有选择新舍入策略、改后端金额计算/比率校验、表结构、请求参数、权限或控件，不新增依赖。仅移除已证实的有损中间转换。
- 红绿回归：quote_todo共25项修前24过1失败，修后25全通过，新增大额50%分期、0.05半分、正常30/70及0.03五期非正数拒绝；原24条兼容测试保持。另9项payment金额/分配测试、最终类型检查/生产构建、脚本语法、diff通过。
- 真PG/HTTP：正常新建CNY订单16并POST生成50/50两期，实际amount_exact为50000000000000.00和49999999999999.99。隔离浏览器扩展既有订单991详情，空计划点击生成应收计划，默认30/70预览DOM显示30000000000000.00和69999999999999.99；完整confirmation_browser_smoke通过，原登记回款/金额展示断言仍通过。浏览器为假API，真实HTTP另行证实落库，未混称生产端到端。
- 证据ops/backups/installment-precision-20261027/，仅本机隔离数据、推送/调度关闭，无SSH/提交/推送/部署。Q-042至Q-046仍本地待发布，Q-041待人工决定。
- 边界：报价转单共用选择器当前仍从报价浮点总额取得预览，未在该调用链新增报价exact字段；订单行单价、分析和其他金额编辑入口仍须独立验证，本轮不宣称这些路径全部正确。下一步继续报价转单预览的真实金额契约，或另选独立未覆盖的业务风险；长期质量目标未完成。


## 2026-10-27 Q-045 登记回款默认值/提交浮点转换导致少记分（本地已修复）

- 上轮goal turn为实质进展：Q-044资金展示精确字段闭环。再次读取长期目标、质量目标/进度及现有代码，检查写入方向；不因展示修复就宣称资金编辑安全。Q-041仍待人工选择，其余本地补丁保留。
- Q-045/P2复现范围：订单详情登记回款按钮原用String(record.remaining_amount)初始化，提交又用Number(paymentForm.amount)，即使手工输入精确.99也会在请求前成为.98。对前轮通过正常接口创建的大额CNY应收，在本机真实POST /payments分别发送等价旧Number形态与精确字符串：回款7实际received_amount=99999999999999.98，回款8为99999999999999.99；独立SQL确认，均仅pending未财务确认。不是只读显示问题，也未在生产造数据。
- 最小修复：OrderDetailPage的默认值优先remaining_amount_exact，无该字段保持原fallback；提示同样使用exact；请求将输入trim后作为Decimal字符串传给原接口，后端PaymentCreate已有Decimal(16,2)约束无需变更。页面、控件、日期、权限、请求键、确认状态机及业务规则不变，不新增依赖/精度限制/数据库字段。仅消除有损Number转换，按确定数据准确性缺陷直接修复。
- 永久浏览器回归：在既有confirmation_browser_smoke扩展订单991实际详情与应收夹具，点击应收计划/登记回款，断言默认input完整99999999999999.99；不编辑直接提交，拦截请求恰好一条且received_amount为同值字符串。整个浏览器回归通过，前端类型检查/构建、9项金额分配Node测试、脚本语法与diff检查均通过。真实API/PG另证字符串保存正确，浏览器是假API隔离环境，不描述为生产端到端。
- 证据ops/backups/payment-input-precision-20261027/。只有合成本机待确认回款写入，无外部付款/推送、生产写入、SSH、提交推送或部署。Q-042/Q-043/Q-044/Q-045本地待发布，Q-041状态仍待决定。
- 未完成：本轮覆盖登记回款默认值/直接登记，不代表所有编辑表单“原样保存”已验证。订单行单价、分期比例换算和其他编辑路径仍须逐项核对；不能将合法大额少记一分泛化为普通小额均有误。下一步继续检查应收计划编辑及分期金额的精确值传递，或其他独立业务风险。


## 2026-10-27 Q-044 大额订单/应收/回款展示丢分（本地已修复）

- 前轮goal turn为实质进展：Q-043精确字符串负号修复与红绿测试。重读完整原目标、质量目标/进度与当前工作区，继续金额链路；Q-041权限未批准，不修改；Q-042/Q-043本地补丁保留。
- Q-044/P2真实复现：本机正常POST /orders用数量100、单价999999999999.9999创建CNY订单，再正常生成全款应收。金额在Numeric(16,2)允许范围内，接口应收amount_exact为99999999999999.99；浮点amount为99999999999999.98。直接运行页面所用真实money.ts，现有列表amount回调实际渲染¥99,999,999,999,999.98，exact字符串渲染为正确.99。未修改数据库构造非法金额；原本资金数据正确，缺陷在展示丢精度。
- 最小范围修复：应收/回款列表和订单详情已有exact字段的展示优先使用原字符串；为订单总额/构成/已收/未收及应收余额补只读exact字符串字段，原数值字段保留，兼容旧响应。客户详情订单表与分配弹窗标题同步取exact。订单列表“已收足”颜色改看后端已算余额<=0，避免两笔接近大额的float相等误判。不改数据库结构、金额算法、舍入口径、权限、锁策略、编辑规则或历史数据，无新增依赖。
- 永久回归：test_payment_consistency添加高额99999999999999.99、已收...98、剩余0.01及负优惠/空构成的序列化断言；既有confirmation_browser_smoke增加订单/应收/回款三列表实际浏览器DOM断言，假接口明确给出float .98与exact .99，防止页面重新误用float。真实API重新读同一合成订单及应收，新增exact字段均准确.99。其余正常用例保留。
- 验证：18项payment consistency通过；完整后端pytest tests退出0/100%，9项Node金额/分配测试通过，完整浏览器回归含新三列表通过；最终前端类型检查和构建、pyflakes、diff检查通过。浏览器用隔离假API验证呈现，真实PG/HTTP独立验证数据契约，不把两者合称生产真实账号端到端。
- 证据ops/backups/exact-money-display-20261027/。测试仅本机隔离库/合成数据，无SSH/生产写入/提交/推送/部署。Q-042/Q-043/Q-044本地待发布；Q-041仍待人工决定。
- 未完成范围：订单行单价/金额、大额编辑表单默认值及新建成功提示、分析汇总/其他金额页面仍须核对，不能由当前三列表和订单概要修复证明全部金额路径精度一致。下一步沿这些尚未覆盖的读取/编辑边界验证，不为暂未证明的路径先改代码；长期质量目标保持未完成。


## 2026-10-27 Q-043 精确金额字符串负小数丢失符号（本地已修复）

- 前轮goal turn为实质进展：真实HTTP/PG补证预览失效整批回滚。此次重新读取原始目标/质量目标/进度及现有工作区，Q-042成本修复保留、Q-041权限决定仍未答复，不重复修改或部署。
- 从回款/应收金额空值和历史字段链路转查共用金额渲染：payment.serialize_plan/serialize_payment提供exact字符串供AllocationAdjustModal等使用；formatMoney已明确接受number/string/null，null及非有限数字显示-。没有仅凭前端number类型与接口_f可空就确认页面崩溃，正常应收/回款金额数据库约束还需结合实际历史数据验证。
- Q-043/P3确认范围为共用格式化函数：直接运行真实TypeScript源码，formatMoney('-0.01','CNY')实际为¥0.01，而number -0.01为¥-0.01；-0.99与带前导/尾随零的同类负金额同样丢负号。money.ts先对字符串整数部分BigInt('-0')转换，负号在拼回小数部分前丢失，违反原金额方向。未证明当前生产业务页面存在可达的负exact输入，不扩大成已造成生产账务错误。
- 最小修复：字符串分支分离符号与整数绝对值，再原样拼接小数；只修符号保留，不改舍入、精度、分组、未知值/币种或业务计算，不增加依赖。ops/payment_allocation.test.mjs（既有CI执行文件）增加精确金额渲染回归，覆盖负一分钱/负小数/尾随零/前导零/正常负数/正值/零/大额正负精确字符串和null、数字负小数。
- 红绿证据：修前9项8过1失败，差异为¥0.01与¥-0.01；修后9项全通过，生产构建/类型检查通过、diff检查通过。无数据库/历史数据修改，无权限或产品行为取舍，因此按明确格式错误自主修复。证据ops/backups/money-sign-review-20261027/。
- 仍只完成函数与构建级验证，未称生产页面端到端；数字路径的大额IEEE浮点显示与exact字段使用一致性仍是后续调查方向，不能用本轮符号修复宣称全系统金额精度无问题。未SSH/源码推送/提交/部署；本地Q-042与Q-043待发布，Q-041待人工选择。


## 2026-10-27 导入预览—确认真实HTTP与整批回滚补验

- 前轮goal turn为实质进展：新增价格/数量/日期边界HTTP证据，排除同类误报。重新读取长期目标及进度；Q-042成本精度修复保持本地未提交，Q-041权限选择仍未答复，不把自动继续视为批准。
- 新范围：既有test_seventh_round_imports的成本原值指纹主要为直接路由/SQLite验证，本轮补真实HTTP、真实PostgreSQL和真实页面维护API交错。前端CsvImportButtons静态链路确认正式请求携带preview_token；后端finalize核对文件摘要、各行结论、原值指纹，不满足则rollback+409。
- 本机隔离环境合成成本14，先实际导入10；预览改20但数据库保持10。拿旧令牌换CSV改30返回409且原值10不变；预览后经正常PATCH成本保存200，再用旧令牌确认返回409，保留200；畸形与篡改令牌均422且未覆盖200；无price:manage的业务员拿有效令牌仍403；重新预览且文件/原值未变化时正常写入20。
- 多行额外验证：预览一条更新和一条新增，随后正常PATCH改动既有成本，再执行相同两行文件。真实后端最终409，SQL独立确认既有行保留200，新生效日2027-04-02没有记录。证明finalize拒绝时不仅恢复既有行，也回滚同批新增，未留下“前面已经导入”的半批结果。
- 此场景未发现新Bug，未增加应用修改或镜像单元测试；前轮Q-042改动保持。证据ops/backups/import-preview-confirm-20261027/，包含两份可复现脚本、实际HTTP日志及独立SQL结果。只访问本机8031/55439，第三方推送/调度关闭，未连接生产、推送或部署。
- 边界：使用真实后端HTTP和页面维护API，未把静态前端代码核对称为浏览器上传操作；未穷尽客户/线索/产品全部预览指纹字段或生产并发。下一步可审查其他模块的导入预览回滚/事务，或继续其他资金与权限边界；长期目标仍未完成。


## 2026-10-27 价格规则/客户专属价导入边界审查（未复现成本精度缺口）

- 前轮goal turn为实质进展：确认并修复Q-042成本导入。再次读取长期目标与最新工作区，Q-042仍本地未提交/部署，Q-041人工权限选择未答复，未擅自实施。
- 新调用链：pricing/io_router的价格规则和客户专属价导入均调用service.check_price_rule_values；其_check_numeric_storage已对数量Numeric(16,3)、金额Numeric(16,4)、目标利润率Numeric(6,4)拒绝超精度，并忽略无意义尾随零。不是仅靠页面Pydantic，故成本导入缺口不能泛化为所有价格导入缺陷。
- 本机真实HTTP/PG反例排除：传数量下限1.0001、上限2.0001及指导价/约定价0.00001，两类正式导入均逐行失败、无落库；额外目标利润率0.00001也拒绝，未记为新Bug。独立SQL确认所有本轮marker记录为0。
- 增量33组真实接口预览验证通过：两条导入路径NaN/Infinity、负价、0.00001、1e-10000、1e10000、整数超限、数量超精度/超限、非法/不存在/超范围日期均拒绝；合法最小0.0001、1.23000及999999999999.9999接受；规则目标利润率0.00001拒绝，0.0001/1接受。历史资料模式仍做这些数值与日期校验。
- 初始合法预览被本机既有D等级无限区间价格规则拦截，属于正常冲突规则。改用系统已有历史资料标记，仅避开本轮无关冲突，不改代码或放宽精度断言。由此合法边界接受的证据只覆盖历史资料预览；未声称正式启用规则全流程已验。非法值实际正式导入拒绝与0写入证据独立保留。
- 本轮无应用/测试代码修改，无提交/推送/部署或服务器操作；复用本机专用隔离库，外部调度/推送关闭。证据ops/backups/price-import-boundaries-20261027/。Q-042和Q-041状态保持原样，不把绿测试当整个定价模块无缺陷；下一步可独立验证预览令牌与实际执行内容变化、长文本/编码边界或其他未覆盖模块。


## 2026-10-27 Q-042 成本CSV导入超精度静默舍入（已修复，本地待发布）

- 上轮goal turn为实质进展：完成真实multipart断流补证并复现Q-041。Q-041权限选择仍未收到人工回复，保持未修改，继续独立审查金额导入。
- Q-042/P2已确认：本机隔离PG/API实传CSV采购成本0.00001、其他三项显式0、生效日2026-12-29。接口报告新增成功，独立SQL读成本8实际purchase_cost=0.0000。成本导入调用RowErrors.decimal仅校验有限/非负，直接赋ProductCost后由Numeric(14,4)舍入；与页面CostCreate/CostUpdate明确最多14位数字、4位小数不一致。预览同样把1e-10000和0.00001视为可更新；可能把非零成本静默记为零，影响成本准确性与毛利。
- 修复：pricing/io_router.py在写入前复用现有CostCreate字段约束，失败逐行反馈精度原因，预览和正式入口一致。没有新增精度规则、依赖、字段或事务锁；沿用页面原有限制，属于明确漏校验的唯一修复。未清洗历史数据，不恢复归档人民币补丁，不改变空白保留旧值和显式零成本语义。
- 验证：test_seventh_round_imports共62项通过，新增四成本字段×三非法值×预览/执行共24组拒绝场景及0/最小单位/尾随零/上限四组合合法场景；原有空白保留、预览零写入、逐行回滚等仍通过。实际HTTP同一CSV重试修后failed_count=1、created/updated=0；真PG好坏好三行预览和正式均两条成功一条精度失败，SQL实读0.0001与9999999999.9999准确保留。check_import_integrity全套通过，pyflakes及diff检查通过。
- 范围与限制：异常NaN/Infinity等原已拒绝，超大数原由SAVEPOINT隔离为失败行，未据此报新500。仅成本导入已复现与修复，其他价格/数量入口不能因该专项就称全部验证。未连接服务器、未提交推送部署；本机合成成本记录保留，历史生产数据未动。证据ops/backups/cost-import-precision-20261027/。
- 下一步：Q-041等人工决定；Q-042可在下一次明确授权部署前核对服务器并完成同SHA发布检查。其他独立审查继续核对导入数量、价格字段与页面约束是否一致，不把静态差异当确认Bug。


## 2026-10-27 Q-041 业务附件改名/删除权限不一致（已复现，待人工决定）

- 上轮goal turn分类为实质进展：Q-040已提交发布并独立核对491文件。此次重新读取原始长期目标、AGENTS、QUALITY_GOAL和最新进度，以fc01dac为基线；不把完成一次发布当作长期质量目标完成。
- 本轮新证据：本机真实HTTP multipart声明2000000字节、发送部分内容后断开连接，比较前后files表行数和独立上传目录均无残留。随后正常上传22、实际下载字节/哈希一致、删除后磁盘无残留且下载404；补齐此前仅存储层中断单测的HTTP边界。此路径是在multipart解析阶段中断，不扩称覆盖提交中的取消补偿。
- check_attachment_business_auth（66项）和check_attachment_sample_guards均通过；已有套件只验证缺file:manage或缺来源查看权的改名拒绝，未覆盖同时有file:manage、quote:view但无quote:manage的组合，因此现有绿测试不能排除下面问题。
- Q-041/P2已确认：在隔离库创建仅file:view、file:manage、quote:view/all的合成账号。管理员给既有报价1上传普通附件34。该账号GET文件200、PATCH报价403（需要quote:manage），但PATCH文件名200、DELETE文件200，普通报价附件被改名并删除。file/router.py rename_file和delete_file依赖file:manage并仅调用can_access_file；access.py can_access_file按来源查看权判断。上传/挂载/解绑同时要求visible_object(write=True)，导致同一业务附件的写权限不一致。未在生产造数据验证。
- 影响：文件管理者即使没有报价维护能力，仍可破坏其可见报价的普通附件；已签署/生成原件保护未被此探针绕过。其他业务类型共享同一文件路由，具体类型影响仍需验证，不能只凭共享函数说已穷尽。
- 待决定方案：改名/删除也要求当前业务的原有维护权限，孤立文件仍由上传者管理；收益为统一业务写入边界，代价为当前仅凭file:manage管理可见附件的账号能力收窄。保留现状则该能力继续存在。按AGENTS权限规则先请求用户选择；未改应用代码、未部署、不把沉默当批准。并发重新挂载/改派等待后的重查策略也须在选定范围后验证，不擅自新增锁策略。
- 全部证据/两份探针归档ops/backups/attachment-review-20261027/。本机隔离库与文件夹使用既有独立环境，第三方推送/调度关闭；无SSH、生产写入、源码推送或部署。下一步按人工选择修复或记录保留，并补真实权限组合和受保护/共享文件回归；长期审查还可独立推进其他未覆盖模块。


## 2026-10-27 Q-040 报价成本脱敏修复已发布（fc01dac）

- 用户明确“确定服务器也有这个问题就部署修复”。只读SSH确认部署基线38b4164；运行清单登记的web/assets/QuoteDetailPage-CdrqcSgi.js实际SHA256为803c9360d88a79cfd33944dfaa8516c98b2db5c1630febc7353a811deeb1e874，与RELEASE.json一致。其成本render直接对(Tn?e+t.logistics_cost_snapshot:e)调用toFixed(2)，与本机业务员成本脱敏null的实际崩溃相同。未在生产创建测试报价。
- 固定提交fc01dac819f4f2e79d4f0de54e06bcc91d80db46已同步GitHub/Gitee；只含报价可空成本/利润处理、回归用例及审查记录，未带用户原12项文档删除。21项发布安全测试、固定发布包浏览器回归通过。相同SHA CI40626748900 completed/success，后端全量回归与UI冒烟均通过，链接https://github.com/ytsbx/crm-sales-agent/actions/runs/40626748900 。查询过程中EOF/TLS超时后独立重查最终成功，没有把查询断线当CI失败或跳过CI。
- 发布包SHA256 305a3df26ca25002d1b89feca48d72334c63595ca5a48712edc5dc37bf7ef713；工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab。从固定提交独立构建，统一工具服务器预检通过后--apply返回verified，备份/opt/crm-sales-agent/backup/unified-fc01dac819f4-iehv26xq保留。
- 独立发布后复核：前后端版本记录均fc01dac完整SHA，491个清单文件全部匹配、0不匹配；服务active/running、NRestarts=0，/health为up/database ok/OA同步与Stream正常，公网HTTP200。首次手工误查/api/v1/health返回404，核对路由后用正确/health通过，不是服务故障。
- 未改数据库、依赖、配置、权限或生产业务数据；实际前端web-dist已更新，服务器残留frontend/src仍不作为运行真源。健康/文件一致性不替代生产业务员真实有数据详情回归；本次错误场景已在本机真实API/PG及发布包浏览器验证通过。
- 证据归档ops/backups/release-fc01dac-20261027/。本次部署完成，长期质量审查仍保留第三方真实故障、大数据性能等未验证范围；用户原文档删除保持未提交。


## 2026-10-27 隔离环境重复请求、响应丢失和对外文件验证

- 按用户“继续”，仅启动本机隔离PostgreSQL和API8031；关闭外部推送/调度配置保持原样，无SSH/生产数据/服务操作。本轮未修改应用代码。
- check_biz_doc_concurrency（22项）、check_biz_doc_freeze（98项）、check_biz_docs（57项）均通过。覆盖真实PG独立连接同键并发只生成一份、不同请求键版本链连续、同版本唯一约束、原件重复下载字节相同、状态副本明确区分、原件丢失410及审计、显式历史重建、快照和模板版本冻结、取消订单不能出单、他人打样出单403及按来源模块授权。
- 新增本机响应丢失探针：创建CNY订单11及应收，以本机HTTP代理把POST /payments转给真实API，后端提交成功后直接关闭客户端连接，不转发响应。客户端实际收到RemoteProtocolError；随后相同请求键两次并发重试均返回回款6，同键改金额101返回409。独立psql确认订单11仅1条100.00回款；未确认金额不进入应收已收额。日志lost-response-round3.log及可复现脚本已保存，纯本机不涉及第三方网络故障。
- 首次临时代理遗漏Content-Type，后端正确拒绝参数，探针未到提交点；补齐代理请求头后重新运行成功。失败轮留下本机订单10/应收，未留下回款；这是探针实现错误，不记为应用Bug。隔离数据保留供后续验证。
- 已有冻结套件使用直接构造的历史USD快照验证旧文件稳定；不代表批准创建非CNY业务，不据此新增外币兼容能力或清洗历史数据。本轮新造业务链全部CNY。
- 证据在ops/backups/local-real-e2e-20261027/*round3.log及lost-response-reproducer.py；diff检查通过。以上为接口、真实PG及文件字节验证，未扩称浏览器下载操作或真实第三方断网验收。未发现新确认故障；上一轮Q-040仍本地待提交/部署，用户原文档删除保留。下一步可验证附件上传/删除与权限变化的交错，以及更多真实页面有数据状态。


## 2026-10-27 隔离环境主管审批、履约与异常流程补验

- 按用户“继续”，只启动本机既有隔离库crm_iso_local_e2e_20261027_test与API8031；外部推送/调度仍关闭。测试前对该隔离库留存before-round2.dump；未连接或修改服务器。
- 真实HTTP/API与PostgreSQL五套专项通过：check_approval_rules（74项）、check_order_payment_api（103项）、check_order_completion_evidence（16项）、check_approval_inbox（78项）、check_batch14_order_safety（两组）。使用测试专用数据并执行原有清理，未改测试断言或应用代码。
- 主管/财务：普通审批、低毛利财务会签通过与否决、极速主管一步审批、收件箱权限/范围/分页、转交及拒绝转交无副作用均通过。发货/回款：分批及超量拦截、未发完禁止完成、批次节点投影、发货取消竞争自洽、财务确认与业绩归属通过。
- 异常状态：无发货记录/只建未发批次不能完成，已完成回退必须写理由并留痕，取消后不可推进。额外实际锁等待证明旧负责人403、新负责人可写；大数量仍差0.001时不提前完成，补齐发货后可完成。
- 证据边界：以上为接口和服务层专项，未扩称全程浏览器回归。代码搜索未发现订单/回款模块独立退货退款办理接口或页面；分析页出现“退货”说明文字并不证明该能力存在。“完成回退重做”不等于退货退款闭环，未擅自新增业务功能。
- 原客户4/报价1的业务员成本脱敏及他人客户/财务确认403再次通过；前轮订单和回款记录保留。本轮无新的确认Bug，无新应用补丁、提交、推送或部署。证据保存ops/backups/local-real-e2e-20261027/*round2.log。下一步可继续隔离验证重复请求/弱网重试与文件导出实际操作；真实第三方故障和大规模性能仍未覆盖。


## 2026-10-27 本机真实业务闭环与 Q-040 报价成本脱敏崩溃修复

- 用户授权在本地造数据测试，明确不动服务器。本轮以38b4164为应用基线；使用独立PostgreSQL库crm_iso_local_e2e_20261027_test、8030前端/8031真实API、独立上传目录，关闭调度及全部外部推送。没有生产写入、SSH、推送或部署。
- 真实浏览器通过种子业务员登录，创建客户4、商机2、需求明细及报价1；发布版在报价详情可复现null.toFixed崩溃。实际报价API为200、成本和利润字段按原权限返回null，故属于前端空值处理问题，非缺失资源或权限授予问题。
- Q-040已确认并修复：QuoteItemRow准确声明脱敏成本/利润可空；详情成本、利润率及改价摘要保留未知值；WhatIf测算对未知成本传播null，避免误算零成本利润。未改变后台权限、价格或成本业务口径。
- 修复后的本机浏览器正常显示相同报价。永久浏览器回归增加有明细且成本/利润脱敏null的业务员用例，断言详情正常且未知利润不显示0%；完整confirmation_browser_smoke通过，生产构建通过、diff检查通过。首次新增夹具遗漏minimum_price_snapshot导致失败，补全真实响应字段后复验通过，未放宽产品判断。
- 同一笔真实数据继续经本机实际API确认主数据并接入草稿、添加运费、提交免审通过、登记手工发送、客户接受、转订单1、业务员登记回款1、财务角色确认。货款290+运费80=370 CNY，应收370.00、已收370.00、剩余0、状态paid，未绕过状态机或直接SQL改状态。后半段为API验证，不能称为全程浏览器操作；本轮也未验证主管人工审批分支。
- 独立负向验证：业务员读取他人客户3返回403，确认回款返回403(payment:manage)，其报价成本/利润仍null。没有扩大权限。浏览器导出取证后会话ieqt正常停止。证据位于ops/backups/local-real-e2e-20261027/。
- 残余范围：完整主管审批、发货/退货、第三方真实断网及大数据性能仍未完成。仅本地应用文件与回归脚本待提交，服务器保持原版本；隔离库保留便于下一轮复验。下一步在相同隔离环境覆盖主管审批及订单异常流程。


## 2026-10-27 真实业务员生产只读页面回归（首轮通过，数据为空的边界保留）

- 用户按建议明确“做”，范围为真实账号生产页面只读检查，涉及写入/AI发送继续隔离验证，不扩大成生产客户/订单变更或第三方故障注入。使用browser-skill在唯一连接Edge实例9a0e4728创建Agent会话vuna；初始未登录，通过request-help请用户自行登录，无读取或代填密码、提取令牌。用户完成登录后观察身份为“张三/销售部/业务员”，这是实际生产账号，不把它称为原截图“冷光友”。
- 实际浏览器结果：①/settings?tab=profile显示“我的信息/我的权限/个人登录名”，无无权限提示；②/settings?tab=roles只能回到本人“我的”，不显示角色管理页；③/wecom明确无wecom:view，未触达企微集成数据接口；④/customers导出按钮disabled，只有一次独立授权提示，与下载模板/批量导入/新建客户同一按钮行，952px实际截图证实不再被旁边长提示顶到上面；没有点击下载或导出；⑤客户/商机/报价/订单列表实际请求成功、页面各有正常空态；⑥/agent正常显示输入框、能力清单，该账号没有历史会话，本轮未发送问题/创建对话。
- 权限配置独立证据：经SSH在显式SET TRANSACTION READ ONLY的生产Session仅查询原截图姓名匹配账号的有效角色/相关权限，不输出密码、电话、令牌或连接凭据。冷光友账号active、salesperson/self，有customer:view/order:view/agent:use，没有customer:export/wecom:view/wecom:manage/settings:manage。它能解释原截图企微/导出被拦截的配置原因，但不是该账号浏览器登录验证。种子角色也将企微查看/管理赋给主管；未因此自动给业务员放权，若要改变仍需用户决定。
- 登录完成后重新开始独立只读debug记录，不导出含登录过程的旧capture。本轮已完成的29个/api/业务请求均GET/200，0条POST/PUT/PATCH/DELETE；管理/users/roles/settings/departments和企微集成数据接口均未请求，控制台6条verbose、无error/warning。没有读取Cookie/token/localStorage、没有修改用户权限、生产业务数据、服务或配置。debug列表无截断/丢弃，导出保存在本机既有备份目录，不向第三方发送。
- 版本证据：实际线上仍38b4164，健康up/database ok/OA stream connected；另只读逐项核对RELEASE.json所列491文件（含119前端web资源）全部同SHA256，无不匹配；页面验证直接访问生产资源，不拿服务器旧frontend/src当现网。生产页未发现本轮新的确认Bug，不为增加修改量加应用补丁或重复镜像测试。
- 隔离补证：新建本机127.0.0.1:55439专用crm_iso_role_browser_review_test迁移head，真实PG/ASGI check_agent_stream_cancellation再次3组通过（已发文本保存、未完成动作/任务回滚、成功commit不重复），相关AI安全31单元通过；未在生产发送AI请求或修改历史。明确夹具ID清理后独立psql确认users/sessions/messages/actions/tasks全0，专用库删除、PG停止。无应用变化，不重复完整1344套件。
- 证据：ops/backups/production-sales-readonly-review-20261027/包含各页观察、实际客户工具栏PNG、登录后capture、GET状态列表/控制台、实际权限只读查询、现网文件核验及隔离测试日志。BrowserSkill会话正常stop。无提交/推送/部署，用户原文档删除保留。
- 未完成范围明确保留：张三当前客户/商机/报价/订单列表均为空，没有可用真实详情或历史AI对话，不证明真实数据详情、跨负责人读取或完整销售闭环；未切换主管/财务角色，未通过生产撤权/写入构造反例。AI正常生成/停止/刷新断线未做生产外部模型调用；隔离ASGI取消证据不能扩称真实浏览器模型端到端。下一步优先用有业务数据的已授权账号继续只读详情链、主管/财务角色对照；若缺少合适真实账号/记录，继续隔离端到端而不创建生产探针。长期全面质量目标未完成。

## 2026-10-27 Q-028 实际断线补偿修复已部署（38b4164）

- 用户本次明确授权“没问题就部署”。固定提交38b4164daad83d29d2a29c3a40e8e9f382c040d9已同步GitHub/Gitee现有pending-acceptance分支，基于另一电脑最新e9151bc文档及服务器14b717a应用；本次应用仅runtime.py的限时取消保护、未完成事务回滚与成功回复去重，保留另一电脑有效修改。原12项文档删除未提交/覆盖，未恢复人民币归档补丁。
- 发布前1344后端单元、3组真PG/实际ASGI断线及回执不确定性验证通过，21发布安全测试通过；pyflakes/diff/151登记/查询JOIN/输出判据通过。最终同SHA CI40618287329 completed/success，151接口套件、静态/单元及UI构建/浏览器冒烟全通过，链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40618287329 。gh watch最后一次观察因TLS握手超时退出，随后直接读取同一运行状态确认全绿，并非CI失败或重启任务。
- 统一构建从git archive固定38b4164及真实生产基线14b717a，包/tmp/crm-release-38b4164.tar.gz，SHA256 9419063727b399304fe6efdbeccb450ffd4f1f1eb48ef57007ae0828a26ab629；工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab，上传远端/tmp/crm-release-38b4164后逐项相同。冻结依赖构建成功，服务器预检通过，待CI全绿才--apply；无依赖/迁移/.env/服务配置或业务数据变更。
- 正式工具备份、短暂重启、切换并完成491包文件完整性、后端/数据库健康和公网首页/入口资源核验，返回status=verified。回滚备份/opt/crm-sales-agent/backup/unified-38b4164daad8-57f8m6nd保留。没有真实角色业务写入或第三方故障注入，也未新增磁盘专项检查。
- 发布后独立SSH复核DEPLOYED_COMMIT与WEB_DEPLOYED_COMMIT均完整38b4164，RELEASE.json verified同SHA；runtime SHA256 45c092240ebb65bdf20b17b5397f07a9602ee1d0429ee54b23723412d977a051与本地及提交相同。crm-sales.service active/running、NRestarts0，8010健康up/database ok，OA submission_enabled/sync_running/stream_connected均true。
- 证据归档ops/backups/release-38b4164-20261027/，含包manifest/摘要、构建/预检/apply/独立postcheck/同SHA CI及本地测试日志。本次部署请求完成，Q-028前条本地状态由本条覆盖；5秒超时/数据库不可用/进程强制终止仍可能无法保存等既定边界保留，不把健康检查等同生产真实角色端到端验证。长期质量目标未完成，后续以38b4164实际服务器代码为准；本次授权不泛化为未来独立生产变更。

## 2026-10-27 Q-028 用户要求修复后完成真实断线补偿（中，本地验证，未部署）

- 用户明确“做”，承接说明的优先修复14b717a半段回复遗漏并继续项目审查；开工已说明先本地修复/隔离验证，不涉及磁盘检查。先只读核实服务器仍14b717a、runtime哈希b441c504…；本机将两个远端一致的e9151bc快进同步，先备份并合并本机发布/审查记录，保留所有另一电脑新增记录和原12项文档删除，没有reset覆盖有效工作。远端仅后两提交为文档，所以应用基线仍服务器14b717a。
- 修复runtime.py：既有GeneratorExit/CancelledError补偿先回滚未完成事务，再在AnyIO限时5秒shield作用域内重读会话、只保存已经输出的文本及原中断标记。使用既有依赖AnyIO，无新增依赖/表结构/外部调用。捕获会话ID避免rollback后读取过期ORM属性；正常回复使用明确对象引用，取消commit可能已成功时按映射identity重查原回复，存在则不重复写。补偿存储异常或超时仅日志留证，继续原取消异常，不能伪装done成功。
- 事务边界验证：不对整个旧事务直接shield提交，不恢复/提交未完成的工具业务。已完成工具轮次原本提交的事实保留；未完成轮次先rollback，只有文本补偿进入新事务。用户“做”授权本次缺陷修复，未改L2/L3确认规则、权限、文本格式或工具业务逻辑，不扩展到真实模型连接清理（旧真实HTTPX证据已排除该误报）。
- 永久check_agent_stream_cancellation.py已登记（151套件）：本机真实PG/实际FastAPI Agent路由/StreamingResponse及get_db，ASGI spec2.3实际http.disconnect持续取消作用域，普通文本断线保存user+assistant；工具处理中断，合成未完成AgentAction及Task真实flush后均rollback且只保留user+assistant；真实commit成功后模拟取消回执，原取消继续抛出且只有一份完整assistant，不重复加半段回复。模型仅假流、鉴权合成CurrentUser，SQL/锁/事务及SSE发送/断线均真实；不声称真实生产角色、浏览器或外部模型故障注入。
- 修前证据：同一永久套件在独立进程加载服务器精确runtime源码，保持其余实际路由/数据库不变，普通断线第一组明确失败于仅user、没有assistant；修后3组exit0，逐组新Session核对内容、未完成动作/任务不存在及pool checkedout0。没有删断言或放宽期望来通过。
- 单元在原29项中扩展存储失败、限时到期两个分支，31项全部通过；原取消传播/模型close断言保留，测试仅把5秒时间预算缩短到0.01秒并验证shield确实启用，避免拖慢套件。完整后端1344项通过（进程exit0；原1342增加2项），pyflakes/diff/151登记、987查询JOIN及2176函数输出判据通过。新套件对缺DATABASE_URL、正式库名和远端地址均在导入应用前拒绝，即使ALLOW_DEV_TARGETS=1也拒绝。
- 证据和同步前文档备份归档ops/backups/agent-stream-cancel-fix-20261027/。测试仅127.0.0.1:55439专用crm_iso_agent_stream_fix_test，推送/调度/Stream关闭，无app lifespan/真实外发；明确ID清理，独立psql确认users/sessions/messages/actions/tasks均0，随后删除专用库并停止PG。当前无提交、推送或部署，服务器仍14b717a；应用修改仅runtime.py，配套单元/PG脚本/登记和记录保留待发布。
- 残余边界：数据库不可用、超过5秒或进程强制终止时仍可能无法保存，日志明示失败，不承诺必达；裸asyncio再次Task.cancel可越过AnyIO shield，不推断抵御任意强制取消。未完成工具回滚验证为合成待办/动作，不声称所有业务工具外部副作用可回滚；已执行外部操作无法靠数据库回滚撤销。长期审查未完成，下一独立方向可验证实际HTTPX模型断线和事务边界更复杂的工具场景，不擅自扩大生产验证范围。

## 2026-10-27 另一电脑新增修复复核：Q-028真实ASGI断线仍丢部分回复（中，已复现，未修改应用）

- 用户请求查看在另一电脑做的Bug修复。分别fetch GitHub/Gitee，两端pending-acceptance一致为e9151bc；相对本机974733e新增14b717a应用修复及d299b15/e9151bc两条文档提交。净变化仅agent/runtime.py（捕获GeneratorExit扩为同时捕获CancelledError）、test_agent_ai_safety.py新增62行、QUALITY_PROGRESS.md新增142行。没有把本机未提交发布记录或12项原文档删除当作新远端修改，也没有pull/reset覆盖工作区。
- 实际服务器DEPLOYED_COMMIT/WEB_DEPLOYED_COMMIT均14b717a8815aaae225cafb786d3cee509155ced5，service active、health up/database ok、OA stream connected；服务器runtime SHA256 b441c504b571cc0a867dc858c4c3f489725e151e7ddb618c8ab20d208ea4d298与Git blob逐字节一致。应用已由另一电脑上线，本机工作代码仍974733e；新审查应以实际服务器14b717a为准。CI40528819688 completed/success，链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40528819688 。
- 复核结论：修复覆盖手动向生成器athrow一次CancelledError，但没有证明Starlette断线的持续取消作用域中数据库补偿能完成。加载14b717a精确runtime源码到独立测试进程（不覆盖本机文件），新增单元原样通过；整份test_agent_ai_safety.py29项通过。该单元的Session.commit没有真实await数据库I/O，因此无法覆盖取消作用域再次中断提交。
- 真PG/ASGI反例：全新本机127.0.0.1:55439一次性crm_iso_agent_stream_rereview_test，迁移head；实际FastAPI Agent路由/StreamingResponse/get_db/真实AsyncSession，只替换鉴权为合成CurrentUser与模型为仅本地假流，不调用真实模型。ASGI spec2.3发送delta后receive返回http.disconnect，最终HTTP起始200、delta_sent true，但独立新Session查询只有user消息，没有assistant部分回复，连接池checkedout0。第二次仅增加commit观察代理，原SQL/提交不变：首次用户消息commit finished，断线补偿第二次commit entered后立即commit interrupted: CancelledError，实际仍只有user；期望两角色断言明确失败。服务器与本机StreamingResponse.__call__/stream_response/listen_for_disconnect逐函数源码SHA256完全相同，非仅按版本号猜测。
- 因而Q-028此前已确认现象在14b717a真实断线分支仍未解决，不另造重复问题编号；当前影响是页面断线后已看到的半段AI回复仍可能消失。本轮没有证明真实模型HTTPX客户端泄漏；假模型close里的await被打断不当真实连接泄漏证据，连接释放已由独立旧证据排除。也没有把未测工具中途取消导致半业务提交静态风险当成新确认Bug。
- 未改应用、回滚、部署或修改生产数据。后续方案应同时考虑取消作用域内有时限的保护与未完成工具事务隔离，不能简单对现有整个Session.commit加shield就宣称安全；这涉及事务/部分回复口径取舍，按用户原规则需要具体方案和批准后再实施。当前只做用户要求的复核并报告，不自动撤销另一电脑有效工作。
- 证据归档ops/backups/other-computer-stream-review-20261027/，包括精确远端源码/差异/远端记录、29单元、两次实际PG/ASGI失败及commit观察日志。合成ID精确清理后psql确认users/sessions/messages全0，专用库随后删除、临时PG停止。未推送/重启/真实外发，长期审查目标未完成。

## 2026-10-27 Q-033至Q-039 已发布服务器

- 用户本次明确授权“改动没问题可以直接部署服务器”，据此发布当前已验证7项修复；先前各条“本地未部署”保留为历史，状态由本条覆盖。最终应用固定SHA为974733edbde05c2e7b4087b4beae4c8ba4c27053，已同步GitHub/Gitee现有pending-acceptance分支。包含公海预告锁内归属复查/最新履约保护、旧轮OA回调隔离、软删打样系统动态/附件隐藏、时间线原模块权限过滤、ERP重复事件自动flush冲突处理、改派与签署/回款协调锁；不包含已归档人民币改动、Q-030隐藏提议或原12项文档删除。
- 发布门禁：本机最终1341单元与21发布安全测试通过，pyflakes/150登记/查询JOIN/输出判据/diff通过。首CI40486160173仅旧时间线分页夹具失败，按下条证据本地复现后只修测试权限，业务代码不变；最终同SHA CI40487656707 completed/success，后端150接口套件、静态/单元及UI构建/浏览器冒烟均通过。CI链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40487656707 。没有重跑忽略失败或红CI上线。
- 从git archive固定SHA与真实部署基线8bce10d构建：/tmp/crm-release-974733e.tar.gz，SHA256 6138b4f0eded9c3eb2a7e548a9800ee8d766bd1bc8c681b4347b04373f710e25；统一工具SHA256 8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab。冻结依赖离线安装成功，未改锁文件/升级依赖。远端/tmp/crm-release-974733e的工具与包逐项核对相同；服务器预检通过后才--apply。
- 统一发布工具在实际基线/配置/迁移head/空间/包完整性守卫通过后备份、短暂停止、切换、启动、核验后端及491个新包文件/公网首页与入口资源，返回status=verified。回滚备份/opt/crm-sales-agent/backup/unified-974733edbde0-3rkvw41e保留。未执行数据库迁移或修改.env/虚拟环境/业务数据/服务配置，未做真实导入或外发测试。
- 发布后独立SSH复核DEPLOYED_COMMIT/WEB_DEPLOYED_COMMIT/RELEASE.json均974733e完整SHA且verified；crm-sales.service active/running、NRestarts=0，8010健康status=up/database=ok；钉钉OA submission_enabled=true、sync_running=true、stream_connected=true。另独立核对新增customer/locking及timeline/settings源码哈希与本地一致。没有把服务健康等同真实角色端到端业务验收。
- 构建、两轮CI及夹具修前/修后、本地门禁、服务器预检/apply/独立postcheck、manifest与包摘要归档ops/backups/release-974733e-20261027/（首候选证据另保留release-2799559-20261027）。仅本机隔离测试库crm_iso_release_timeline_test用于复现夹具并已删除、API8029正常停止、临时PG停止。用户原文档删除保持未提交，当前应用代码与最终发布SHA一致。
- 本次部署请求已完成；长期全面质量目标未完成，不把本次发布泛化为未来独立部署授权或全项目无缺陷声明。后续以当前974733e实际服务器文件为新审查基线，历史快照仅用于取证。

## 2026-10-27 Agent 流式断开修复正式发布（14b717a）

- 用户明确授权“确定没问题可以部署”。本地已验证提交 `14b717a8815aaae225cafb786d3cee509155ced5` 已推送 GitHub/Gitee `pending-acceptance`，两端引用一致。
- 统一发布包以服务器实际 `974733e` 为基线构建；首次 Windows `git archive` 受本机 CRLF 配置影响，被服务器预检逐文件拒绝。改用临时 `core.autocrlf=false` 配置重新构建，未修改全局 Git 配置；包 SHA256 为 `aa4e7cb0d4a58c32921069d0f5f9c854ab342fb28e074cc72e8d4618ae9dcafd`。
- 发布工具自测 **21 passed**；服务器预检通过后执行 `--apply`，返回 `status=verified`，备份目录为 `/opt/crm-sales-agent/backup/unified-14b717a8815a-ao5nwird`。无数据库迁移、依赖变化或业务数据写入。
- 发布后只读核验：`crm-sales.service`、`crm-sales-web.service` 均 active；`DEPLOYED_COMMIT` 与 `WEB_DEPLOYED_COMMIT` 均为 `14b717a...`; `RELEASE.json` 为 `status=verified`；后端 `/health` 返回 `status=up`、`database=ok`、钉钉 OA stream connected；公网首页 HTTP 200。
- 服务器 `agent/runtime.py` SHA256 与 `14b717a` Git blob 一致，确认流式 `CancelledError` 修复已进入运行代码。首次预检失败没有停止服务或改动线上文件。
- 发布后残余边界：未用真实生产角色执行完整浏览器业务回归，未对真实 AI/钉钉/企微/ERP 网络故障做生产注入，也未完成全系统锁图和大规模性能基准；这些属于后续验证，不把健康检查和隔离测试扩大为全项目无风险结论。
- 线上运维复核：根分区约 64% 使用率（25G 可用），`crm-sales.service` 重启次数为 0；历史磁盘容量风险当前未复现。

## 2026-10-27 本地与 GitHub/Gitee 最新提交复核

- `git ls-remote` 复核结果：GitHub 与 Gitee 的 `pending-acceptance` 均指向 `974733edbde05c2e7b4087b4beae4c8ba4c27053`；本地 `HEAD`、`origin/pending-acceptance` 与 `github/pending-acceptance` 也均为该提交。
- 两个远端都没有返回 `main` 分支引用，因此不能把不存在的 `main` 当作落后依据。服务器 `DEPLOYED_COMMIT` 与 `WEB_DEPLOYED_COMMIT` 同样为该提交。
- 本地工作区另有未提交的 Agent 流式取消补偿修复及测试；这些改动未推送到 GitHub/Gitee，也未部署服务器，避免把未批准的本地修复误认为远端/线上版本。
- 本轮只做远端读取与状态核对，没有生产写入、发布或重启。

## 2026-10-27 后端全量回归复核

- 使用项目虚拟环境 `backend/.venv/Scripts/python.exe` 执行 `python -m pytest -q`，全量测试跑到 **100%**，退出码 0；随后以 `-rA` 复核，所有收集到的测试均为 PASSED。
- 本轮覆盖此前流式取消修复、权限边界及既有订单/回款/审批/企微/文件链路；未发现新增回归。测试仍只使用隔离测试资源，不连接生产数据库或真实第三方服务。
- 本轮没有新增应用代码修改、提交、推送或部署。

## 2026-10-27 人民币入口闸门静态复查（未发现新确认 Bug）

- 复核 `trade_mode.py` 的固定 CNY 白名单、`CURRENCY_GATE_SITES` 和实际写入调用链：报价创建、手工建单、订单草稿改币种三处调用 `ensure_currency_allowed`；成本导入另有只允许 CNY 的校验；继承路径和 ERP 外部同步按既定边界处理。
- 对审批、ERP 对账、回款和统计下游检查 `reject_foreign_currency`/外币提示，未发现把历史或同步外币静默当作人民币计算的新路径。测试中构造 USD 数据属于验证历史数据拒绝与展示边界，不是支持外币业务的入口。
- 后端全量测试已通过，未新增代码修改；本轮没有生产数据写入或部署。

## 2026-10-27 前端生产构建复核

- 在 `frontend` 执行 `npm run build`，TypeScript 检查和 Vite 生产构建均成功，3795 个模块完成转换，退出码 0。
- 构建保留既有大 chunk、依赖内 `eval` 和 MDX 空模块指令警告；它们来自现有依赖/打包结构，本轮没有证据证明造成业务故障，因此不擅自改依赖或拆包。
- 构建未产生未追踪的源码改动；没有推送或部署。

## 2026-10-27 服务器只读健康与版本复核

- SSH 只读检查显示 `crm-sales.service` 与 `crm-sales-web.service` 均为 `active`；本机健康接口返回 `status=up`、`database=ok`，钉钉 OA stream 当前为 connected。
- 服务器 `DEPLOYED_COMMIT`、`WEB_DEPLOYED_COMMIT` 与 GitHub/Gitee `pending-acceptance` 均为 `974733edbde05c2e7b4087b4beae4c8ba4c27053`，没有发现线上版本漂移。
- 本轮没有服务器写入、重启、发布或配置改动；本地未提交的流式取消修复仍未进入线上。

## 2026-10-27 鉴权运行时回归执行边界

- 选取现有 `test_file_access_authorization.py`、`test_customer_assignment_permissions.py` 与 `test_agent_ai_safety.py` 作为静态扫描后的运行时补证候选；使用项目自带 `backend/.venv/Scripts/python.exe` 执行，三组共 **46 passed**，退出码 0。
- 覆盖文件访问权限/数据范围、客户显式负责人权限、Agent 工具权限和跨模块读取边界；测试使用隔离替身/测试数据库，没有生产 HTTP、生产数据库或真实外部服务写入。

## 2026-10-27 公开业务路由鉴权入口扫描（未发现新确认 Bug）

- 对 `backend/app` 下全部 FastAPI `@router`/`@app` 路由做静态入口扫描，逐个追到对应处理函数签名及函数开头；业务模块路由均带 `require_permission`、`get_current_user`、`CurrentUser` 或等价显式登录校验依赖。
- 未筛出缺少鉴权依赖的业务写入口或可读取客户、订单、文件等数据的公开入口。认证登录/刷新、SSO 与钉钉回调、Webhook、健康检查等公开接口属于现有设计边界，未误报为越权问题。
- 本轮没有修改应用代码、没有提交、没有部署；静态扫描也没有替代真实 HTTP 权限矩阵验证，后续仍需在隔离环境抽样验证匿名请求与低权限用户请求的实际响应。

## 2026-10-27 导入/导出权限与范围复查（未发现新确认 Bug）

- 对照客户、线索、产品、SKU 导入/导出路由与业务规则：客户/线索导出分别经过独立权限或模块权限及数据范围；产品/SKU 是无负责人归属的全局产品主数据，使用 `product:view` 的全局目录口径与列表一致。客户导出额外执行 `export.limit`、审计和异常批量告警，未把仅针对客户导出的设计误扩成其它主数据缺陷。
- 检查 CSV 公式注入、模板列对齐、额外列、导出过滤和 CNY 规则相关回归；未发现能确认的数据范围绕过、导出字段越权或导入后回滚不一致。
- 隔离测试：`test_round4_review_fixes.py` 与 `test_settings_number_bounds.py` 共 **85 passed**，包含 CSV 注入防护、导出设置边界和币种闸门；无生产数据库或真实文件导入导出操作。
- 验证边界：本轮没有对全部大数据量导出做性能基准，也没有执行真实用户 HTTP 导入；产品/SKU 全局可见性是当前业务设计，若要改成按部门范围需要人工决定，不擅自收紧。
- 本轮无应用代码修改、无提交、无部署。

## 2026-10-27 数据库 Session 与后台任务资源生命周期复查（未发现新确认 Bug）

- 核对 `get_db`、应用 lifespan、定时任务、OA Stream、通知投递、分析快照和其它 `SessionLocal()` 独立会话：正常路径均使用异步上下文管理器；写入异常处已有回滚，读快照任务在结束时显式回滚，后台任务在应用关闭时停止。
- 取消异常不会被 `get_db` 的 `except Exception` 捕获，但 `async with SessionLocal()` 的退出仍会关闭会话并回滚活动事务；本轮没有证据把它认定为连接泄漏。Agent 流式部分回复丢失已由上一轮单独复现并修复。
- 修改文件 `runtime.py` 与测试通过 `compileall`；本环境未安装 `ruff`，未把缺少工具误报为代码失败。没有新增应用修改、提交或部署。
- 仍需后续在隔离 PostgreSQL 下观察连接池耗尽、取消时事务回收和长时间外部调用超时；当前只完成代码级/隔离测试级验证。

## 2026-10-27 Agent 流式取消修复的服务器状态核对（本地已修复，服务器未部署）

- 只读 SSH 核对服务器：`/opt/crm-sales-agent/app/DEPLOYED_COMMIT` 为 `974733edbde05c2e7b4087b4beae4c8ba4c27053`，`crm-sales.service` 为 active；服务器 `agent/runtime.py` 仍是 `except GeneratorExit`，未包含本地 `CancelledError` 修复。服务器文件 SHA256 为 `fe902adb59643e7cf8508818f6d6209dc6646520e566d4a02215830d53d44d37`。
- 本地代码基线仍为 `974733e` 加未提交的流式取消修复；因此不能把本地测试结果称为服务器已修复。没有执行服务器写入、发布、重启或配置改变。
- 当前待人工决定：是否将这项已经隔离复现且完整回归通过的纯收益修复纳入下一次正式发布。若批准，需按现有发布流程构造固定提交、重新跑 CI/发布前检查后再部署；在批准前保留本地改动，不动生产。

## 2026-10-27 异步流式/取消入口同类扫描（未发现第二处同类 Bug）

- 扫描后端全部 `StreamingResponse`、异步生成器、`request.stream()` 和 `CancelledError/GeneratorExit` 处理点：当前真正向浏览器逐事件输出的入口只有 Agent SSE；企微入口是请求体流式读取，OA Stream 是后台 WebSocket 任务，不共享 Agent 的部分回复落库语义。
- Agent 的取消路径已由上一轮修复；OA Stream 的取消、断线重连与失败不确认逻辑由 `test_oa_live_sync.py` 覆盖，文件上传的中断补偿由 `test_file_storage_boundary.py` 覆盖。本轮两组共 **18 passed**。
- 未发现其它入口存在“已向用户输出内容但取消后应保存却未保存”的同类确认问题；没有为了形式统一而修改无关流式代码。本轮无新增应用代码修改、提交或部署。

## 2026-10-27 前端 API 登录代次与 401 迟到响应复查（未发现新确认 Bug）

- 核对 `shared/api/client.ts` 与 `shared/store/auth.ts`：请求发送时记录登录代次，`setAuth/clear` 均递增并清理查询缓存；401 只有属于当前代次才清除登录态和跳转，旧账号迟到响应只向当前调用方抛错，不会把新账号踢下线。
- 检查了参数错误字段提取、空错误文案兜底、二进制下载/预览和 multipart 上传的拦截器接线；未发现当前候选的并发状态覆盖或错误恢复路径。
- `node --test ops/auth_epoch_401.test.mjs`：**4 passed**，含纯判据、旧账号迟到 401 和拦截器接线检查。本轮没有修改前端代码、没有真实 HTTP 或生产登录操作。
- 验证边界：当前客户端设计是 401 直接退出登录，并没有自动 refresh token 流程，因此不存在“重复刷新”可审查；未把没有该功能误报成 Bug。网络断开、Service Worker 和浏览器多标签时序仍需独立端到端验证。
- 本轮无提交、无部署。

## 2026-10-27 应收删除与回款自动/人工分配交错复查（未发现新确认 Bug）

- 核对应收删除、改额、回款确认、人工分配和自动重平衡的调用链。写路径统一先锁订单，再锁应收节点，涉及回款时再锁回款/分配；删除前检查回款记录与分配，改额在订单锁内重平衡并重算全部节点，确认先分配再重算状态。
- 特别复查了 `rebalance_order_allocations` 本身未重复取订单锁的疑点：它只由已经通过 `get_visible_plan(..., for_update=True)` 的改额入口调用，而确认/人工分配也通过同一订单锁序进入；因此当前代码下未形成可确认的删除后分配、金额重复或写偏斜路径。
- 隔离测试：`test_batch12_allocation_chain.py`、`test_payment_consistency.py`、`test_payment_rebalance_order.py` 共 **39 passed**，覆盖删除已有回款拒绝、空节点删除、人工归属保留、自动重平衡、币种/幂等和共同应收并发锁证据。
- 证据边界：本轮没有新增真实 PostgreSQL 删除与自动分配交错矩阵，也没有生产数据写入；现有代码锁序和隔离测试足以排除当前候选，后续仍需在有隔离 PostgreSQL 时补真实删除/分配交错验证。
- 本轮无应用代码修改、无提交、无部署。

## 2026-10-27 Agent 流式回复客户端断开补偿（中，已修复本地未部署）

- **问题与触发**：`agent/router.py` 使用 Starlette `StreamingResponse`；在当前运行环境中，客户端刷新/断开会向异步生成器注入 `asyncio.CancelledError`。`agent/runtime.py` 原先只在 `GeneratorExit` 分支保存 `streamed_parts`，所以模型已经流出部分文字后断开时，用户消息已提交，但部分助手回复不会写入 `agent_messages`，刷新后会话历史缺少已看到的内容。
- **复现证据**：用当前 Starlette 的最小 ASGI 流测试观察到断开路径为 `CancelledError`；再用隔离假模型流逐事件驱动 `run_turn_events`，先收到 `delta='部分回复'`，随后注入 `CancelledError`，修复前只有用户消息提交，没有 assistant 部分回复，模型客户端仍进入关闭流程。
- **修复**：`run_turn_events` 将 `GeneratorExit` 与 `asyncio.CancelledError` 统一进入已有的部分回复补偿逻辑，提交“部分回复 + 连接中断”并继续抛出原始取消异常；新增 `test_stream_cancel_persists_partial_reply_and_propagates`，不吞取消、不改变正常完成或工具确认流程。该修改只恢复已实际流出的会话事实，属于纯收益且无业务口径取舍。
- **验证**：`tests/test_agent_ai_safety.py` **29 passed**；全部 `tests/test_agent_*.py` **55 passed**；`git diff --check` 通过。测试使用隔离替身和假模型，不调用真实模型、生产数据库或外部服务。
- 在本地修复保留后又运行后端完整 `python -m pytest -q`，进程退出码 0；前端构建此前已通过。完整回归未发现其它模块回归失败。
- **边界**：本地修复尚未提交、推送或部署；尚未做真实浏览器断网和真实模型网络中断验收。数据库提交本身若在进程被强制终止时无法完成，仍需后续按隔离 PostgreSQL/ASGI 场景验证。

## 2026-10-27 前端浏览器冒烟补证（样品/企微/设置/回收站/报价）

- 使用当前本地 `frontend/dist`、临时 Chrome profile、临时 HTTP mock API 和内存状态运行 `CRM_SMOKE_BROWSER=... node ops/confirmation_browser_smoke.mjs frontend/dist`；不连接生产 API、数据库或外部服务。脚本输出全部既有断言为 `true`，进程正常结束。
- 新增证据覆盖：样品 61/62 批准、63 重提、64 寄样、65 签收、66 修订、67 审批取消确认；企微四类同步与候选客户关联；报价撤回/历史版禁撤回/删除取消及成本提示；回收站产品、SKU、客户、样品、合同恢复；设置批量回收决策与自动规则；并验证取消不提交、确认只提交一次、指定对象 ID/版本/接口正确。
- 同一脚本还复验任务、通知补投、线索/公海领取、客户负责人分页选择、报价成本提示、产品/SKU 成本入口和客户导出权限边界。未发现前端裸提交、对象串行错位、取消后仍写入或成功后页面状态未闭环的新增证据。
- 验证边界：mock API 证明的是当前构建的浏览器交互、请求和刷新逻辑，不证明真实后端事务、网络断线、真实浏览器生产数据或第三方接口；因此不把它描述为生产端到端验收。构建产物与源码均未修改，未提交、未部署。

## 2026-10-27 样品/企微前后端入口闭环复查（未发现新确认 Bug）

- 对照后端 `sample/router.py` 与前端 `SampleListPage`、`shared/api/sample.ts`：新建、深链详情、软删/恢复、审批/重提、寄样/签收/反馈、制作完成、开新修订版、明细及制作依据附件均能找到对应入口、请求参数和成功后的刷新；未把钉钉内部回调/导出接口误算成缺失用户按钮。
- 对照后端 `wecom/router.py` 与前端 `WeComPage`、`shared/api/wecom.ts`：同步部门/成员/外部联系人/跟进关系、待确认客户关联/建档/忽略、交接预览/执行/逐项重试、同步记录和结果清单均有页面调用；页面的 `wecom:manage`、客户创建/分配能力门槛与后端权限要求一致。
- 前端生产构建：`npm run build` 成功（TypeScript 检查和 Vite 构建均通过）。构建仍报告既有依赖 `lottie-web` 的 `eval` 警告及大 chunk 警告；这些已在既有记录中列为保持现状的风险，本轮没有证据证明造成业务故障，也没有擅自改依赖或拆包。
- 验证边界：本轮是源码调用链与构建验证，没有启动浏览器、真实 HTTP 写入或生产数据操作，因此不把“入口存在”扩大为完整浏览器端到端证明。当前未发现应当新增页面/按钮的明确遗漏；继续覆盖仍未逐一点击的回收站、设置批量动作和样品各状态真实浏览器流程。
- 本轮无应用代码修改、无提交、无部署。

## 2026-10-27 定时任务、逾期提醒与通知重投复查（未发现新确认 Bug）

- 核对 `core/scheduler.py` 的任务注册、`max_instances/coalesce/misfire_grace_time`、OA 单独开关及 Session 生命周期；同时检查自动任务、报价/合同到期提醒、跟进逾期提醒、逾期节点/批次通知和企微失败重投的提交边界与去重条件。
- 已确认的保护链路包括：扫描共享 PostgreSQL advisory lock、到期提醒按 `source_key`/业务对象去重、逾期通知按独立时间戳去重、投递行 `FOR UPDATE SKIP LOCKED` 防重复发送、失败重试退避与上限、手工补投复用原通知行。未找到可由现有代码和当前测试确认的重复建单、重复投递、状态覆盖或连接未释放路径。
- 隔离测试：`test_oa_scheduler_activation.py`、`test_overdue_reminder_state.py`、`test_contract_oa_notification.py` 共 **29 passed**。未启动生产调度器、未调用真实企微/OA、未写生产数据库。
- 证据边界：多实例部署仍依赖正式配置只启用一个调度器，这是既定运行约束；本轮没有把配置误用或真实多进程网络故障当作代码 Bug。尚未做大规模数据量性能基准及真实企微超时重试验收。
- 本轮无应用代码修改、无提交、无部署。下一独立方向转向尚未充分覆盖的前后端闭环或订单/回款删除与自动分配交错场景。

## 2026-10-27 支付凭证上传失败补偿复查（未发现新确认 Bug）

- 逐行核对回款凭证上传、通用文件上传、流式存储和 `discard_unregistered_upload`：写盘后登记/审计/提交任一异常会先回滚，再按本次 `object_key` 查询登记；确认库中没有记录后才精确删除，避免把已提交记录对应的文件误删。`CancelledError` 单独补偿，上传流中断或超限由存储层清理半成品。
- 也复查了凭证删除的文件行锁、共享引用/受保护原件判据和提交后磁盘删除边界，未发现能由现有代码确认的孤儿文件、误删共享原件或重复删除路径。
- 隔离测试：`backend/tests/test_file_storage_boundary.py` **9 passed**，覆盖路径穿越、符号链接越界、读取中断、超限和正常写入；未调用生产数据库、真实对象存储或外部服务。凭证专用入口缺少独立 HTTP 断流测试，暂列验证边界，不据此推断已完成端到端证明。
- 本轮无应用代码修改、无提交、无部署。长期质量审查继续，后续仍需覆盖文件与业务挂载并发及真实 HTTP multipart 中断场景。

## 2026-10-27 报价转订单与 ERP 源单链路复查（未发现新确认 Bug）

- 以当前同步基线 `974733e` 检查报价版本转订单、订单草稿确认和 ERP 订单推送/回调。报价转订单调用链逐项核对：数据范围、报价/版本锁、当前版本、客户未删除、审批通过、成本完整、客户接受、发送状态、报价有效期、逐行金额与报价小计一致、订单 `quote_version_id` 唯一约束、订单明细快照、应收生成和订单过程记录；未发现能在现有规则下确认的绕过或重复写入路径。
- 订单草稿确认复查了来源明细多重集（含重复 SKU）、客户/商机一致性、币种/付款条件、修订号和转单幂等；未把历史外币兼容代码误报为新入口漏洞，实际调用方指定币种仍经过固定 CNY 闸门。
- 隔离测试：`backend/tests/test_quote_cost_gate.py`、`test_order_drafts.py`、`test_batch14_order_safety.py` 共 **46 passed**；`test_erp_callback_and_push.py`、`test_erp_event_conflict.py`、`test_erp_external_sync.py`、`test_batch13_erp_repairs.py` 共 **61 passed**。测试均为本地隔离/内存环境，无生产数据库、真实 ERP 或外部消息调用。
- 证据边界：未做生产业务写入，也未把专项单元测试当作真实 ERP 合同、浏览器端到端或大数据量性能证明。报价转订单与客户改派的锁策略仍受 Q-039 待决定项约束；ERP 真实第三方字段合同仍需外部资料，不能由模拟回调推断已完成真实联通验收。
- 本轮无应用代码修改、无提交、无部署。下一独立方向继续选择尚未充分验证且不依赖待决定事项的高风险链路。

## 2026-10-27 发布前CI时间线夹具修正（测试修改，无应用变化）

- 用户明确授权“改动没问题可以直接部署服务器”，据此准备Q-033至Q-039发布。首个固定提交2799559已同步GitHub/Gitee，1341单元/21发布安全测试/静态/登记检查、本地构建及服务器预检通过。远端CI40486160173的浏览器冒烟通过，但150接口套件中仅check_customer_timeline失败于分页夹具断言，其余接口套件通过；未在红CI时上线。
- 真实隔离PG+本机8029API复现同一失败：脚本手工构造viewer只有customer:view/order:view，却要求聚合返回6条跟进；Q-037经用户批准要求followup:view，模块门禁正确返回空。修复只给分页/文案探针夹具补followup:view，并给订单越范围探针补该权限以确保真正验证来源范围；模块无权断言保留，6条/105隐藏行分页、来源授权、文案与其它断言不变。实际HTTP/SQL套件修后exit0，没有改应用或放松权限。
- 服务器仍8bce10d，首包尚未apply。最终发布将改用包含该测试修正的固定SHA重新构建并等待完整CI，不把首CI失败当应用已发布失败。日志归档见本次release证据目录，原文档删除保留。

## 2026-10-27 回款凭证与确认/驳回并发状态边界（真PG两套各10组通过，无应用修改）

- 上轮Q-039完成获批修复，构成实质进展；本轮选择此前未充分验证的凭证上传/删除与确认/驳回交错。直接SSH读取服务器payment/router只读快照，AST逐函数核对upload_payment_voucher/delete_payment_voucher/reject_payment与当前本地完全相同；确认入口本地含Q-039协调锁，所以另在隔离进程加载服务器原版路由运行同一矩阵，未覆盖工作区文件。两套均通过，不把本地新行为误称服务器已部署。
- 环境为本机127.0.0.1:55439一次性PG，两个真实AsyncSession，先请求取得真实订单锁后暂停，再以独立pg_blocking_pids确认另一请求真实等待；两边预加载pending回款缓存，实际业务函数锁后刷新、写入和提交。合成CNY订单1元/一期应收1元/待确认回款1元，实际UploadFile流和临时磁盘，权限以CurrentUser/all构造，不声称HTTP鉴权/浏览器新证据。推送/调度/Stream关闭，无app lifespan、真实OA或外部发送。
- 每套10组：上传→确认200/200且confirmed有凭证；确认→上传200/400且confirmed无新文件；上传→驳回200/200且rejected保留已传凭证；驳回→上传200/400且无新文件；删除→确认200/200且confirmed无凭证；确认→删除200/400且原凭证保留；删除→驳回200/200且rejected无凭证；驳回→删除200/400且原凭证保留；重复上传200/400且只新增一个文件；重复删除200/404且只删除一次。确认无需强制凭证是现有明确入口行为，不擅自增加业务门槛。
- 各组实际查询支付终态、分配/已收守恒（确认1元，其余0）、应收paid/pending、文件行/磁盘增减、保留内容size/SHA256、成功审计数量和无关合同原件完整性。未发现终态后新增/误删凭证、孤儿写入、金额重复或审计丢失，不增加应用补丁/重复永久套件，也不重跑无应用变化的完整单元。
- 初稿断言使用含await的生成器传all，产生测试驱动TypeError；改为显式逐文件读取。初稿异常清理没收集刚上传文件ID，独立核对后仅删除该驱动确切id238/uploaded_by59/合成文件名的隔离记录，再补清理按本轮合成用户明确范围获取文件ID；最终两套各10组均exit0。没有修改实际业务校验或放宽断言获得通过。
- 脚本、当前与服务器原版两份日志、原版路由快照、哈希、初稿错误归档ops/backups/payment-voucher-state-race-review-20261027/。最终独立psql确认users/customers/orders/payments/allocations/files/audit均0，临时文件清理且PG停止。无生产数据写入/配置/服务变化，无提交、推送或部署，既有Q-033至Q-039及用户文档删除保留。
- 边界：本轮验证真实PG直接业务调用及本地文件，未验证HTTP multipart断流/提交回执丢失补偿、文件被其它业务并发挂载、共享原件保护的所有锁图；相关顺序用例已有历史证据但不当本轮新并发覆盖。下一独立方向可验证凭证上传失败/回执丢失的文件补偿，或实际读取端的缓存/下载状态；长期质量目标未完成。

## 2026-10-27 Q-039 用户接受等待后完成协调锁修复（中，本地验证，未部署）

- 用户在通俗举例后明确回复“接受”，授权这些操作互相等待，先本地修好测试、不上线；覆盖下方Q-039待决定状态。再次SSH只读核实部署仍8bce10d，合同service/router、回款service/router、客户service原始哈希与修前基线一致。本轮范围明确为合同登记签署、财务确认回款与客户改派，不改变归属、签署、金额、取消规则。
- 实现采用比客户排他锁更窄的协调锁：customer/locking.py新增protect_customer_transfer，签署/确认在取得任何合同、订单、应收、回款或文件锁前，先取客户FOR KEY SHARE，持有到原事务提交/回滚。它与改派既有FOR UPDATE互斥，让后到请求先等客户归属处理结束；与普通进展UPDATE的NO KEY UPDATE及外键KEY SHARE兼容，避免粗暴改成客户排他锁后与已有订单→客户更新入口形成新的循环，也保留同客户不同单据的共享协调能力。没有新表、索引、迁移、全局互斥器、依赖或自动重试/外部调用。
- 合同get_doc_or_404、回款get_visible_payment新增仅相应入口启用的protect_transfer选项，普通读取及其它写入口原锁序保留。先只读所属客户标识，协调后再取得原锁并刷新对象；客户协调查询本身populate_existing刷新已有客户缓存，既有权限/状态检查照常在等待后执行。发现合同/订单所属客户在间隙变化则409拒绝，避免持错客户锁继续写入。
- 永久套件check_customer_transfer_document_race.py已登记，清单现150项。13组真实本机PG交错全部通过：原合同签署/作废生命周期5组；签署/确认分别先于改派、晚于改派共4组均200/200；改派先完成后，预加载旧客户/订单/合同的旧负责人self范围两组均200/403，未留下成功签署/确认审计或事件；同客户进展更新及其他客户改派兼容；订单取消先锁订单、确认持客户KEY SHARE后等待，取消仍完成、随后确认400（取消已按原规则驳回待确认回款），不形成新增死锁。均实际调用业务路由/SQL，独立pg_blocking_pids证明等待，核对签署引用/状态、金额守恒、负责人一致及成功审计/事件次数；未声称HTTP鉴权或浏览器并发验证。
- 永久断言的修前对照：仅在测试进程撤掉协调函数、保留其它代码及真实业务操作，签署和确认两组分别明确失败为[200,40P01]；修后13组同断言全部通过。初次对照驱动在一侧异常时未等待另一任务结束，导致清理等待测试连接；仅终止该驱动的两条已核实隔离PG连接并完成精确清理，修正套件为捕获DBAPIError回滚且等待两任务结束，最终两份修前失败可正常退出，无残留连接。此为验证驱动错误，未记成业务连接泄漏。夹具曾漏应收到期日、把没有已确认回款的汇总字典误当必有键、清理漏订单状态历史；均补齐实际模型/既有规则，没有放宽业务断言。
- 完整后端1341项通过（实际进程exit0，收集数另核实）；最终PG套件exit0、pyflakes、diff、150登记唯一且文件存在、987条查询JOIN/2176函数输出判据检查通过。误配置缺DATABASE_URL、正式库名、远端地址在导入应用前拒绝，即使ALLOW_DEV_TARGETS=1也不能绕过本套件额外守卫。证据归档ops/backups/customer-transfer-document-fix-20261027/。
- 测试仅127.0.0.1:55439一次性隔离PG，合成OA通过记录/临时文件，推送/调度/Stream关闭，无app lifespan或真实外部调用。按明确夹具ID清理，独立psql确认users/customers/orders/contracts/payments/allocations/history/files均0，测试PG随后停止。没有生产写入/配置/服务变化，无提交推送部署；Q-033至Q-038及用户原文档删除保留。
- 边界：消除已证明的两个入口与改派循环，不宣称全系统所有交易锁图已无死锁；其它订单/报价等留痕入口与改派仍单列待调查，不因本次批准扩改。KEY SHARE会等待改派/删除等排他客户操作，实际等待受其它事务和数据库超时影响，没有生产大批量时延证明。长期目标仍未完成，下一方向可独立验证尚未覆盖的来源变更/审批关联完整性，或按具体新证据检查其它跨模块锁序。

## 2026-10-27 合同审批关联真实鉴权HTTP矩阵（通过，无应用修改）

- 在Q-039待用户决定期间检查独立的审批关联读取/写入边界，后收到批准转入上述修复。直接SSH核对customer_flow、contract/router、visibility及OA model与本地原版同哈希。新建FastAPI+真实登录会话/JWT/角色self权限、全新内存SQLite，只替换get_db和外部审批读取为合成返回；不使用真实钉钉客户端、无app lifespan，配置默认外呼关闭，测试进程临时打开路由检查闸门以进入已完全替代的读取函数。
- 实际HTTP证明错误流程422、错误发起人403、错误编号422、错误名称422、缺正文附件422，均0关联/0审计；其他负责人合同403且在合成审批读取前拒绝。合法关联200后相同实例重复200，关联/审计均只1条且不再读取提供者；已有pending时换实例422且无提供者读取。真实同步路由把第一轮变withdrawn后第二实例成功成为round2/pending，旧轮保留withdrawn、合同快照指向最新实例。撤销order:manage后同一JWT下一次同步403且未触达提供者。
- 未发现新的已确认问题，不改应用或增加重复永久单元；既有单元已覆盖字段校验，本轮增加的是实际HTTP、数据库无残留/轮次与撤权链路证据。初稿夹具漏created_at及system_settings表，补齐后全部断言通过，不记成业务Bug。脚本/最终日志/首个夹具错误归档ops/backups/contract-link-http-review-20261027/，临时内存库/文件销毁。边界：没有验证真实钉钉字段/文件正文或金额一致性、PG同时关联、浏览器及外部系统交付；名称/编号匹配通过不能据此宣称附件内容一致，未擅自新增审批金额/正文比对规则。

## 2026-10-27 Q-039 客户改派与合同签署/回款确认锁顺序冲突（中，真PG已复现，待决定）

- 新方向检查合同签署/作废及跨客户改派的并发边界。直接SSH核对部署仍8bce10d；contract/service、router、customer_flow、file/service及customer/service、documents、followup/service、payment/service、router逐项与本机执行文件同SHA256。后五项本轮再次读取服务器核实，完整本机哈希及可重现脚本/日志归档ops/backups/customer-transfer-deadlock-review-20261027/。没有用服务器残留前端源码替代实际运行版本。
- 普通合同生命周期5组真实PG交错通过：签署→主管作废200/200；作废→签署200/400；重复签署200/400且仅首份签署原件；签署→普通业务员作废200/403；重复作废200/400。先请求真实持有合同锁后暂停，第二请求实际等待由pg_blocking_pids证实；合同状态、签署挂载、审计/系统事件成功次数及物理文件字节一致。使用合成已通过OA实例和临时原件，不调用真实OA；通过直接调用实际路由并构造CurrentUser验证，未声称实际HTTP鉴权或浏览器验证。
- 新反例1：实际登记合同签署持有合同/来源订单/文件锁，之后通过系统留痕更新客户进展；另一个Session实际transfer_customer(move_documents默认开启)先持客户锁，再迁移订单负责人。先在签署持有文件锁处暂停，改派进入等待订单后继续签署，独立观察到双方pg_blocking_pids互相指向，数据库返回SQLSTATE40P01；本次签署成功、改派回滚。客户和订单负责人一致，无半份签署或孤儿签署挂载。
- 新反例2：实际confirm_payment持有订单锁后暂停，同时实际客户改派持客户锁并等待订单，再继续确认，双方再次真实循环等待并出现40P01；本次确认成功、改派回滚，已确认总额70、分配70，客户/订单负责人一致，没有半确认回款。故确认影响为正常同时操作中一次失败，需要重新操作；数据库回滚避免本次已测数据半写，不称生产数据损坏或真实线上事故。直接路由异常未转换成实际HTTP500证据，不把静态未重试推断写成已测HTTP结果。
- 原因：合同签署/回款确认采用订单→客户，客户改派并迁移单据采用客户→订单，存在反向锁序；合同还涉及合同、文件、任务锁，不能仅凭一个局部改动声称全系统死锁已排除。其它订单/报价留痕入口仅为同类待调查，不计已确认。
- 推荐统一这两类操作与改派的客户/订单取锁顺序，让同一客户的冲突操作排队完成，并保留等待后状态、归属和权限复核。收益是消除已证明的循环；代价是同客户不同订单之间也可能等待、锁持有时间变长，需验证合同/任务/OA相关锁图和反向交错。备选保留现状，接受该窗口一次失败后由用户重试；自动重试可能涉及外部副作用，不作为未经评估的替代补丁。按AGENTS.md“涉及业务规则、权限、数据清洗、数据库结构、生产环境或用户体验取舍时，先记录并请求人工决定”及用户完整目标对事务策略的要求，当前不改应用，待用户选择后先本地修复验证，不部署。
- 环境严格为127.0.0.1:55439隔离库crm_iso_pool_owner_review_20261027，两独立真实AsyncSession，所有测试推送/调度/Stream关闭，无app lifespan/生产数据库写入/真实外部发送。按本轮明确ID清理，独立psql核实users/customers/orders/contracts/payments/allocations均0；测试PG随后停止。没有提交、推送、部署，Q-033至Q-038及原文档删除保留；未重跑与无应用改动无关的全量单元。
- 下一步：获批后建立永久修前失败回归并验证两个操作方向、改派后的权限刷新、金额/签署/审计完整性、不同客户不互相阻塞；等待期间可独立审查不改事务策略的其它链路。长期全面审查仍未完成。

## 2026-10-27 ERP日志/异常队列权限与未匹配恢复（真实鉴权ASGI HTTP通过，无应用修改）

- 上一轮Q-038真PG/HTTP反例及修复为实质进展；本轮转向不同调用链的日志读取、异常队列计数及“先未匹配、后补齐订单”恢复，不重复相同并发去重测试。直接SSH核对部署仍8bce10d，erp/service原始c325d06c…、router d920bbb8…、integration/model df4781e7…；service本地含Q-038补丁，所以另拉服务器原版只读快照，逐函数验证sync_logs/exception_queue/serialize_log/_mark_superseded源码与本地完全相同。
- 两套实际HTTP运行均通过：一套使用当前本地代码，一套在独立测试进程中把ERP service替换为上述逐字节核验的服务器原版再跑相同断言（不覆盖本地文件）。全新内存SQLite、新建FastAPI加载实际ERP及鉴权路由，只替换get_db；真实JWT/登录会话/角色权限/self与all范围读取，每次请求新Session、同一枚JWT持续使用。显式DATABASE_URL为localhost:1不可连接隔离地址，JWT和回调密钥为随机测试值，ERP外呼仅配置本机不可用地址/合成凭据，无app lifespan或外部网络调用。
- 日志权限矩阵：合成本人/同事订单异常、无归属unmatched/conflict、非订单unknown及非ERP日志。普通self账号两入口只返回本人一行且total1，日志按另一个订单ID筛选仍total0，响应不含其他客户/未归属/非订单哨兵内容；all范围看到5条ERP日志、非ERP仍排除，page_size1的第1/2页与total5一致。撤销order:view后同一JWT对两入口均403。初稿把本人夹具设成failed，异常队列默认仅含conflict/unmatched/unknown/skipped/not_sent，导致测试预期不符；按既有队列定义把要测试的异常夹具改为unknown后通过，未修改默认状态集合或把该夹具错误记成Bug。
- 恢复链：同一合法回调连续两次未匹配，均200并完整保留原始JSON（包含额外partner_trace）；隔离夹具随后创建匹配单号的CNY订单，重放原事件后仅一条success/一次状态历史/订单in_production，前两条unmatched均变superseded并指向本次成功行，原始报文仍原样保留。异常队列不再包含该事件旧账；随后再重放返回replayed true，日志总数保持3。补齐订单由夹具事务插入，不声称验证了ERP人工匹配API或CRM整套建单流程。
- 本轮未发现新的已确认Bug，不增加应用补丁/永久镜像用例，不重复跑无关全量单元。脚本、当前代码/服务器原版两份成功HTTP日志、初稿夹具错误日志、服务器原版源码与双方/函数哈希归档ops/backups/erp-log-scope-recovery-review-20261027/；测试内存库/临时文件均清理，未启动PG，无生产数据库或真实外部操作，没有提交、推送、部署，Q-033至Q-038及用户原文档删除保留。
- 边界：SQLite顺序恢复不能证明PG并发/索引竞争，相关真实PG证据只援引Q-038而不扩展；本轮没有证明不同事件键乱序、双编号冲突被人工改派后的竞争、全量诊断配置或生产真实权限配置。未把未匹配/冲突仅全量可见当成遗漏，也未把内部回调恢复能力直接当成必须新增前端按钮。下一独立方向可检查其它聚合/批量入口或合同撤销与签署的幂等，避免继续围绕同一已通过事件反复扫描；长期目标未完成。

## 2026-10-27 Q-038 重复ERP回调在查询自动flush冲突时返回500（中，已复现，本地修复未部署）

- 上一轮应收删除4组真PG交错新增证据为实质进展；本轮转向ERP状态回调，既有测试只覆盖普通重放/单元替身，不把它们的通过当真实并发已充分证明。SSH直接核对实际部署仍8bce10d，erp/service.py原始c325d06c…、router.py d920bbb8…、order/service.py 9fef9ced…、integration/model.py df4781e7…与修前本机文件逐项同SHA256。另只读读取服务器配置的drivername，确认postgresql+asyncpg，不输出连接地址或凭据。
- 两个同源问题：apply_status_webhook先查成功事件，再处理并把log.status置success；随后_mark_superseded执行ORM查询，会自动flush该成功行并碰成功事件部分唯一索引，但原try只包后面的commit，异常提前漏出。即使把查询放进try，原_is_event_key_conflict也只看psycopg的orig.diag.constraint_name；实际SQLAlchemy asyncpg适配器把原生UniqueViolationError及constraint_name留在orig.__cause__，仍认不出当前事件约束。
- 真PG+实际ASGI HTTP修前证明：本机127.0.0.1:55439一次性库crm_iso_pool_owner_review_20261027；新建FastAPI加载实际ERP路由/错误处理，只替换get_db为真实隔离AsyncSession包装，在两请求真实初始成功事件查询返回后暂停控制交错。两边均先查到无成功事件，再依次继续；合法随机回调密钥真实校验，实际Jushuitan状态映射（仅本地取常量，无外部请求），第二请求HTTP500，日志明确Query-invoked autoflush触发uq_integration_logs_erp_success_event。数据库成功日志1、状态历史1、订单in_production，故问题是正常重放误报失败/多余重试，不是数据库已被重复改写。
- 分步验证保留：永久check_erp_callback_race.py在修前同样200/500失败；仅扩try后仍失败，诊断明确orig无constraint_name，而orig.__cause__为UniqueViolationError且constraint_name精确匹配。两个点一起修后200/200且第二replayed true。没有通过关闭autoflush、放宽唯一约束或删除断言获得通过。
- 最小修复仅erp/service.py：把原_mark_superseded调用移进既有try；识别器兼容原错误及其直接cause上的精确constraint_name，同时保留psycopg diag形式。仍只接受现有指定事件唯一约束，不能靠错误文本里出现名字判重放；其他IntegrityError继续向外抛出，不吞真实写入错误。已有rollback→重读成功事件→返回重放逻辑原样复用；没有新锁、隔离级别、提交时点、索引、业务状态规则或外部协议字段。正确行为由原有去重承诺和部分唯一约束明确限定，符合已授权无实质取舍纯Bug修复，不需要选择新的业务/事务策略。
- 永久真实PG/HTTP套件登记ops/check_suites.txt，现149项：初始pending的重复并发只变更一次（1成功日志/1历史），初始in_production的同状态重复并发不改状态（1日志/0历史）；两种随后串行重放不再新增日志。已取消订单仍返回blocked_reason、保持cancelled，失败事件两条可重试日志符合原口径；错误密钥403且进入服务查询前拒绝。套件仅允许显式本机一次性PG，缺DATABASE_URL/正式库名/远端地址三种配置拒绝，即便ALLOW_DEV_TARGETS=1也不绕过本套件额外守卫。
- 永久单元test_erp_event_conflict.py新增9项：psycopg diag/asyncpg适配器cause/原生constraint_name三种精确名称正反例，错误文本仅包含名字不足为据，查询阶段特定冲突回滚后重放、其他约束冲突原样抛出。相关28项通过，完整后端1341项通过（/tmp/crm-erp-callback-full.log，实际进程exit0），修改文件pyflakes、149登记、diff、查询JOIN/输出判据检查通过。
- 证据归档ops/backups/erp-callback-race-fix-20261027/，包括原始/永久修前、仅扩try仍失败、驱动诊断、最终3组真PG/HTTP、单元/全量日志与双方哈希。只清理本轮明确客户/订单/事件键，独立psql确认customers/orders/history/integration_logs均0，临时PG停止。ERP外呼地址和凭据仅测试进程设置为本机不可用地址/合成值，回调密钥随机且不打印，推送/调度/Stream关闭，无生产业务写入/真实外发/生产服务变更，无提交推送部署。Q-033至Q-037及原文档删除保留。
- 边界：本轮覆盖同事件并发/串行重放和取消终态，不证明不同事件键之间的业务时间乱序策略；真实协议尚未验收的事件ID/业务时间字段不擅自发明，也不把允许纠错的状态回退静态认定为Bug。未做生产外部系统真实投递、网络故障重试或浏览器新验证。Q-038已本地修复，长期全面审查未完成，下一方向可独立核对映射冲突恢复/异常队列范围或其他未充分覆盖链路。

## 2026-10-27 应收删除与人工分配/清空交错（真PG 4组通过，无应用修改）

- 上一轮Q-037获批修复及10项新回归/HTTP证据为实质进展。本轮承接之前只读过的删除守卫，验证尚未逐交错证明的删除与“回款原本登记在别期、现在改分到目标期”竞争；不把既有确认/驳回、生成幂等或上轮移期/改额测试重新当新成果。
- 直接SSH读取实际删除实现并核对部署仍8bce10d；payment/router.py 3e998aa7…、service.py a72f012c…、order/milestones.py b31778a1…与本机隔离执行文件逐项同SHA256。删除入口get_visible_plan(for_update=True)先锁订单再锁节点，锁内同时查原登记PaymentRecord和实际PaymentAllocation；人工调整通过get_visible_payment走同一订单锁，目标期取数在等待后发生。
- 真PG环境仅本机127.0.0.1:55439的crm_iso_pool_owner_review_20261027，开头确认用户/客户/订单/应收/回款空。每组独立合成CNY订单150、两期50/100、首笔已确认50原分一期、另一笔待确认20也登记一期，目标二期没有直接登记回款。调用实际delete_receivable/update_payment_allocations及真实SQL/提交/审计/跟单同步，在先请求实际拿到订单锁后暂停；后请求通过独立pg_blocking_pids证明真实等待，不替换锁或业务写入。CurrentUser仅为隔离财务/all夹具，未新增HTTP鉴权/浏览器声明。
- 四组全部通过：①先删除空二期，再移50到二期：删除200、分配404，50仍在原一期auto，目标确已删除、没有孤儿分配；②先把50人工移到二期，再删除：分配200、删除422，二期及50人工归属保留；③二期原有人工50，先清空整笔分配再删除：两者200，50按manual空决定保持待分配、二期删除；④同初态先删后二者清空：删除422、清空200，二期保留但50待分配，未因后来清空倒推第一次删除应成功。
- 每组独立查询分配行及目标存在性，已确认总额始终50（待确认20不计入），分配＋待分配守恒；成功删除恰好1条删除审计、被拒删除无成功审计。实际OrderMilestone也验证：deposit为已确认最早到账日，payment仅在删空二期且剩余一期确实收齐时完成；清空人工分配后不假完成。未发现孤儿分配、误删、金额丢失、错误节点或所测锁序死锁，不为增加修改量加入应用补丁或镜像测试，也不重跑无关全量单元。
- 脚本/四组成功日志/服务器实际实现与哈希归档ops/backups/receivable-delete-allocation-review-20261027/。仅按本轮明确ID清理，独立psql确认users/customers/orders/plans/payments/allocations/milestones/audit均0，临时PG已停止。推送/调度/OA Stream关闭，没有生产数据库、真实外部发送或生产服务变更；Q-033至Q-037有效本地修改及原文档删除保留，无提交推送部署。
- 边界：本轮覆盖人工分配/清空与删除，不覆盖另一笔确认自动选中目标期、新增登记直接挂目标期、删除订单或全系统大批量锁图。代码守卫对所有回款状态检查，但没有把静态阅读当所有状态的并发新验证。下一独立方向可沿自动分配与删除的目标变化做复核，或转向尚未充分覆盖的合同/外部回调幂等；长期全项目目标仍未完成。

## 2026-10-27 Q-037 用户批准后统一时间线任务/跟进权限（中，本地修复验证，未部署）

- 用户明确选择“按原有权限过滤动态”，授权先本地修复测试、不上线，覆盖下方待决定状态。收紧的是聚合入口原有旁路，不改任务/跟进详情权限，不把客户查看权升级为其他模块查看权，也不把Q-036删除后隐藏扩成无关业务类型规则。
- 最小实现仅timeline/service.py：跟进段先检查followup:view或管理员特例，SQL复用followup_visibility_filter（同时含既有跟进关联优先级范围和系统来源过滤）；人工跟进继续按现有客户协作规则可见，不按记录作者一刀切。任务段先检查task:view或管理员特例，再复用scoped_owner_ids给Task.owner_id加SQL范围条件。两类均在排序/limit之前过滤；customer/lead/opportunity共用同一聚合逻辑，有权内容/全量范围/管理员照常可见。没有新权限码、UI入口、表结构、锁、依赖或数据清洗。
- 永久回归新增test_timeline_visibility.py 10项，修前8项失败、2项全量/admin对照通过。失败覆盖三种父对象无模块权、三种父对象self范围的同事任务标题/完成备注泄露，以及103条范围外任务或跟进挤掉旧的有权事件。修后10项全部通过：本人任务/合法协作人工跟进保留，其他任务标题和完成备注排除，all与admin允许合法内容；跟进按客户优先于商机的现有范围判据，limit=1也不会拿到范围外跟进，不扩大到协作不可见的数据。
- 实际HTTP：合成普通self角色、真实JWT/登录会话/权限读取，重复使用同一JWT。客户、线索、商机三条时间线分别验证无task:view不含任务、有task:view只含本人、同事任务详情仍403且动态不泄露；all正对照详情/动态均200；撤followup:view后详情403、概览受限、三条时间线均不含人工跟进内容。跟进有查看权时合法协作内容仍在。测试仅替换get_db为全新内存SQLite，未跑app lifespan/真实外部调用；初稿临时夹具批量文本替换误给SampleRequest传lead_id，修正夹具字段后完整HTTP通过，保留该构造错误日志，不将其误记应用问题。
- 验证：完整后端1332项通过（/tmp/crm-timeline-permission-full.log，实际进程exit0），修改文件pyflakes、git diff、查询JOIN/输出判据检查通过；未放宽原测试或重复跑无关全量。实际HTTP只用普通self/all角色，admin特例由永久服务回归验证；未声称真实浏览器或PG并发的新验证，本项仅改变读取过滤。
- 再次直接SSH核对部署仍8bce10d；服务器timeline/service仍e3bf0192…、task/router仍1e47feff…、customer/service仍16753ae4…，本地修复未部署。修前/修后/全量/HTTP脚本与日志、双方SHA256归档ops/backups/timeline-permission-fix-20261027/。内存库/临时文件均清理，没有PG、生产业务数据/服务/配置/真实消息操作，无提交推送部署；Q-033/Q-034/Q-035/Q-036及用户原12项文档删除保留。
- Q-037当前无待批准实现。本轮在等待答复时只读取应收删除守卫，收到批准即回到本项，未声称完成新的删除并发验证。下一独立方向仍可做应收删除与人工分配竞争的锁内可删除性复核；全项目长期审查未完成。

## 2026-10-27 Q-037 客户时间线绕过任务/跟进读取权限（中，已复现，待权限口径决定）

- 上一轮真PG 8组分配交错新增证据属于实质进展；本轮转向已标为静态线索的时间线聚合权限，不重复已有附件/产品疑点。SSH两次核对部署仍8bce10d，timeline/service.py e3bf0192…、router.py f55daa0e…、task/router.py 1e47feff…、model.py aa5295ef…、customer/service.py 16753ae4…与隔离运行文件逐项同SHA256。未将尚未部署的Q-033至Q-036当成线上实现。
- 代码证据：build_timeline按customer/lead/opportunity关联直接查Task并limit，未检查task:view或scoped_owner_ids，随后返回任务标题与完成备注；其跟进段虽检查系统单据来源，却不检查followup:view。相对地，任务详情/列表要求task:view并按Task.owner_id范围；客户概览明确按task:view/followup:view限制模块，任务也按owner范围。时间线路由只守父客户/线索/商机查看与范围，因此父对象可见并不保证聚合子信息可见。
- 实际HTTP复现：全新内存SQLite/随机本机JWT密钥/合成登录会话和普通self角色，通过实际get_current_user的JWT/会话/角色/权限/范围读取，新建FastAPI加载实际客户、时间线、任务及跟进路由，只替换get_db，无app lifespan或真实外部调用。合成本人客户、本人任务70、同事任务80（含完成备注），同一JWT下：①无task:view时两个任务详情403，概览tasks受限为空，时间线200且含同事任务标题与完成备注；②补task:view后本人详情200、同事403，概览只含本人，时间线仍含同事；③all范围正对照同事详情/时间线均允许；④撤followup:view后跟进详情403、概览followups受限为空，时间线仍含人工跟进原文。
- 已确认影响限于所测父客户可见条件下的任务标题/完成备注与人工跟进内容，不称可读任意任务全部字段、可修改范围外任务或跨租户。三个父对象类型走同一聚合分支，代码上均受影响，但本轮实际HTTP仅验证客户路径。测试加载当前已批准Q-036的来源过滤；Q-037所测普通人工跟进/任务不依赖该软删变化，直接涉事timeline/task/customer文件与实际服务器一致。
- 方案与取舍：推荐时间线按原模块权限及既有数据范围过滤任务、跟进，并在limit前应用，保留管理员特例和有权看到的动态，不改任务/跟进详情权限、不删除业务或历史记录。代价是原来依靠客户查看权读到的额外任务/跟进动态会减少。替代为明确允许父客户可见者协作查看全部关联任务/跟进摘要，保留现状但接受时间线与模块详情/概览不同；若改为全模块协作授权则范围更大，不推荐。不能将Q-036删除后隐藏授权扩展成所有聚合权限调整，按AGENTS及完整目标先记录、请求人工决定。
- 证据脚本/成功复现日志/源码哈希归档ops/backups/timeline-task-scope-review-20261027/。DATABASE_URL显式固定localhost:1不可连接隔离地址，内存库销毁、临时目录清理；没有PG、生产数据库/真实外部请求，未修改timeline、task或权限实现，无提交/推送/部署。现有获批修复与用户文档删除保留。
- 下一步：若批准，永久回归先证明普通无模块权/self越界隐藏，再验证本人/all/admin合法对照、limit前过滤及customer/lead/opportunity入口；复用已有范围助手，避免以作者范围取代人工跟进的客户协作规则。等待期间可独立核对其它资金删除/确认边界，不把本项需要决定等同全项目阻塞。长期目标未完成。

## 2026-10-27 已确认回款人工分配与确认/应收改额并发（真PG 8组通过，无应用修改）

- 上一轮按用户决定完成Q-036过滤与回归，属于实质进展；本轮回到资金链，先查既有并发套件已覆盖同键登记、双确认50+50、确认/驳回唯一终态，不重复把这些场景当新审查结果。选择此前没有逐交错证明的人工分配整份替换、空人工分配与另笔确认/应收重算相撞。
- 直接SSH读取并再次核对当前部署仍8bce10d；payment/router.py 3e998aa7…、service.py a72f012c…、model.py bc4ba02b…及order/milestones.py b31778a1…与隔离执行文件逐项同SHA256。已有实现所有路径先取得共同订单锁，之后再锁节点/回款，人工分配仅改指定回款，确认只自动分配正在确认的回款，应收改额重算全部自动分配且保护完整人工决定。
- 隔离实际执行：本机PG15的crm_iso_pool_owner_review_20261027（127.0.0.1:55439），开头确认用户/客户/订单/应收/回款空，合成财务账号/客户、每组新订单、两期50/100元、两笔50/20元CNY回款。实际update_payment_allocations、confirm_payment、update_receivable路由函数与真实AsyncSession/SQL/提交/审计/站内通知执行，只有真实订单SELECT FOR UPDATE取得后暂停控制顺序；第二事务已在等待时由独立pg_blocking_pids证明真实数据库阻塞，不用sleep推定锁有效。CurrentUser为隔离夹具构造的finance/all上下文，未声称验证本次真实HTTP鉴权或浏览器。
- 八组均通过：①移期→另一笔确认；②确认→移期；③移期→应收从50改80；④改额→移期；⑤清空人工分配→另一笔确认；⑥确认→清空；⑦同笔移期→清空；⑧同笔清空→移期。每组验证分配行无重复且符合该顺序的具体期数/金额、人工mode及source保留、各期paid/partial/pending与实际分配相符、serialize_plan已收额、订单received70/allocated/unallocated总量守恒。单次调整审计一条；同笔两次调整审计两条，第二条before完整衔接第一条after，且before mode已为manual，未因等待前旧对象丢失人工决定。
- 差异不是缺陷：①第二笔20填回空出的第一期，②第二笔20在先前确认时已落第二期，随后人工把第一笔50也明确指定第二期；两者最终分布不同，但都保留明确登记/人工归属，没有凭空多收或自动搬动其他已确认回款。已有规则不要求人工调整时自动重铺别笔回款，不能把操作顺序影响结果误报为并发覆盖；本轮不改该业务口径。清空后的50持续待分配，没有被另一笔确认擅自重新分配。
- 结果与边界：所列交错均正常提交，无死锁、余额覆盖或审计丢失；不据此推断全部财务并发已穷尽。函数确实调用跟单同步，但本轮没有额外逐节点断言，不宣称完成新的跟单节点/前端缓存验证；也未覆盖删除应收、取消订单、凭证上传与分配交错。没有新确认Bug，不为制造改动增加应用补丁或镜像测试，不重复跑无关全量单元。
- 脚本及6组初测/8组最终成功日志、服务器哈希归档ops/backups/payment-allocation-race-review-20261027/。只清理本轮明确用户/客户/订单/回款ID，独立psql确认users/customers/orders/plans/payments/allocations/milestones/audit/notices/events/followups均0，临时PG已停止。真实推送/调度/OA Stream关闭，无生产数据库或真实外部业务调用，无提交推送部署；Q-033/Q-034/Q-035/Q-036及原文档删除状态保留。
- 下一独立方向：可验证应收删除与确认/人工分配竞争时的可删除性复核，或查看客户时间线聚合的任务权限与来源范围是否同源；后者目前仅静态线索，不记为已确认问题。全项目长期目标仍未完成，当前没有因为待人工决定而阻塞本轮。

## 2026-10-27 Q-036 用户决定“一起隐藏”后完成实现（低，本地验证，未部署）

- 用户回复“啥意思”后已解释打样单删除但客户动态/动态附件仍可看的例子，并再次说明文件保留、恢复后显示；用户随后明确选择“一起隐藏（推荐）”。本项现在按该展示/访问口径实施，覆盖下方待决定状态，不追溯称为跨负责人泄露或未经批准的安全修复。实际授权只涉及打样的自动记录，人工跟进继续保留，不扩展其它删除类型。
- 最小实现：followup/visibility.py的system_source_filter在原有quote软删判断旁，给sample来源也追加model.deleted_at IS NULL。过滤发生在计数/排序/limit之前，已有客户概览、客户跟进、全局/商机跟进、时间线和文件委托读链共用此条件，无需逐端点或前端另造规则。不存在记录/附件删除、数据库结构/锁策略/依赖改变；管理员与普通账号均在正常读入口隐藏，恢复样品后原记录自然重新符合条件。
- 永久回归扩展test_opportunity_followup_visibility.py两个参数分支（普通self、管理员all），在修前均失败于软删后仍返回自动记录3；其它5项旧回归通过。修后覆盖正常→软删→恢复三个状态、详情403/200、商机列表、客户动态所用来源过滤、全局列表总数及第二页不被隐藏项挤占、人工跟进保留、文件读取同步拒绝/恢复；自动记录同时关联sample/quote，证明删除sample不会退回报价而绕过。另保留共享文件的“任一可见挂载即可”：只隐藏自动记录对应挂载，仍有客户挂载时合法可读，移除该共享挂载后再次拒绝。
- 实际HTTP复验：沿用上一轮全新内存SQLite/随机JWT密钥/真实登录会话与权限/self范围及临时文件，新建FastAPI加载实际路由、只替换get_db，无app lifespan或外部调用。实际DELETE /samples/30后原单404、自动跟进详情403、附件元信息/下载/预览403，文件字节仍在；实际POST /samples/30/restore后自动跟进与直接样品附件三入口恢复200。全局跟进、客户跟进、客户概览、客户时间线共4读入口在删除/恢复后均正确隐藏/显示自动内容，人工内容始终保留；完整文件访问矩阵34组通过，包含原来的改派/撤权/合同/共享行为，没有放宽断言。
- 验证：完整后端1322项通过（/tmp/crm-sample-fact-hide-full.log，实际进程exit0）；增加共享挂载断言后针对性跟进/文件23项通过，应用未再改变，不重复跑无关完整套件。pyflakes、git diff、查询JOIN及输出判据检查通过。修改应用只有followup/visibility.py，配套测试在已有文件中自动进入CI发现；未新增重复管理体系或模块入口。
- 基线与证据：再次直接SSH只读核实DEPLOYED_COMMIT仍8bce10d，服务器followup/visibility仍c6aae909…、sample/router仍648188e8…、file/access仍e8222960…；本地未部署差异明确保留。修前/修后/全量/最终针对日志、HTTP脚本/日志及双方哈希归档ops/backups/sample-fact-hide-fix-20261027/。测试内存库已销毁、临时文件已清理，不启动PG，不碰生产业务/真实外部发送，无提交、推送或部署，Q-033/Q-034/Q-035及原12项文档删除保留。
- 证据边界：验证服务/实际ASGI HTTP和物理文件字节，没有新浏览器交互或PG并发验证，本项只加已有读取查询谓词、不调整事务。不阻断已经开始的文件响应，不限制文件从其它可见业务挂载合法读取，不改变审计/回收站恢复能力。Q-036当前无待批准实现；原拟独立回款审核/分配交错尚未在本轮开展，下一轮先对照既有并发证据选择未覆盖场景，长期目标仍未完成。

## 2026-10-27 合同/跟进附件的真实鉴权HTTP访问矩阵；Q-036已删除打样系统动态口径待决定（低）

- 上一轮Q-035获批修复与验证属于实质进展。本轮承接此前仅静态阅读的委托文件访问，未重复产品/SKU父子删除和路径穿越已排除项。SSH直接核实当前部署仍8bce10d；file/access、router、storage，contract/visibility、router，followup/visibility、router，sample/router、service，core/deps及auth/session共11文件SHA256与本机运行文件逐项相同。哈希归档ops/backups/file-delegation-http-review-20261027/server-source-hashes.txt。
- 环境：本机全新内存SQLite+临时合成文件；显式DATABASE_URL固定localhost:1不可连接地址，FILE_ROOT为唯一临时目录、JWT_SECRET为进程随机值，真实推送/调度关闭。仅新建FastAPI应用加载实际文件/合同/跟进/样品路由与统一错误处理，无app lifespan。唯一依赖替换是get_db指向内存库，JWT解码/登录会话有效性/角色权限/self数据范围/文件可见性/物理文件读取全用实际实现；每个HTTP请求新Session，同一枚JWT反复使用。登录会话由真实open_session创建，未声称跑了登录密码HTTP流程。合成两名普通self角色用户，不用管理员特例绕过读取检查。
- 实际HTTP矩阵32组（每组元信息/下载/预览各一次，共96次）通过：合同、人工跟进、系统打样跟进、打样、客户5类挂载在归属变更前后，旧/新负责人均按当前范围得到200或403，成功下载/预览的实际字节与临时文件相同；共享文件只要有一条可见挂载即放行，移除该挂载立即拒绝；公海客户的合同按创建人回退，有订单时优先跟订单负责人，未向所有人开放；软删合同和软删样品的直接挂载拒绝；撤销order:view/followup:view/file:view后旧JWT下一请求即403，未登录下载401。归属变化由隔离夹具事务修改客户/样品owner，不声称验证了真实交接接口及其所有单据迁移。
- Q-036观察：实际DELETE /samples/30成功200，随后GET /samples/30为404，直接打样附件三入口403；同一打样的系统过程记录GET /followups/21及全局列表仍200且包含该记录，其followup挂载文件三入口仍200。真实删除接口保留数据库/审计/附件，只设置deleted_at；system_source_filter只对quote排除软删，没有对SampleRequest排除，文件委托get_visible_followup沿用同一结果，故不是另一个文件授权旁路。现有quote软删系统记录已有隐藏回归；样品规则尚需人工决定，不能直接称为跨负责人泄露或已确认安全漏洞。
- 影响/选项：同一位仍有样品查看权且范围内的用户，在正常客户动态与跟进列表继续看到被删除样品的系统留痕，并能拿到挂在该留痕的附件；样品原单/直接附件则不可读。推荐与当前报价规则一致，样品被软删后正常列表、系统动态详情及其挂载附件一并不再展示，恢复原单后恢复；不物理删除记录/附件、不隐藏人工跟进、不扩大原本看不到的范围。代价是删除后正常时间线少一条历史自动记录；替代为明确保留这类历史自动记录及附件，可保留查阅但接受两个入口不同。涉及展示和访问口径，按AGENTS及完整目标先请求决定，当前没有改任何权限/过滤/文件/界面代码。
- 边界：本轮证明普通self账号的所列路由行为，未跑浏览器/真实生产请求、部门树、PG并发文件锁、多进程或下载中途改派；不推断撤权能终止已开始的流式文件响应。脚本/成功HTTP日志/同源哈希归档file-delegation-http-review-20261027，内存库已销毁、临时文件目录已清理；未启动PG、访问真实数据库或外部服务，无提交/推送/部署，Q-033/Q-034/Q-035本地修复及用户12项原文档删除保留。
- 下一步：等待Q-036口径决定；若同意统一隐藏，需要修前失败的永久回归，覆盖正常/软删/恢复、人工跟进保留、列表计数分页、动态与附件读取，不顺带收紧其它业务类型。等待期间仍可独立审查其它业务链，长期质量目标未完成。

## 2026-10-27 Q-035 用户批准后完成定向保护复查（中，本地修复验证，未部署）

- 用户首次回复“没懂”不算批准；解释“刚建打样却收到准备回收的多余提醒”及增加查询耗时后，用户明确选择“修，发提醒前再确认”。授权为本地修复测试、不上线，覆盖下方待决定状态；不改草稿算保护、保存草稿不算真实活跃、90天保护上限等现有规则。
- 实现：protection_detail新增可选customer_id，报价、在途订单、未结应收、打样及修订父链均在SQL内限定当前客户；父链按被替代父单的客户过滤，保持旧历史跨客户子链也能替代父单的既有语义。不传参数仍为原全量查询。run_public_pool_recycle取消批次起始全量保护快照，只对已锁内刷新且确实超过活跃阈值的客户取最新保护事项，结果直接用于跳过/超期标记、候选快照与返回理由，避免混用旧字典。不新增锁、表结构、依赖、保护条件或外部调用。
- 永久回归扩展check_pool_scan_race.py：真实手工订单、普通打样、钉钉草稿三条路由在扫描初读后提交；前两条更新活跃并跳过，钉钉草稿不更新活跃但现在protected1、nominated0、notified0。新增扫描先持客户锁的反向交错，草稿创建真实等待外键/客户锁，经pg_blocking_pids确认，扫描先提交合法预告后草稿再成功保存，两者完成且无死锁。本轮连同原7组共11组真实PG交错通过。
- 修前失败证据：仅在一次测试进程内撤回Q-035定向刷新、恢复批次起始保护快照，保留Q-033客户锁和实际业务入口，永久新增分支明确失败于钉钉草稿nominated1/notified1；修后同断言通过。原服务器旧快照反例也保留在上一条归档。初稿测试复制误把unknown拼成unkNOWn、临时驱动漏scripts模块路径，均在形成有效业务失败证据前修正，未通过删除/放宽断言获得通过。可重现驱动与有效修前/修后日志归档pool-protection-fix-20261027。
- 单元新增test_pool_protection_filter.py 4项：定向/全量对同一合成数据的保护原因一致，覆盖四类保护、过期报价、软删/已接受样品与跨客户修订父链；60天+上限90跳过，100天+上限90仍可提名并保留保护原因，100天+上限0继续豁免。完整后端1320项通过（/tmp/crm-protection-full.log，实际进程exit0）。pyflakes修改文件、diff、148套件登记及查询JOIN/输出判据检查通过。没有应用变化后再重复跑无关全量测试。
- 性能取样仅在本机隔离PG：目标客户各1份报价/订单/应收/打样，另一个客户各5000份，实际protection_detail定向固定5查询，返回4条保护且没有其他客户结果。EXPLAIN ANALYZE显示报价/订单/应收/样品/修订父链均使用既有索引；7次定向中位1.708ms，范围1.398–2.403ms，原全量函数中位55.268ms。此对照证明定向不反复搬回全站数据，不代表新扫描整批比旧扫描快；新路径对每个确实待提名客户增加5次查询，总耗时仍随客户数和网络延迟增加，客户锁仍持有到本批提交。未验证生产大批量延迟或全系统锁图。
- 测试隔离补齐：该扫描套件全局取规则/客户，现改为与现有提醒套件同样的父进程创建随机专用本机PG库→迁移→子进程测试→删除，避免依赖CI共用库为空；迁移/子进程超时会终止，finally删除专用库。实际完整执行退出0，pg_database核实crm_iso_pool_scan_*无残留。缺DATABASE_URL、正式库名、远端地址三种误配置在导入app/建库前拒绝，即便ALLOW_DEV_TARGETS=1也拒绝。
- 再次直接SSH只读核对部署仍8bce10d，服务器settings/service仍d21f4bfb…、sample/router仍648188e8…，完整哈希及修后本地哈希归档ops/backups/pool-protection-fix-20261027/。成本夹具按两个明确客户ID清理，独立psql确认users/customers/quotes/orders/samples/plans/notifications/rules均0，临时PG停止；未碰生产数据库/真实发送/配置/服务，无提交推送或部署。Q-033/Q-034有效修改和用户原文档删除保留。
- 本轮曾开始文件委托可见性/MIME调用链复核，随后收到批准转回Q-035；仅静态阅读不能称新文件安全验证，没有重复记录既有产品/SKU访问已排除项。下一独立方向可做合同/跟进附件随改派和软删变化的实际访问矩阵；Q-035当前没有待批准实现项，长期全面审查仍未完成。

## 2026-10-27 Q-035 扫描保护快照漏掉期间新增的钉钉打样草稿（中，已复现，待决定）

- 上一轮完成Q-033/Q-034代码和回归，属于实质进展。本轮检查Q-033明确保留的履约保护快照边界；未重复测试客户改派等已覆盖交错。再次直接SSH核对DEPLOYED_COMMIT仍8bce10d，settings/service.py原始d21f4bfb…、order/router.py 4d3e90a3…、order/service.py 9fef9ced…、sample/router.py 648188e8…、quote/router.py eef6b222…、customer/service.py 16753ae4…；除已记录的本地settings修复外，对应文件与服务器逐项同哈希。
- 代码链：run_public_pool_recycle在读客户前一次性调用protection_detail；Q-033随后刷新客户归属/活跃，不刷新该保护字典。真实POST /samples的use_dingtalk=True创建pending草稿时跳过record_and_notify（所以不更新last_progress_at），但现有protection_detail把该存活pending草稿计为在途打样保护。与此不同，真实手工订单及普通打样通过record_and_notify→record_progress_event→touch_progress更新进展，不能只搜路由是否直接调用touch_progress就报缺陷。
- 真PG：本机一次性crm_iso_pool_owner_review_20261027，开头独立确认客户/用户/订单/打样/规则空，合成1用户、产品/SKU、30天B级规则及每场景60天未联系客户（小于90天保护上限，避免混入超期保护口径）。扫描真实Customer初读完成处暂停，另一个AsyncSession实际调用订单/打样路由并提交，未替换业务函数/SQL/时钟/通知。普通订单及普通打样：实际保护列表非空、进展更新、扫描0候选0通知；钉钉草稿：实际保护列表非空、进展未更新，扫描仍新增1 pending候选及1站内预告。
- 下游安全闸已验证：将隔离候选到期时间设为已到期，实际decide_candidate(approve)因新打样保护422拒绝，客户仍归原负责人；没有错误回收。确认影响为不该有的预告及多余人工处理，不称生产事故，不推断未测试报价/回款等所有交错。
- 方案取舍：推荐发预告前在既有客户锁内定向复核最新保护事项，继续按现有“草稿也受保护”及90天上限判断，不改业务规则；代价是每个实际待提名客户多次查询，扫描时间与客户锁持有时间可能增加，需验证过滤查询成本和并发锁序。替代为保留批次起始保护快照、允许该窗口产生预告并由主管复核拦截，可免额外查询。不能为了修此问题擅自把未提交草稿排除保护，或把保存草稿改作真实活跃，这两种都会改变业务口径。
- 按AGENTS.md及完整目标对性能/事务取舍的人工决定要求，当前只记录并请求决定，未应用保护查询/时钟/草稿规则修改；Q-033既有获批修复及Q-034保持。日志与脚本归档ops/backups/pool-protection-entry-review-20261027/。测试真实推送和调度全关、未跑app lifespan、未访问生产数据；按本轮明确ID清理，随后独立核对数据库空。没有提交/推送/部署。
- 下一步：若批准，先建立此分支修前失败的永久回归，再检查定向保护查询不跨客户污染、90天上限保持及扫描/新草稿/主管审批交错；等待期间可独立检查订单履约变更与保护更新，不将静态疑点记为缺陷。长期目标未完成。

## 2026-10-27 Q-033 已获批准：发预告前锁内复核（中，本地修复并验证，未部署）

- 用户明确选择“修，避免发旧提醒”，范围为先本地修复测试、不上线；覆盖下方历史“待决定”状态。继续以服务器为准，SSH再次核实settings/service.py原始哈希d21f4bfbcd64d98244984353cf4a72246f9a532a2f7146e583a1b8f6fa55e90a，部署基线仍8bce10d；没有覆盖用户原有12项文档删除。
- 最小实现：run_public_pool_recycle初始客户列表仅作候选；客户若不在初始未结集合，按客户ID、原负责人、存活/私海及当前等级条件FOR UPDATE SKIP LOCKED并populate_existing刷新，再用刷新后的联系/进展时间判断资格。已改派/删除/不再符合条件则跳过，正被修改的客户下轮再扫；锁持有到扫描提交，避免检查后改派先成功、再生成旧提醒。
- 锁序保护：持客户锁后用普通MVCC查询重查未结候选，处理另一扫描已提交但初始open_ids尚未包含的候选；主管可能先持候选锁再等客户锁，此时普通读仍可看见原未结状态并跳过，不等待候选唯一索引。保留原唯一约束与保存点兜底，没有改审批规则、数据库结构、历史候选或已发送通知。
- 永久真实PG套件backend/scripts/check_pool_scan_race.py已登记ops/check_suites.txt。修前在客户查询后实际改派，断言无旧预告明确失败：nominated1且实际通知旧负责人；修后七组均通过：①改派已提交、②软删已提交、③新联系时钟已提交，三者均0候选/0通知；④客户锁忙碌时跳过、释放后补扫成功；⑤另一扫描在初始列表读取后提交，重查只保留一个候选且不重复通知；⑥同样交错再加主管持候选锁，扫描不阻塞插入，主管随后实际批准executed；⑦扫描持客户锁时另一扫描跳过，人工改派真实等待，经独立pg_blocking_pids证实，扫描提交后改派完成。实际SQL/保存点/候选/站内通知/改派/批准均未替换，只暂停真实查询返回点控制顺序。
- 验证环境仅本机127.0.0.1:55439的crm_iso_pool_owner_review_20261027，真实推送/调度/OA Stream关闭；没有应用启动或生产数据。新套件拒绝缺DATABASE_URL、正式库名、远端地址三种误配置。完整后端1316项通过（/tmp/crm-pool-full.log，实际进程exit0），修改文件pyflakes与git diff检查通过。每场景只清理本轮合成ID，独立psql确认users/customers/candidates/notifications/rules/audit均0。修前/修后日志及脚本归档ops/backups/pool-scan-fix-20261027/。
- 已验证的代价与边界：同客户改派会等待本批扫描提交，忙碌客户本轮不提名；本轮证明所列交错不死锁，不宣称全系统锁图或大批量延迟已穷尽。新活跃场景直接修改隔离夹具时钟，未跑真实跟进HTTP；保护明细仍是原整轮快照，履约保护并发变化未纳入此修复。已有失效候选不自动清洗。未提交、推送或部署；下一独立审查可验证保护快照与关联单据并发创建，不将静态疑点直接记为缺陷。

## 2026-10-27 Q-034 迟到旧审批轮次覆盖重提业务（中，已复现，本地修复未部署）

- 直接核对服务器dingtalk/service.py原始SHA256为0274b6ecc4dd42d45f479570ec53652b5d6d2924411776b488677a2440ab748d。Stream按processInstanceId找到历史OaInstance，再定向拉取真实审批快照，虽然不会相信事件自报结果，但sync_pending_instances允许定向刷新旧轮次，且此前未检查submit_round是否最新。打样同版本被拒后重提pending，旧拒绝事件再到会把主单重新改为rejected；合同旧事件则把filled_data._dingtalk_oa改回旧轮次并可能通知旧结果。不是外部事件伪造或生产真实数据案例。
- 复现：本机内存SQLite实际服务、合成客户/主单及两轮OA，外部客户端由固定快照替代；旧OA已rejected、最新OA pending时，定向旧拒绝返回checked1/changed0，打样却变为rejected。永久test_oa_round_isolation.py六个旧轮次分支（sample/contract × refuse/agree/TERMINATED）在修前全部失败，四个当前轮次对照通过；没有用放宽断言或删除原测试获得通过。
- 最小修复：既有主单锁取得后，用现有get_by_business_key读取同业务/版本/类型最新轮次；历史OA继续保存其返回快照，只有最新轮次可以映射打样/合同主单及通知。没有改变审批终态映射、提交/撤回流程、权限或锁策略，正确行为明确，按已授权纯Bug修复实施。既有两个打样mock用例仅补充真实新增查询的替身并断言查询参数，原状态断言保留。
- 修后新增10项全部通过，完整后端1316项通过（/tmp/crm-oa-round-full.log，实际进程exit0），覆盖历史记录刷新、新轮次pending不变、无旧通知及当前轮次批准/拒绝重复同步仅通知一次。证据归档ops/backups/oa-old-round-fix-20261027/。此项是服务/SQLite回归，未声称真实钉钉事件投递、PG竞争或浏览器端到端已验证；没有真实外部请求、数据清洗、提交推送或部署。

## 2026-10-27 Q-033 公海扫描在客户改派/删除后仍生成旧预告（中，已复现，待锁策略决定）

- 读取完整目标/质量目标/最新进度，上一轮新增真PG交错证据属于进展。本轮验证此前明确未覆盖的“扫描读取候选客户后发生变化”，不重复把批准执行时的锁内复核报告为缺陷。直接SSH读取服务器run_public_pool_recycle的客户查询、候选插入/通知段；当前8bce10d，settings/service哈希d21f4bfbcd64d98244984353cf4a72246f9a532a2f7146e583a1b8f6fa55e90a、customer/service哈希16753ae40ddd22ad75b5e8c8284415bda2c8c73f9e678f8725dafc4318742373，与隔离运行文件一致。
- 代码证据：settings/service.py约1016行按存活/私海/有负责人读Customer，随后逐条直接用旧对象写PublicPoolRecycleCandidate及_notify_recycle_candidate；写入前没有锁内刷新归属或deleted_at。唯一索引只防同客户重复未结候选，不保证快照当前有效；owner_id快照同时决定原负责人及其管理主管的提醒对象。下一扫描通过open_ids跳过旧pending候选。
- 真PG复现：复用上一轮已独立确认空客户/用户的本机一次性crm_iso_pool_owner_review_20261027，合成3用户及唯一B级规则。脚本/tmp/crm-pool-scan-snapshot-review.py使用实际扫描/transfer_customer/delete_customer/decide_candidate，只在真实Customer查询返回后暂停，另一真实AsyncSession改派或软删并提交，然后继续扫描，不替换SQL、候选写入或通知保存。两场景均nominated1，实际pending候选owner仍旧A，并实际新增一条给A的站内预告；无真实外部投递。
- 后续影响也已验证：改派场景旧提名批准409且B归属保留，下一扫描nominated0/already_open>=1，新负责人不能得到当前轮的新提名；删除场景批准404且客户保持删除。两场景均可实际reject旧候选，故不能称“永远无法处理”或“会错误回收客户”。确认影响是失效预告、额外人工复核及旧未结候选阻挡重新提名；不清洗已有生产候选或撤回已送通知。
- 待决定方案：推荐生成候选/预告前对客户锁内重读，客户已改派/删除或不再符合规则则跳过，忙碌客户留后续轮次，再保存新候选及通知。需评估扫描长事务对改派等待的影响、与批准候选→客户锁序及唯一索引竞争的交错，不能简单在现有循环补锁就声称无死锁。替代为保持快照现状、由主管驳回，免去锁等待但保留已复现无效预告/阻挡；只普通重读无锁仍有读取后变化窗口，不当成完整修复。
- 依据AGENTS.md“涉及…生产环境…先记录并请求人工决定”及完整目标“调整…事务策略…必须先…等待明确决定”，已异步请求选择推荐方案或保留现状，范围先本地验证且不部署。尚未收到决定，未应用任何锁、数据库、权限或界面调整；此前批准Q-029/Q-032不扩展为本项永久锁策略授权。
- 脚本/成功复现日志/与服务器一致的源码哈希归档ops/backups/pool-scan-snapshot-review-20261027/。精确本轮ID清理后独立psql确认users/customers/candidates/notifications/rules/audit均0，临时PG已停止；无生产业务读取/写入、服务变更或外部发送，没有新增应用/永久测试修改或提交推送，原12项文档删除保留。
- 下一步：用户批准后建立修前失败的永久回归，再验证改派/删除/新活跃、忙碌跳过补扫、两扫描、扫描与批准的锁序及等待；未批准期间可继续独立审批回调/撤回链路审查。本项存在待决策风险不代表全项目工作阻塞，长期目标保持未完成。

## 2026-10-27 公海回收批准与客户改派/新活跃的事务交错（真PG通过，无修改）

- 恢复完整目标、规则、当前进度与Git状态，上一轮实际浏览器验证产生新证据，归类进展。本轮查历史已有公海领取/回收顺序回归，选择未逐交错证明的批准等待客户锁后重读、与人工改派的双向锁序，不重复将既有正常批准测试充当新发现。
- 直接SSH读取实际服务器public_pool/router.py、settings/service.py关键实现，DEPLOYED_COMMIT仍8bce10d。服务器settings/service、customer/service、settings/model哈希分别d21f4bfbcd64d98244984353cf4a72246f9a532a2f7146e583a1b8f6fa55e90a、16753ae40ddd22ad75b5e8c8284415bda2c8c73f9e678f8725dafc4318742373、84cfe7e4c9be53674adf4b51721992e0da424b53cf603c6f01746fdc367cda34，与隔离运行文件一致。代码链：decide_candidate先锁候选并populate_existing，再_customer_now锁客户并刷新归属/活跃；transfer_customer自身也锁客户并刷新，人工改派不反向锁候选。
- 全新本机PG15库crm_iso_pool_owner_review_20261027（127.0.0.1:55439）从空库迁移，合成3用户/4客户/4已过预告期候选。脚本/tmp/crm-pool-owner-race-review.py使用真实AsyncSession及实际decide_candidate/transfer_customer；只在真实SELECT FOR UPDATE完成后暂停控制时序，不替换SQL、保护查询、单据迁移或提交。每组等待均以独立连接pg_blocking_pids确认真实阻塞，不用sleep推定锁已生效。
- 四组全部通过：①改派持客户锁、批准带旧ORM等待，改派提交后批准409，保留新负责人及唯一A→B历史、候选pending；②批准先持候选/客户锁，旧对象改派真实等待，批准提交后改派成功，最终B私海、候选executed，历史依次A→公海→B且无死锁；③旧ORM已加载后，另一事务持客户锁提交新last_followup_at，批准等待后422，原负责人/pending不变且无归属历史；④两批准竞争候选锁，第一执行、第二按新终态拒绝，只有一次回收/一条历史。
- 新活跃场景仅直接更新隔离夹具的客户时钟来控制提交时序，未声称完成真实跟进HTTP创建全链；改派调用默认move_documents=True，夹具无待搬单据，不推断万级关联单据或全系统锁图均无风险。当前验证排除的是所列陈旧对象覆盖/双执行疑点，不涵盖扫描初始快照过期、真实外部通知、生产角色端到端、撞单裁定/删除同时介入等其它交错。
- 脚本/成功日志/迁移日志/服务器核对一致的源码哈希归档ops/backups/pool-owner-race-review-20261027/。独立psql确认users/customers/public_pool_recycle_candidates/customer_owner_history/audit_logs均0，临时PG已停止；关闭真实推送/OA Stream/调度，未跑app lifespan，未访问生产数据库或触发生产写入。
- 本轮无已确认新Bug，无应用/永久测试修改、提交推送或部署，原12项文档删除保留。长期审查未完成；下一独立方向可检查候选生成扫描与客户改派/软删同时发生时的快照、提醒对象及后续可处理性，先验证现有批准复核是否足以消除实际影响，再判断是否存在需人工选择的快照/锁策略取舍。

## 2026-10-27 回款分配保存与页面刷新（同线上构建的隔离浏览器通过）

- 上一轮新增80组算法对照属于实质进展；本轮承接未覆盖的真实前端交互，不重跑相同算法充当新证据。已恢复目标、规则、最新进度与Git状态，服务器DEPLOYED_COMMIT仍8bce10d，直接读取分配弹窗保存处理及订单详情查询链。
- 重要基线修正：服务器保留的frontend/src/modules/order/OrderDetailPage.tsx哈希1c6d1314500929748b2397aef15f64a912f4f9cf904945c0c5df448baea2de98，与当前固定提交源码f8a803f6095d960bded175deb5a0728c2b1bb04ebe909c4562557b8f2bd590ea不同。直接下载只读副本对照，差异涉及旧SKU展示/时间格式/复购确认。检查统一发布工具，确认它实际替换backend/app和web-dist，不替换残留frontend/src；不能据此声称线上页面回退或用旧src覆盖本地。此前只证明已核对的两个分配相关前端文件一致，不扩展为所有服务器src均一致。
- 直接在服务器按RELEASE.json计算119个web文件实际SHA256，mismatches=[]；本机frontend/dist按同一发布包manifest逐项119文件亦0差异。线上实际OrderDetailPage-BzEi8V0o.js哈希2745ff1f00a9d81dabb09ccfdb069f307a93bf378bf5a64c69881f670a880029。浏览器测试使用这套逐字节一致的构建，满足以服务器运行代码为准；未触碰生产业务接口。
- 临时浏览器脚本复用既有confirmation_browser_smoke的独立profile/CDP和本机模拟API方式，只造合成订单/两期/100元已确认回款。取消弹窗零PUT；填写二期70.01并输入原因后模拟422，错误文案显示且金额/原因/弹窗保留，模拟记录未变；重试成功仅发一条对应PUT，明确携带plan_id=402和字符串70.01及原因，列表已分配70.01、待分配29.99，汇总/应收请求重新执行，切应收期显示相同余额。返回全局应收列表后重新取数并显示二期70.01/29.99。四组断言全部通过，进程退出0、独立浏览器/profile/本机server按finally关闭。
- 排除缓存误报：AllocationAdjustModal只显式失效订单/分析查询，没有全局receivables/payments失效；但项目Outlet导航卸载页面、默认staleTime=0，实际返回全局列表会重新取数。已用先打开全局列表填缓存→详情调整→站内返回列表且新请求/新余额的场景验证，不能仅靠静态缺失一条invalidate就确认用户可见Bug。未擅自增加请求或改变缓存策略。
- 初稿测试替身将保存方法误设PATCH，实际API为PUT，因此模拟错误分支未触发；修正替身为真实PUT后完整重跑通过，未修改业务代码或放宽失败保持断言。脚本生成器、最终脚本/日志、初稿失败日志、旧src副本和哈希记录归档ops/backups/allocation-browser-review-20261027/。
- 边界：本轮模拟API验证的是实际前端构建的反馈/刷新，不证明后端事务、真实HTTP业务写入、网络断线时服务端是否已提交或所有并发编辑冲突；不把模拟成功描述为生产写入回归。没有新增已确认Bug、应用/永久测试修改、提交推送或部署，用户12项文档删除未动。下一独立方向可查公海回收候选生成/人工处理与客户归属变化的事务复核；先查历史是否已覆盖，再读实际后端服务器文件并隔离验证。

## 2026-10-27 服务器回款分配与前端重铺算法对照（隔离验证通过，无应用修改）

- 恢复本轮完整目标、AGENTS/QUALITY_GOAL/历史进度与Git状态。上一轮完成正式部署，属于实质进展；本轮选择此前未逐场景对照的前后端分配算法，不重复登记已有锁修复或恢复人民币归档补丁。用户再次明确以服务器代码为主，已直接SSH读取当前服务器而非使用旧d6e147e快照。
- 直接服务器证据：DEPLOYED_COMMIT及WEB_DEPLOYED_COMMIT均为8bce10d526293115e2a8e8310b6dccab092b4e31；payment/service.py、router.py SHA256分别a72f012c9656b1ad3f2b549665a05c74a101b6f6ca0f082dfe265856f7e75749、3e998aa7f6f44c334258fcc8872666d72a4875d15caa13871971f71f200e79d2。服务器frontend/src/shared/payment-allocation.ts、AllocationAdjustModal.tsx分别385932c786e0e7003fcddf3eaf349bba7bdbabc9e43a7c527a6c8973094a707f、ce4cc83b70267749840fa7c6a242b243bd57c4a700b7fbaf8f9b87faf580cc5c，与本地逐项一致。直接读取服务分配/重分配和前端算法原文，并核对模型due_date约束。
- 排除一项静态疑点：前端可选due_date类型对空值排在最前、PG升序通常将NULL放末尾，但服务器ReceivablePlan.due_date非空，创建schema要求日期、编辑忽略null，不存在本轮已证明的正常空日期入口。不以防御性类型允许null直接确认生产排序Bug，也不擅自选择历史异常排序规则。
- 新验证脚本/tmp/crm-payment-allocation-parity.py：固定80个随机种子，逐个新建内存SQLite，仅合成CNY订单/应收/两笔已确认回款，实际service.allocate_payment及serialize_plan/serialize_allocations生成当前分配和前端输入；真实TypeScript源码经现有typescript转译执行fillPaymentByDueDate，与后端再次计算逐分对照。覆盖0至5期、输入乱序、同日按ID、取消期、空计划、其他人工回款占用及超过某期应收的占用、自己的旧自动分配、剩余待分配与分币尾差。80组分配及余额一致，后端重复计算稳定，没有以替代算法作为后端真值。
- 相关既有后端test_payment_rebalance_order/test_payment_consistency共24项退出0；ops/payment_allocation.test.mjs 8项通过，包括大额精确字符串与不足一分拒绝。脚本及三份日志归档ops/backups/payment-parity-review-20261027/。测试DATABASE_URL显式固定127.0.0.1:1不可连接隔离地址，实际SQL全部只在内存SQLite；没有生产数据库操作或真实外部调用。
- 范围限制：本轮是服务器同源函数/SQL与前端纯算法对照，没有完成生产浏览器保存、失败提示、缓存更新或PG并发的新验证，也不以80例推断所有金额组合。手工分配本来允许指定归属，不把人工选择与自动结果不同当Bug。没有应用/永久测试修改、提交、推送、部署或服务重启，12个原文档删除保留；长期全面目标仍未完成。下一独立验证方向为调整分配保存后的失败反馈及跨页面缓存刷新，需先核对服务器调用链，再在隔离浏览器验证。

## 2026-10-26 Q-026/Q-027/Q-029/Q-031/Q-032 发布完成

- 用户明确“改动没问题就部署”，据此执行本次正式发布。固定应用提交 `8bce10d526293115e2a8e8310b6dccab092b4e31` 已同步GitHub/Gitee现有pending-acceptance分支。应用仅变更4文件：AI确认/拒绝/取消互斥2项、订单逾期扫描父子锁序、客户失败行裁定报告、成本导入首次空行互斥；不包含已归档人民币改动，不包含Q-030隐藏方案，原12个文档删除未提交或覆盖。
- 发布前最终复验：1306项单元通过，统一发布安全测试通过，147套件登记/pyflakes/diff检查通过；本机PG复跑AI动作10组、提醒锁序及成本导入7组均成功。发现提醒套件全局扫描不能复用CI共用数据，修为父进程创建随机专用空库→迁移→子进程测试→删除；初稿子进程相对PYTHONPATH失败，修正绝对路径并补超时终止后通过，没有修改应用断言。相关变更一起进入上述固定提交。
- 远端CI同一SHA运行 `40299569634` 最终completed/success：后端全量接口回归＋静态/单元/登记检查通过，UI类型构建与浏览器冒烟通过。链接 https://github.com/ytsbx/crm-sales-agent/actions/runs/40299569634 。没有重跑失败作业或忽略业务断言。
- 统一构建从git archive固定SHA及实际服务器基线d6e147e生成。首次离线构建因缺prosemirror-model缓存拒绝；显式允许下载锁文件指定依赖后成功，未升级锁文件/依赖。包 `/tmp/crm-release-8bce10d.tar.gz` SHA256 `e1e66cb952e20a9bb533b1eb1f331ff75394a8af8c3e3f534360a4bc4b933da5`，发布工具SHA256 `8347192444e4ecf3347513b78f668b33a4fd62efa49624fc8082f90911ad5cab`，上传至服务器/tmp/crm-release-8bce10d并逐项核对一致。
- 服务器只预检通过后，等待CI全绿才执行--apply。工具完成既有源码/配置/迁移head/空间守卫、备份、短暂停止、切换、后端健康、完整新文件哈希、公网首页及入口资源核验，返回status=verified。回滚备份 `/opt/crm-sales-agent/backup/unified-8bce10d52629-va4fs18_` 保留；未升级数据库/依赖或修改.env、业务数据和服务配置。
- 发布后独立只读复核：DEPLOYED_COMMIT与WEB_DEPLOYED_COMMIT均8bce10d完整SHA，RELEASE.json verified同一提交，crm-sales.service active/NRestarts=0，8010/health status=up/database=ok；钉钉OA submission_enabled=true、sync_running=true、stream_connected=true。没有为上线验证制造真实业务写入、导入或外部消息。
- 发布记录与构建/预检/apply/CI/本机最终日志持久归档ops/backups/release-8bce10d-20261026/。临时本机PG已停止。上述5项修复从“本地未部署”更新为“已发布”，旧章节保留作历史证据；当前活动应用源码与本次固定提交一致。旧server-sync-20261025-01快照是d6e147e历史证据，不得再把它当新线上源码，后续审查需重新直接读取当前服务器。
- 范围限制：发布工具核验入口和文件/服务健康，不宣称生产真实角色端到端业务回归；长期全面审查未完成，Q-028半段回复及历史NULL业绩归属等已记录事项仍按既有决定/证据等级保留。本次部署请求已完成，后续独立发布仍需当次授权。

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
