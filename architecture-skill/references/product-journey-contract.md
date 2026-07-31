# 产品旅程合同

HTTP smoke 证明服务边界可调用，不证明产品可用。`project-product` 必须在真实浏览器执行以下关键旅程：

- 登录、初始化、选择非首个订单，异步详情返回后选择仍保持；
- 新建会话、发送消息、刷新并恢复历史；
- 每个完成的 assistant turn 在首次实时渲染时必须至少有 narrative、released block 或 interaction；空头像/空气泡是 P1 失败，HTTP 200 不能豁免；
- 同一 thread 刷新前后的可见文本与 presentation contract 集合必须一致，不能首次空白、刷新后再出现降级提示；
- 真实模型至少执行 12 轮订单集合筛选、代词/最高级、对象切换、任务中断恢复、意图纠正和新会话歧义，逐轮用独立语义 Oracle 验证订单对象与业务意图，不能只断言非空；
- 强上下文 repair 必须额外执行原种子与未见种子各 20 个隔离会话、每会话 10 轮的 configured-model Chromium Campaign；全部交互都通过登录后的网页输入框与发送按钮完成并读取 DOM，保存逐轮 Oracle、公开回复、contract、thread、刷新历史和诊断，按对话回归合同的阈值及零容忍失败类验收；
- 必须另用隔离 thread 原样执行客户报告的三轮链路“我都买了什么 → 哪些在路上 → 可以退货退款吗”，第三轮必须绑定第二轮实际展示的在途订单并给出资格语义；通用“未获得明确结果”、空卡片或误入提交能力均失败；
- 已发布集合中实际展示的精确成员可以作为后续单对象 ResultRef；Runtime 必须证明“成员属于同线程、同用户的已发布父集合”，不得要求成员自身另有顶层 presentation origin，也不得从集合中自动选择成员；
- 用户用已发布标签明确点名唯一成员时，模型若仍提交多成员集合，Runtime 必须在业务调用前拒绝；网页 Oracle 必须要求点名成员可见并禁止兄弟成员可见，不能因目标成员也包含在宽结果中而通过；
- 连续指代必须把最近一轮公开 ResultRef 作为默认话题层级，同时保留旧 ResultRef 供“回到/之前/最初”等显式话题返回；单成员集合上的最高级必须返回该唯一成员，不能因存在更早同类集合而制造歧义。该层级只是模型可见的对话新近度，不替代模型选择与 Runtime 对精确 handle 的验证；
- 连续指代同时包括“它/其中”等显式代词和中文省略主语的零指代；最新公开 ResultRef 只有一个成员时，下一轮只问资格/状态不得枚举旧集合追问对象。结构化澄清必须声明 missing kind，Runtime 只拒绝已经由唯一范围解决的 target/scope 澄清，不得阻断真实缺少 condition/intent 的追问；
- 集合展示后的泛化资格问题必须给出逐对象结论或明确追问具体订单，不能降级成展示完整性/内部协议告警；
- 明确追问具体订单后必须保留挂起目标；下一轮只输入商品/订单短标签时，要恢复原咨询或查询并只展示该对象，不能把短标签当成新的独立能力或返回通用失败。
- 同一挂起状态还必须验证显式放弃/话题切换：例如“先不问退款了，查订单10004能不能开发票”只能返回 10004 的发票结论，不得恢复退款、报告已有发票能力为 unsupported，或创建任何申请。
- 发票、物流、退款/售后等业务目标必须与各自订单分别绑定；对象正确但意图串线仍是失败；
- 强上下文主链必须包含“订单10004能开发票吗？我只问发票，不要退款，也不要售后。”并同时断言发票结论可见、退款/售后/物流政策不可见；返回跨域政策包视为 P1 失败；
- 新会话中的无唯一对象代词必须产生客户可理解的澄清，不能继承旧 thread，也不能返回空白；
- 从事务中心切换到另一 thread，并加载该 thread 的 pending interaction；
- 创建 Draft、跨多轮对话、恢复 Draft、补充输入、授权与结果对账；
- 两个 thread 分别拥有不同 Draft，不能互相泄漏；
- 390px 移动视口无需穿过订单详情即可到达聊天记录和输入框；
- chat log 具有 live-region 语义，输入框有 label，所有关键按钮可通过可访问名称定位；
- 内部 lifecycle enum、异常类型、stack 和 provider 错误不能直接展示给用户。
- 内部 presentation contract、renderer、coverage reason code 和 `registered_*`/`projection_contract_violation` 不能直接展示给用户。

Browser Gate 必须使用运行中的 Agent/Business 服务与隔离数据库。jsdom/Vitest 负责组件与 reducer 合同，不能冒充真实浏览器证据。

Quick 的确定性 Chromium 负责高频 UI、事务和实时/历史等价回归；Integration 的 configured-model Chromium 负责真实语义上下文，缺少当前配置模型环境必须阻断而不是跳过。两层 Gate 都必须有 Mutation：删除 notice block 渲染、弱化逐轮语义 Oracle或移除 configured-model Gate 时，反例套件必须失败。

真实模型 release Gate 必须自行启动或可证明地绑定使用真实 provider 的 Agent 进程。仅在 runner 环境放入真实 API Key、却复用由 deterministic stub 启动的 `AGENT_TEST_URL`，属于模型身份伪证。Planner、Verifier、Support 的调用预算必须分别封顶；Planner 不能耗尽目标/能力/最终发布 verifier 的保留额度。

托管本地 Integration 可以并行拥有一套 deterministic 公共 HTTP 测试服务，但在启动 Quality Controller 前必须从传入环境删除该 stub 的 `OPENAI_API_KEY`、`OPENAI_API_BASE` 和 `OPENAI_MODEL`。configured-model Chromium runner 必须重新读取工作区真实配置并自行启动 Agent；provider 额度、认证或网络失败必须保留红灯。
