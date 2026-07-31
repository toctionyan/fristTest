import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const DEFAULT_SEED = 20260715;
const GENERIC_FAILURES = [
  "系统未获得可继续办理的明确结果",
  "系统未能证明当前结果完整满足你的查询条件",
  "未获得可继续办理的明确结果",
  "未确认创建或提交任何业务申请",
  "请刷新后查看事务中心",
  "请重新说明需要处理的事项",
  "结果暂时无法完整展示",
];
const INTERNAL_MARKERS = [
  "Traceback",
  "Exception:",
  "tool_trace",
  "state_contract_violations",
  "projection_contract_violation",
  "registered_primary_presentation",
];

const t = (prompt, expected = {}) => ({ prompt, expected });
const has = (...requiredAny) => ({ requiredAny });
const exact = (...requiredAll) => ({ requiredAll });
const safeClarification = (requiredAny = ["请明确", "哪", "订单", "具体"]) => ({
  requiredAny,
  allowControlledFailure: true,
});
const safeUnsupported = (...requiredAny) => ({
  requiredAny: requiredAny.length ? requiredAny : ["不支持", "无法", "暂不", "不能"],
  allowControlledFailure: true,
});

// The campaign is deliberately domain-diverse but deterministic.  The seed
// changes scenario order and wording variants while preserving replayability.
const SCENARIOS = [
  {
    id: "orders-logistics-refund-recall",
    tags: ["orders", "visible-set", "pronoun", "topic-return"],
    turns: [
      t("我买过哪些东西？", exact("10001", "10002", "10003", "10004")),
      t("只看还在运输中的。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "运输中", "已发货"], forbidden: ["10003", "无线鼠标"] }),
      t("其中最贵的那个是哪件？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "199"] }),
      t("它现在是什么状态？", { requiredAny: ["10001", "蓝牙耳机", "运输中", "已发货"] }),
      t("它可以退货退款吗？先不要提交。", { requiredAll: ["10001"], requiredAny: ["退款", "资格", "不能", "暂不"], forbidden: ["申请成功", "已提交"] }),
      t("为什么不能？", { requiredAny: ["未签收", "配送", "运输", "退款"] }),
      t("先别办理。无线鼠标什么时候发货？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货", "备货"] }),
      t("它是哪一个订单？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "订单"] }),
      t("回到刚才的耳机，它到哪里了？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "分拨", "运输"] }),
      t("总结一下耳机和鼠标当前分别是什么状态。", { requiredAll: ["10001", "10003"], requiredAny: ["蓝牙耳机", "无线鼠标"] }),
    ],
  },
  {
    id: "clarify-resume-refund-target",
    tags: ["clarification", "resume", "short-answer", "goal-lock"],
    turns: [
      t("我都买了什么？", exact("10001", "10002", "10003", "10004")),
      t("可以退货退款吗？", safeClarification()),
      t("鼠标", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "退款", "资格"], forbidden: ["10001", "10002", "10004"] }),
      t("为什么？", { requiredAll: ["10003"], requiredAny: ["未签收", "待发货", "退款"] }),
      t("先不退款，查它的物流。", { requiredAll: ["10003"], requiredAny: ["待发货", "备货", "物流"] }),
      t("它的订单号是什么？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "订单"] }),
      t("改成机械键盘，它能退款吗？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "退款", "资格"] }),
      t("这个结论为什么和鼠标不一样？", { requiredAll: ["10002", "10003"], requiredAny: ["签收", "待发货", "退款"] }),
      t("不要提交，键盘现在是什么状态？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "已签收"] }),
      t("我当前问的是哪件商品？", { requiredAll: ["10002"], requiredAny: ["机械键盘"] }),
    ],
  },
  {
    id: "clarify-abandon-to-invoice",
    tags: ["clarification", "abandon", "new-request", "invoice"],
    turns: [
      t("列出我的订单。", exact("10001", "10002", "10003", "10004")),
      t("哪一个可以退款？", safeClarification()),
      t("先不问退款了，查订单10004能不能开发票。", { requiredAll: ["10004"], requiredAny: ["发票", "开票"], forbidden: ["退款资格", "10001", "10002", "10003"] }),
      t("为什么可以或者不可以？", { requiredAll: ["10004"], requiredAny: ["发票", "支付", "退款", "开票"] }),
      t("这个订单现在是什么状态？", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "已签收"] }),
      t("它的物流呢？", { requiredAll: ["10004"], requiredAny: ["签收", "送达", "物流"] }),
      t("这个杯子有保修吗？", { requiredAll: ["10004"], requiredAny: ["保修", "维修", "政策"] }),
      t("改问机械键盘的保修。", { requiredAll: ["10002"], requiredAny: ["机械键盘", "保修"] }),
      t("键盘能退吗？", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "退货"] }),
      t("只咨询，不要创建任何申请。", { forbidden: ["申请成功", "已提交", "已创建"] , requiredAny: ["不会", "不创建", "未创建", "仅咨询"] }),
    ],
  },
  {
    id: "invoice-policy-record-correction",
    tags: ["invoice-policy", "invoice-record", "correction", "recall"],
    turns: [
      t("我的哪些订单已经签收？", { requiredAll: ["10002", "10004"], requiredAny: ["机械键盘", "定制马克杯", "已签收"] }),
      t("订单10004能开发票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("它有没有开过发票？", { requiredAll: ["10004"], requiredAny: ["发票", "记录", "暂未", "没有"] }),
      t("如果还没开，申请发票需要什么？先不要申请。", { requiredAll: ["10004"], requiredAny: ["发票", "申请", "开票"], forbidden: ["申请成功", "已提交"] }),
      t("保持不申请。刚才查的是哪个订单？", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "发票"] }),
      t("改查订单10002能不能开发票。", { requiredAll: ["10002"], requiredAny: ["发票", "开票"] }),
      t("它有没有发票记录？", { requiredAll: ["10002"], requiredAny: ["发票", "记录", "暂未", "没有"] }),
      t("前一个订单是哪一个？", { requiredAll: ["10004"], requiredAny: ["定制马克杯"] }),
      t("后一个订单是哪一个？", { requiredAll: ["10002"], requiredAny: ["机械键盘"] }),
      t("总结这两个订单的开票情况，不要办理。", { requiredAll: ["10002", "10004"], requiredAny: ["发票", "开票"], forbidden: ["申请成功", "已提交"] }),
    ],
  },
  {
    id: "target-correction-pronoun",
    tags: ["correction", "pronoun", "interleaving", "target-authority"],
    turns: [
      t("查订单10004的详情。", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "已签收"] }),
      t("说错了，我要查10003。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货"], forbidden: ["10004"] }),
      t("它什么时候发货？", { requiredAll: ["10003"], requiredAny: ["待发货", "备货", "发货"] }),
      t("我说的是鼠标，不是耳机。", { requiredAll: ["10003"], requiredAny: ["无线鼠标"], forbidden: ["10001"] }),
      t("它可以退款吗？", { requiredAll: ["10003"], requiredAny: ["退款", "资格"] }),
      t("先不处理，回到杯子。", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "订单"] }),
      t("它能开发票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("它是否已经签收？", { requiredAll: ["10004"], requiredAny: ["已签收", "定制马克杯"] }),
      t("再回到鼠标，它的订单号？", { requiredAll: ["10003"], requiredAny: ["无线鼠标"] }),
      t("分别说出杯子和鼠标的订单状态。", { requiredAll: ["10003", "10004"], requiredAny: ["无线鼠标", "定制马克杯"] }),
    ],
  },
  {
    id: "cancel-draft-no-commit",
    tags: ["cancel", "draft", "no-commit", "transaction"],
    turns: [
      t("哪些订单还没发货？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货"] }),
      t("帮我取消这个鼠标订单。", { requiredAll: ["10003"], requiredAny: ["取消", "草稿", "确认", "待办"], forbidden: ["已取消", "取消成功"] }),
      t("先不提交，停止这个办理。", { requiredAny: ["停止", "取消草稿", "不办理", "已撤销", "未提交"], forbidden: ["已取消订单", "取消成功"] }),
      t("现在还有待处理草稿吗？", { requiredAny: ["草稿", "待处理", "没有", "暂无"] }),
      t("订单10003本身现在是什么状态？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货"] }),
      t("帮我取消订单10002，但仍然不要提交。", { requiredAll: ["10002"], requiredAny: ["取消", "不能", "草稿", "确认"], forbidden: ["取消成功", "已取消"] }),
      t("为什么这笔和鼠标不一样？", { requiredAny: ["签收", "待发货", "取消", "订单"] }),
      t("回到鼠标，我刚才想取消的是哪个订单？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "取消"] }),
      t("仍然不要提交任何操作。", { requiredAny: ["不会", "不提交", "未提交", "不办理"], forbidden: ["取消成功", "已取消"] }),
      t("查一下办理记录。", { requiredAny: ["办理", "记录", "草稿", "没有", "暂无"] }),
    ],
  },
  {
    id: "refund-draft-interaction",
    tags: ["refund", "eligibility", "draft", "interaction"],
    turns: [
      t("已签收的订单有哪些？", { requiredAll: ["10002", "10004"], requiredAny: ["已签收"] }),
      t("机械键盘可以退款吗？", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "机械键盘"] }),
      t("帮我准备退款，但先不要提交。", { requiredAll: ["10002"], requiredAny: ["退款", "原因", "草稿", "待办"], forbidden: ["退款成功", "已提交"] }),
      t("原因是不喜欢。", { requiredAny: ["不喜欢", "退款", "确认", "下一步", "待办"], forbidden: ["退款成功", "已提交"] }),
      t("停下来，不要提交。", { requiredAny: ["停止", "不提交", "不会提交", "取消草稿", "未提交"], forbidden: ["退款成功", "已提交"] }),
      t("刚才的办理记录是什么状态？", { requiredAny: ["办理", "草稿", "停止", "取消", "记录"] }),
      t("订单10002有没有退款记录？", { requiredAll: ["10002"], requiredAny: ["退款", "记录", "暂无", "没有"] }),
      t("再查杯子能不能退款。", { requiredAll: ["10004"], requiredAny: ["退款", "资格", "定制马克杯"] }),
      t("只咨询杯子，不创建申请。", { requiredAll: ["10004"], requiredAny: ["咨询", "不会", "不创建", "退款"], forbidden: ["已提交", "申请成功"] }),
      t("比较键盘和杯子的退款结论。", { requiredAll: ["10002", "10004"], requiredAny: ["退款", "机械键盘", "定制马克杯"] }),
    ],
  },
  {
    id: "after-sales-repair-draft",
    tags: ["after-sales", "repair", "draft", "warranty"],
    turns: [
      t("查机械键盘订单详情。", { requiredAll: ["10002"], requiredAny: ["机械键盘", "已签收"] }),
      t("签收后按键坏了，售后政策怎么处理？", { requiredAll: ["10002"], requiredAny: ["售后", "质量", "处理"] }),
      t("帮我申请维修，但不要提交。", { requiredAll: ["10002"], requiredAny: ["维修", "售后", "草稿", "问题"], forbidden: ["申请成功", "已提交"] }),
      t("问题描述是空格键失灵。", { requiredAny: ["空格键", "维修", "确认", "待办"], forbidden: ["申请成功", "已提交"] }),
      t("先不办了，停止草稿。", { requiredAny: ["停止", "不办理", "取消草稿", "未提交"], forbidden: ["申请成功", "已提交"] }),
      t("还有售后办理草稿吗？", { requiredAny: ["草稿", "待处理", "没有", "暂无"] }),
      t("这个键盘的保修规则呢？", { requiredAll: ["10002"], requiredAny: ["保修", "维修"] }),
      t("改查杯子坏了的售后政策。", { requiredAll: ["10004"], requiredAny: ["售后", "定制马克杯"] }),
      t("杯子不要申请，只咨询。", { requiredAll: ["10004"], requiredAny: ["咨询", "不申请", "售后"], forbidden: ["申请成功", "已提交"] }),
      t("总结键盘维修和杯子售后的区别。", { requiredAll: ["10002", "10004"], requiredAny: ["维修", "售后"] }),
    ],
  },
  {
    id: "invoice-draft-stop-history",
    tags: ["invoice", "draft", "interaction", "history"],
    turns: [
      t("查定制马克杯的订单详情。", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "已签收"] }),
      t("这个订单可以开发票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("帮我准备开票申请，但不要提交。", { requiredAll: ["10004"], requiredAny: ["发票", "抬头", "草稿", "待办"], forbidden: ["开票成功", "已提交"] }),
      t("抬头写个人。", { requiredAny: ["个人", "发票", "确认", "待办"], forbidden: ["开票成功", "已提交"] }),
      t("停止，不提交这个申请。", { requiredAny: ["停止", "不提交", "取消草稿", "未提交"], forbidden: ["开票成功", "已提交"] }),
      t("订单10004有没有发票记录？", { requiredAll: ["10004"], requiredAny: ["发票", "记录", "暂无", "没有"] }),
      t("刚才的开票办理是什么状态？", { requiredAny: ["办理", "开票", "草稿", "停止", "记录"] }),
      t("订单本身现在是什么状态？", { requiredAll: ["10004"], requiredAny: ["已签收", "定制马克杯"] }),
      t("机械键盘能开发票吗？", { requiredAll: ["10002"], requiredAny: ["发票", "开票"] }),
      t("刚才准备开票的是哪个订单？", { requiredAll: ["10004"], requiredAny: ["发票", "定制马克杯"] }),
    ],
  },
  {
    id: "business-record-type-isolation",
    tags: ["refund-record", "invoice-record", "after-sales-record", "type-isolation"],
    turns: [
      t("查我的退款记录。", { requiredAny: ["退款", "记录", "暂无", "没有"] }),
      t("只看订单10002的退款记录。", { requiredAll: ["10002"], requiredAny: ["退款", "记录", "暂无", "没有"] }),
      t("现在改查发票记录。", { requiredAny: ["发票", "记录", "暂无", "没有"] }),
      t("只看订单10004的发票。", { requiredAll: ["10004"], requiredAny: ["发票", "记录", "暂无", "没有"] }),
      t("再改查售后工单。", { requiredAny: ["售后", "工单", "记录", "暂无", "没有"] }),
      t("机械键盘有没有售后记录？", { requiredAll: ["10002"], requiredAny: ["售后", "记录", "暂无", "没有"] }),
      t("我刚才依次查了哪三类记录？", { requiredAny: ["退款", "发票", "售后"] }),
      t("这些查询不要创建任何申请。", { requiredAny: ["不会", "不创建", "仅查询", "没有创建"], forbidden: ["申请成功", "已提交"] }),
      t("订单10002本身是什么状态？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "已签收"] }),
      t("最后一次业务记录查询针对什么？", { requiredAll: ["10002"], requiredAny: ["售后", "机械键盘"] }),
    ],
  },
  {
    id: "policy-capability-distinction",
    tags: ["refund-policy", "eligibility", "warranty", "after-sales"],
    turns: [
      t("一般退款规则是什么？", { requiredAny: ["退款", "规则", "政策", "退货"] }),
      t("具体到蓝牙耳机，它现在能退吗？", { requiredAll: ["10001"], requiredAny: ["退款", "资格", "蓝牙耳机"] }),
      t("这两个问题有什么不同？", { requiredAny: ["规则", "政策", "资格", "订单"] }),
      t("先不退款，查耳机的保修规则。", { requiredAll: ["10001"], requiredAny: ["保修", "蓝牙耳机"] }),
      t("如果耳机坏了，售后怎么处理？", { requiredAll: ["10001"], requiredAny: ["售后", "质量", "处理"] }),
      t("不要申请，只说政策。", { requiredAny: ["政策", "不会", "不申请", "售后"], forbidden: ["申请成功", "已提交"] }),
      t("无线鼠标可以退款吗？", { requiredAll: ["10003"], requiredAny: ["退款", "资格", "无线鼠标"] }),
      t("它的退款记录呢？", { requiredAll: ["10003"], requiredAny: ["退款", "记录", "暂无", "没有"] }),
      t("回到耳机，我问过哪两种政策？", { requiredAll: ["10001"], requiredAny: ["保修", "售后"] }),
      t("当前不要执行任何业务操作。", { requiredAny: ["不会", "不执行", "仅查询", "不办理"], forbidden: ["申请成功", "已提交"] }),
    ],
  },
  {
    id: "unsupported-and-supported-switch",
    tags: ["unsupported", "capability-switch", "recovery"],
    turns: [
      t("订单10002能直接换成别的品牌吗？", safeUnsupported("不支持", "无法", "换货", "售后")),
      t("那先查这个订单现在的状态。", { requiredAll: ["10002"], requiredAny: ["机械键盘", "已签收"] }),
      t("可以修改这个订单的收货地址吗？", safeUnsupported("不支持", "无法", "地址", "不能")),
      t("不改地址，查它能不能开发票。", { requiredAll: ["10002"], requiredAny: ["发票", "开票"] }),
      t("能用礼品卡给这个已完成订单补付款吗？", safeUnsupported("不支持", "无法", "礼品卡", "不能")),
      t("回到支持的能力，查它的退款资格。", { requiredAll: ["10002"], requiredAny: ["退款", "资格"] }),
      t("如果要维修，可以准备售后吗？先不提交。", { requiredAll: ["10002"], requiredAny: ["维修", "售后", "草稿", "问题"], forbidden: ["申请成功", "已提交"] }),
      t("停止这个售后草稿。", { requiredAny: ["停止", "取消草稿", "不办理", "未提交"], forbidden: ["申请成功", "已提交"] }),
      t("刚才哪些需求不在能力范围？", { requiredAny: ["品牌", "地址", "礼品卡", "不支持"] }),
      t("最后支持并查询成功的是什么？", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "机械键盘"] }),
    ],
  },
  {
    id: "multi-intent-interleaving",
    tags: ["multi-intent", "ordinal-reference", "interleaving"],
    turns: [
      t("同时查订单10001的物流和订单10004的开票政策。", { requiredAll: ["10001", "10004"], requiredAny: ["物流", "运输", "发票", "开票"] }),
      t("第一个现在到哪里了？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "分拨", "运输"] }),
      t("第二个到底能不能开票？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("第一个是哪件商品？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机"] }),
      t("把第二个改成订单10002，还是问开票。", { requiredAll: ["10002"], requiredAny: ["发票", "开票", "机械键盘"] }),
      t("第一个保持不变，它的状态？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "已发货", "运输"] }),
      t("再查无线鼠标的物流。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货", "备货"] }),
      t("比较最开始的耳机和现在的鼠标物流。", { requiredAll: ["10001", "10003"], requiredAny: ["蓝牙耳机", "无线鼠标"] }),
      t("这些都只查询，不要申请。", { requiredAny: ["查询", "不会", "不申请", "没有申请"], forbidden: ["申请成功", "已提交"] }),
      t("总结本轮涉及的三个订单号。", { requiredAll: ["10001", "10002", "10003"], requiredAny: ["订单"] }),
    ],
  },
  {
    id: "visible-set-algebra",
    tags: ["visible-set", "filter", "superlative", "set-difference"],
    turns: [
      t("列出全部订单。", exact("10001", "10002", "10003", "10004")),
      t("其中已经签收的有哪些？", { requiredAll: ["10002", "10004"], requiredAny: ["已签收"], forbidden: ["10001", "10003"] }),
      t("这两个里面最贵的是哪个？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "399"] }),
      t("便宜的那个呢？", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "59"] }),
      t("除了杯子，签收集合里还剩谁？", { requiredAll: ["10002"], requiredAny: ["机械键盘"], forbidden: ["10004"] }),
      t("剩下这个订单现在什么状态？", { requiredAll: ["10002"], requiredAny: ["已签收", "机械键盘"] }),
      t("它的保修规则是什么？", { requiredAll: ["10002"], requiredAny: ["保修", "机械键盘"] }),
      t("回到便宜的杯子，它能开发票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("杯子有发票记录吗？", { requiredAll: ["10004"], requiredAny: ["发票", "记录", "暂无", "没有"] }),
      t("总结签收集合里这两个订单，不要办理。", { requiredAll: ["10002", "10004"], requiredAny: ["已签收"], forbidden: ["申请成功", "已提交"] }),
    ],
  },
  {
    id: "fresh-thread-ambiguity-isolation",
    tags: ["thread-isolation", "ambiguity", "resume"],
    turns: [
      t("它现在能退吗？", { ...safeClarification(), forbidden: ["10001", "10002", "10003", "10004"] }),
      t("我说订单10001。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "退款", "资格"] }),
      t("为什么？", { requiredAny: ["未签收", "运输", "退款"] }),
      t("它是什么商品？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机"] }),
      t("改成订单10003。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "订单"] }),
      t("它现在的状态？", { requiredAll: ["10003"], requiredAny: ["待发货", "无线鼠标"] }),
      t("它能退款吗？", { requiredAll: ["10003"], requiredAny: ["退款", "资格"] }),
      t("不要提交。", { requiredAny: ["不会", "不提交", "未提交", "仅查询"], forbidden: ["申请成功", "已提交"] }),
      t("这次对话里我明确提过哪两个订单？", { requiredAll: ["10001", "10003"], requiredAny: ["订单"] }),
      t("当前对象是哪一个？", { requiredAll: ["10003"], requiredAny: ["无线鼠标"] }),
    ],
  },
  {
    id: "transaction-interleaving-return",
    tags: ["transaction", "topic-switch", "return", "no-commit"],
    turns: [
      t("订单10004能开票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("准备一个开票申请，先别提交。", { requiredAll: ["10004"], requiredAny: ["发票", "草稿", "抬头", "待办"], forbidden: ["开票成功", "已提交"] }),
      t("抬头个人。", { requiredAny: ["个人", "发票", "确认", "待办"], forbidden: ["开票成功", "已提交"] }),
      t("先切出去，查蓝牙耳机物流。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "运输", "分拨"] }),
      t("耳机能退款吗？", { requiredAll: ["10001"], requiredAny: ["退款", "资格"] }),
      t("回到刚才的开票办理，它是什么状态？", { requiredAny: ["开票", "办理", "草稿", "待确认"] }),
      t("刚才开票针对哪个订单？", { requiredAll: ["10004"], requiredAny: ["发票", "定制马克杯"] }),
      t("不要提交并停止这个草稿。", { requiredAny: ["停止", "不提交", "取消草稿", "未提交"], forbidden: ["开票成功", "已提交"] }),
      t("再查耳机现在到哪。", { requiredAll: ["10001"], requiredAny: ["运输", "分拨", "蓝牙耳机"] }),
      t("确认本轮没有提交任何申请。", { requiredAny: ["没有提交", "未提交", "不会提交", "未创建"], forbidden: ["申请成功", "开票成功"] }),
    ],
  },
  {
    id: "after-sales-record-to-refund",
    tags: ["after-sales-record", "refund", "goal-switch"],
    turns: [
      t("查所有售后工单。", { requiredAny: ["售后", "工单", "暂无", "没有"] }),
      t("只看机械键盘相关的。", { requiredAll: ["10002"], requiredAny: ["售后", "机械键盘", "暂无", "没有"] }),
      t("如果没有，告诉我键盘售后政策。", { requiredAll: ["10002"], requiredAny: ["售后", "政策", "机械键盘"] }),
      t("先不申请，查键盘退款资格。", { requiredAll: ["10002"], requiredAny: ["退款", "资格"] }),
      t("为什么？", { requiredAny: ["签收", "退款", "期限", "条件"] }),
      t("键盘有没有退款记录？", { requiredAll: ["10002"], requiredAny: ["退款", "记录", "暂无", "没有"] }),
      t("回到售后，刚才查的是哪个商品？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "售后"] }),
      t("换成杯子查售后政策。", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "售后"] }),
      t("杯子也不要申请。", { requiredAny: ["不申请", "不会", "仅咨询", "未创建"], forbidden: ["申请成功", "已提交"] }),
      t("总结两个商品的咨询对象。", { requiredAll: ["10002", "10004"], requiredAny: ["机械键盘", "定制马克杯"] }),
    ],
  },
  {
    id: "repeated-correction-authority",
    tags: ["correction", "authority", "negative-reference"],
    turns: [
      t("查订单10004的状态。", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "已签收"] }),
      t("不对，改成10003。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货"], forbidden: ["10004"] }),
      t("还不对，我其实要10001。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "已发货"], forbidden: ["10003", "10004"] }),
      t("它的物流到哪了？", { requiredAll: ["10001"], requiredAny: ["分拨", "运输", "蓝牙耳机"] }),
      t("不是鼠标，对吧？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机"], forbidden: ["10003"] }),
      t("现在改回10003查物流。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货", "备货"] }),
      t("它能退吗？", { requiredAll: ["10003"], requiredAny: ["退款", "资格"] }),
      t("前一个耳机能退吗？", { requiredAll: ["10001"], requiredAny: ["退款", "资格", "蓝牙耳机"] }),
      t("当前对象再改回鼠标。", { requiredAll: ["10003"], requiredAny: ["无线鼠标"] }),
      t("最后确认当前订单号。", { requiredAll: ["10003"], requiredAny: ["订单"] }),
    ],
  },
  {
    id: "negative-intent-switch",
    tags: ["negative-constraint", "intent-switch", "capability-isolation"],
    turns: [
      t("只查订单10004发票政策，不要退款，也不要售后。", { requiredAll: ["10004"], requiredAny: ["发票", "开票"], forbidden: ["退款资格", "售后政策"] }),
      t("它有没有发票记录？", { requiredAll: ["10004"], requiredAny: ["发票", "记录", "暂无", "没有"] }),
      t("现在改成查退款资格，不再问发票。", { requiredAll: ["10004"], requiredAny: ["退款", "资格"], forbidden: ["开票政策"] }),
      t("为什么是这个退款结论？", { requiredAll: ["10004"], requiredAny: ["退款", "签收", "条件"] }),
      t("再改成售后政策，不要退款申请。", { requiredAll: ["10004"], requiredAny: ["售后", "政策"], forbidden: ["申请成功", "已提交"] }),
      t("我依次切换了哪三种能力？", { requiredAny: ["发票", "退款", "售后"] }),
      t("把对象改成机械键盘，保持售后咨询。", { requiredAll: ["10002"], requiredAny: ["售后", "机械键盘"] }),
      t("它的保修政策呢？", { requiredAll: ["10002"], requiredAny: ["保修", "机械键盘"] }),
      t("不要申请维修。", { requiredAny: ["不申请", "不会", "仅咨询", "未创建"], forbidden: ["申请成功", "已提交"] }),
      t("最后当前能力和对象是什么？", { requiredAll: ["10002"], requiredAny: ["保修", "机械键盘"] }),
    ],
  },
  {
    id: "multi-target-action-clarification",
    tags: ["multi-target", "action", "clarification", "safe-draft"],
    turns: [
      t("列出已签收订单。", { requiredAll: ["10002", "10004"], requiredAny: ["已签收"] }),
      t("把这两个都申请退款。", { ...safeClarification(["一个", "分别", "明确", "订单", "不能同时"]), forbidden: ["申请成功", "已提交"] }),
      t("只处理机械键盘，先查资格。", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "机械键盘"] }),
      t("按这个资格准备退款，但不提交。", { requiredAll: ["10002"], requiredAny: ["退款", "原因", "草稿", "待办"], forbidden: ["退款成功", "已提交"] }),
      t("原因是不喜欢。", { requiredAny: ["不喜欢", "退款", "确认", "待办"], forbidden: ["退款成功", "已提交"] }),
      t("停止办理。", { requiredAny: ["停止", "取消草稿", "不办理", "未提交"], forbidden: ["退款成功", "已提交"] }),
      t("另一个签收订单是什么？", { requiredAll: ["10004"], requiredAny: ["定制马克杯"] }),
      t("杯子只查退款资格。", { requiredAll: ["10004"], requiredAny: ["退款", "资格"] }),
      t("不要为杯子创建草稿。", { requiredAny: ["不创建", "不会", "仅查询", "未创建"], forbidden: ["申请成功", "已提交"] }),
      t("总结两笔资格查询和实际草稿情况。", { requiredAll: ["10002", "10004"], requiredAny: ["退款", "草稿", "资格"], forbidden: ["退款成功", "已提交"] }),
    ],
  },
  {
    id: "long-comprehensive-interleave",
    tags: ["long-context", "all-read-types", "return-to-prior"],
    turns: [
      t("我有哪些订单？", exact("10001", "10002", "10003", "10004")),
      t("最贵的是哪个？", { requiredAll: ["10002"], requiredAny: ["机械键盘", "399"] }),
      t("它能退款吗？", { requiredAll: ["10002"], requiredAny: ["退款", "资格"] }),
      t("先不退，最便宜的订单是什么？", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "59"] }),
      t("这个最便宜的能开发票吗？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("再查还在路上的订单。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "运输", "已发货"] }),
      t("它到哪里了？", { requiredAll: ["10001"], requiredAny: ["分拨", "运输"] }),
      t("回到最贵的，它有没有售后记录？", { requiredAll: ["10002"], requiredAny: ["售后", "记录", "暂无", "没有"] }),
      t("回到最便宜的，我问过它什么？", { requiredAll: ["10004"], requiredAny: ["发票", "开票"] }),
      t("按出现顺序总结这三个订单及问题，不要办理。", { requiredAll: ["10002", "10004", "10001"], requiredAny: ["退款", "发票", "物流"], forbidden: ["申请成功", "已提交"] }),
    ],
  },
  {
    id: "history-reload-and-last-goal",
    tags: ["history", "reload", "last-goal", "type-recall"],
    turns: [
      t("查蓝牙耳机物流。", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "运输", "分拨"] }),
      t("查无线鼠标物流。", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货", "备货"] }),
      // Comparative answers may name the losing candidate to explain why it
      // does not satisfy the predicate.  Exact-id isolation remains the
      // default everywhere else; these two turns explicitly opt into the
      // contrast while still requiring the correct winner and state.
      t("刚才两个哪个已经发出？", { requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "已发货", "运输"], allowAdditionalOrderIds: true }),
      t("哪个还没发？", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "待发货"], allowAdditionalOrderIds: true }),
      t("查机械键盘退款资格。", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "机械键盘"] }),
      t("查定制马克杯发票政策。", { requiredAll: ["10004"], requiredAny: ["发票", "开票", "定制马克杯"] }),
      t("倒数第二个问题针对哪个订单？", { requiredAll: ["10002"], requiredAny: ["退款", "机械键盘"] }),
      t("最后一个问题针对哪个订单？", { requiredAll: ["10004"], requiredAny: ["发票", "定制马克杯"] }),
      t("最开始查了哪两个商品的物流？", { requiredAll: ["10001", "10003"], requiredAny: ["蓝牙耳机", "无线鼠标"] }),
      t("总结四个目标和对应能力。", { requiredAll: ["10001", "10003", "10002", "10004"], requiredAny: ["物流", "退款", "发票"] }),
    ],
  },
  {
    id: "vague-label-resolution",
    tags: ["short-label", "vague-reference", "clarification", "correction"],
    turns: [
      t("查我的订单。", exact("10001", "10002", "10003", "10004")),
      t("杯子", { requiredAll: ["10004"], requiredAny: ["定制马克杯", "订单"] }),
      t("状态", { requiredAll: ["10004"], requiredAny: ["已签收"] }),
      t("发票", { requiredAll: ["10004"], requiredAny: ["发票", "开票", "记录"] }),
      t("不是申请，只问能不能开。", { requiredAll: ["10004"], requiredAny: ["发票", "开票"], forbidden: ["申请成功", "已提交"] }),
      t("键盘", { requiredAll: ["10002"], requiredAny: ["机械键盘", "订单"] }),
      t("退款", { requiredAll: ["10002"], requiredAny: ["退款", "资格", "记录", "政策"] }),
      t("我是问能不能退。", { requiredAll: ["10002"], requiredAny: ["退款", "资格"] }),
      t("鼠标", { requiredAll: ["10003"], requiredAny: ["无线鼠标", "订单"] }),
      t("当前我最后明确的是哪个商品？", { requiredAll: ["10003"], requiredAny: ["无线鼠标"] }),
    ],
  },
];

function seededShuffle(values, seed) {
  let state = seed >>> 0;
  const random = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  const result = [...values];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const target = Math.floor(random() * (index + 1));
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

function normalize(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

async function waitUntil(predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${label} timed out${lastError ? `: ${String(lastError?.message || lastError)}` : ""}`);
}

function evaluateTurn(text, expected = {}) {
  const errors = [];
  if (!text) errors.push("empty_response");
  for (const marker of INTERNAL_MARKERS) {
    if (text.includes(marker)) errors.push(`internal_marker:${marker}`);
  }
  for (const marker of expected.requiredAll || []) {
    if (!text.includes(marker)) errors.push(`missing_required:${marker}`);
  }
  if ((expected.requiredAny || []).length && !expected.requiredAny.some((marker) => text.includes(marker))) {
    errors.push(`missing_any:${expected.requiredAny.join("|")}`);
  }
  for (const marker of expected.forbidden || []) {
    if (text.includes(marker)) errors.push(`forbidden:${marker}`);
  }
  // When an oracle declares concrete order ids, the visible answer must
  // contain exactly that identity set.  A positive substring assertion alone
  // lets a superlative falsely pass when the page renders both the winner and
  // every losing candidate, and it also hides stale-target leakage.
  const expectedOrderIds = [...new Set((expected.requiredAll || [])
    .filter((marker) => /^\d{5}$/.test(String(marker))))].sort();
  if (expectedOrderIds.length && !expected.allowAdditionalOrderIds) {
    const actualOrderIds = [...new Set(text.match(/\b\d{5}\b/g) || [])].sort();
    for (const orderId of actualOrderIds) {
      if (!expectedOrderIds.includes(orderId)) errors.push(`unexpected_order_id:${orderId}`);
    }
  }
  if (!expected.allowControlledFailure) {
    for (const marker of GENERIC_FAILURES) {
      if (text.includes(marker)) errors.push(`generic_failure:${marker}`);
    }
  }
  return { pass: errors.length === 0, errors };
}

async function textOf(locator) {
  return normalize(await locator.innerText().catch(() => ""));
}

async function transcript(tab) {
  return tab.playwright.evaluate(() => Array.from(document.querySelectorAll(".chat-message"))
    .slice(-80)
    .map((row) => ({
      role: row.classList.contains("user") ? "user" : "agent",
      text: String(row.textContent || "").replace(/\s+/g, " ").trim(),
    })));
}

async function currentThreadId(tab) {
  return textOf(tab.playwright.locator(".thread-tools strong"));
}

async function ensureLoggedIn(tab, baseURL) {
  await tab.goto(baseURL);
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const login = tab.playwright.getByRole("button", { name: "登录" });
  const loginCount = await login.count();
  if (loginCount === 1) {
    const account = tab.playwright.getByLabel("账号");
    const password = tab.playwright.getByLabel("密码");
    if (await account.count() !== 1 || await password.count() !== 1) throw new Error("login fields are not unique");
    await account.selectOption("customer_u001");
    await password.fill("123456");
    await login.click();
  } else if (loginCount > 1) {
    throw new Error("login button is not unique");
  }
  await tab.playwright.getByText("u001 · default").waitFor({ state: "visible", timeoutMs: 30_000 });
  await tab.playwright.getByRole("textbox", { name: "输入问题" }).waitFor({ state: "visible", timeoutMs: 30_000 });
}

async function newThread(tab) {
  const before = await currentThreadId(tab);
  const button = tab.playwright.getByRole("button", { name: "新会话" });
  if (await button.count() !== 1) throw new Error("new conversation button is not unique");
  await button.click();
  await waitUntil(
    async () => {
      const current = await currentThreadId(tab);
      return Boolean(current && current !== before);
    },
    30_000,
    "new thread",
  );
  return currentThreadId(tab);
}

async function contractIds(locator) {
  return locator.evaluate((row) => Array.from(row.querySelectorAll("[data-contract-id]"))
    .map((node) => node.getAttribute("data-contract-id"))
    .filter((value, index, values) => Boolean(value) && values.indexOf(value) === index));
}

async function sendTurn(tab, turn, turnIndex) {
  const startedAt = new Date().toISOString();
  const agents = tab.playwright.locator(".chat-message.agent");
  const before = await agents.count();
  const input = tab.playwright.getByRole("textbox", { name: "输入问题" });
  const send = tab.playwright.getByRole("button", { name: "发送" });
  try {
    if (await input.count() !== 1 || await send.count() !== 1) throw new Error("chat composer is not unique");
    await input.fill(turn.prompt);
    await waitUntil(
      async () => await send.isEnabled(),
      30_000,
      "send button enabled",
    );
    await send.click();
    await waitUntil(
      async () => (await agents.count()) > before,
      150_000,
      "agent response",
    );
    // Use a stable positive index. The in-app Browser's dynamic ``last``
    // selector can be invalidated while React replaces the streaming bubble.
    const current = agents.nth((await agents.count()) - 1);
    let text = "";
    await waitUntil(
      async () => {
        text = await textOf(current);
        return Boolean(text);
      },
      150_000,
      "non-empty agent response",
    );
    const verdict = evaluateTurn(text, turn.expected);
    return {
      turn: turnIndex,
      prompt: turn.prompt,
      expected: turn.expected,
      response: text,
      contracts: await contractIds(current).catch(() => []),
      ...verdict,
      startedAt,
      finishedAt: new Date().toISOString(),
    };
  } catch (error) {
    return {
      turn: turnIndex,
      prompt: turn.prompt,
      expected: turn.expected,
      response: "",
      contracts: [],
      pass: false,
      errors: [`browser_error:${String(error?.message || error)}`],
      startedAt,
      finishedAt: new Date().toISOString(),
    };
  }
}

async function persist(report, artifactPath) {
  await mkdir(path.dirname(artifactPath), { recursive: true });
  await writeFile(artifactPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

export function campaignDefinition(seed = DEFAULT_SEED) {
  const ordered = seededShuffle(SCENARIOS, seed).slice(0, 20);
  return {
    seed,
    scenarioCount: ordered.length,
    turnsPerScenario: 10,
    totalTurns: ordered.reduce((sum, scenario) => sum + scenario.turns.length, 0),
    order: ordered.map((scenario) => scenario.id),
    scenarios: ordered,
  };
}

export async function initializeCampaign(tab, options = {}) {
  const definition = campaignDefinition(options.seed || DEFAULT_SEED);
  const report = {
    schemaVersion: 1,
    campaignId: options.campaignId || `context-campaign-${definition.seed}`,
    phase: options.phase || "baseline",
    engine: "codex-in-app-browser",
    baseURL: options.baseURL || "http://127.0.0.1:5173/web/",
    artifactPath: options.artifactPath,
    seed: definition.seed,
    scenarioOrder: definition.order,
    plannedScenarios: definition.scenarioCount,
    plannedTurns: definition.totalTurns,
    startedAt: new Date().toISOString(),
    scenarios: [],
  };
  await ensureLoggedIn(tab, report.baseURL);
  if (report.artifactPath) await persist(report, report.artifactPath);
  return { definition, report };
}

export async function beginNextScenario(tab, campaign) {
  const index = campaign.report.scenarios.length;
  const scenario = campaign.definition.scenarios[index];
  if (!scenario) return { done: true, summary: summarizeCampaign(campaign.report) };
  const threadId = await newThread(tab);
  const scenarioResult = {
    index: index + 1,
    id: scenario.id,
    tags: scenario.tags,
    threadId,
    startedAt: new Date().toISOString(),
    turns: [],
  };
  campaign.report.activeScenario = scenarioResult;
  campaign.report.updatedAt = new Date().toISOString();
  if (campaign.report.artifactPath) await persist(campaign.report, campaign.report.artifactPath);
  return { done: false, index, scenario, scenarioResult, campaign };
}

export async function runNextTurn(tab, scenarioRun) {
  if (scenarioRun?.done) return scenarioRun;
  const turnIndex = scenarioRun.scenarioResult.turns.length;
  const turn = scenarioRun.scenario.turns[turnIndex];
  if (!turn) {
    return {
      done: true,
      scenarioId: scenarioRun.scenario.id,
      completedTurns: turnIndex,
    };
  }
  const result = await sendTurn(tab, turn, turnIndex + 1);
  scenarioRun.scenarioResult.turns.push(result);
  if (scenarioRun.campaign?.report) {
    scenarioRun.campaign.report.activeScenario = scenarioRun.scenarioResult;
    scenarioRun.campaign.report.updatedAt = new Date().toISOString();
    if (scenarioRun.campaign.report.artifactPath) {
      await persist(scenarioRun.campaign.report, scenarioRun.campaign.report.artifactPath);
    }
  }
  return {
    done: scenarioRun.scenarioResult.turns.length >= scenarioRun.scenario.turns.length,
    scenarioId: scenarioRun.scenario.id,
    turn: result,
    completedTurns: scenarioRun.scenarioResult.turns.length,
  };
}

async function recordChunkedTurn(scenarioRun, result) {
  scenarioRun.scenarioResult.turns.push(result);
  if (scenarioRun.campaign?.report) {
    scenarioRun.campaign.report.activeScenario = scenarioRun.scenarioResult;
    scenarioRun.campaign.report.updatedAt = new Date().toISOString();
    if (scenarioRun.campaign.report.artifactPath) {
      await persist(scenarioRun.campaign.report, scenarioRun.campaign.report.artifactPath);
    }
  }
  return {
    done: scenarioRun.scenarioResult.turns.length >= scenarioRun.scenario.turns.length,
    scenarioId: scenarioRun.scenario.id,
    turn: result,
    completedTurns: scenarioRun.scenarioResult.turns.length,
  };
}

// In-app browser control has a shorter synchronous call budget than a real
// model turn.  Split UI submission from observation so the HTTP request keeps
// running in the page while the controller polls in small, restart-safe calls.
// This remains a real page interaction: no chat API is called by the runner.
export async function startNextTurn(tab, scenarioRun) {
  if (scenarioRun?.done) return scenarioRun;
  if (scenarioRun.pendingTurn) throw new Error("a browser turn is already pending");
  const turnIndex = scenarioRun.scenarioResult.turns.length;
  const turn = scenarioRun.scenario.turns[turnIndex];
  if (!turn) return { done: true, completedTurns: turnIndex };
  const agents = tab.playwright.locator(".chat-message.agent");
  const before = await agents.count();
  const input = tab.playwright.getByRole("textbox", { name: "输入问题" });
  const send = tab.playwright.getByRole("button", { name: "发送" });
  const startedAt = new Date().toISOString();
  try {
    if (await input.count() !== 1 || await send.count() !== 1) {
      throw new Error("chat composer is not unique");
    }
    await input.fill(turn.prompt);
    await waitUntil(async () => await send.isEnabled(), 30_000, "send button enabled");
    await send.click();
    scenarioRun.pendingTurn = { turnIndex, turn, before, startedAt };
    return { done: false, submitted: true, turn: turnIndex + 1, prompt: turn.prompt };
  } catch (error) {
    const result = {
      turn: turnIndex + 1,
      prompt: turn.prompt,
      expected: turn.expected,
      response: "",
      contracts: [],
      pass: false,
      errors: [`browser_error:${String(error?.message || error)}`],
      startedAt,
      finishedAt: new Date().toISOString(),
    };
    return recordChunkedTurn(scenarioRun, result);
  }
}

export async function pollPendingTurn(tab, scenarioRun) {
  const pending = scenarioRun?.pendingTurn;
  if (!pending) throw new Error("no browser turn is pending");
  const agents = tab.playwright.locator(".chat-message.agent");
  const count = await agents.count();
  if (count <= pending.before) {
    return { done: false, pending: true, turn: pending.turnIndex + 1 };
  }
  const current = agents.nth(count - 1);
  const text = await textOf(current);
  if (!text) return { done: false, pending: true, turn: pending.turnIndex + 1 };
  const verdict = evaluateTurn(text, pending.turn.expected);
  const result = {
    turn: pending.turnIndex + 1,
    prompt: pending.turn.prompt,
    expected: pending.turn.expected,
    response: text,
    contracts: await contractIds(current).catch(() => []),
    ...verdict,
    startedAt: pending.startedAt,
    finishedAt: new Date().toISOString(),
  };
  delete scenarioRun.pendingTurn;
  return recordChunkedTurn(scenarioRun, result);
}

export async function completeScenario(tab, campaign, scenarioRun) {
  if (scenarioRun?.done) return scenarioRun;
  const { index, scenario, scenarioResult } = scenarioRun;
  if (scenarioResult.turns.length !== scenario.turns.length) {
    throw new Error(
      `scenario ${scenario.id} is incomplete: ${scenarioResult.turns.length}/${scenario.turns.length}`,
    );
  }
  const live = await transcript(tab);
  await tab.reload();
  await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 30_000 });
  await tab.playwright.getByRole("textbox", { name: "输入问题" }).waitFor({ state: "visible", timeoutMs: 30_000 });
  const restored = await transcript(tab);
  scenarioResult.reloadEquivalent = JSON.stringify(live) === JSON.stringify(restored);
  scenarioResult.transcript = restored;
  scenarioResult.finishedAt = new Date().toISOString();
  scenarioResult.passTurns = scenarioResult.turns.filter((turn) => turn.pass).length;
  scenarioResult.failedTurns = scenarioResult.turns.length - scenarioResult.passTurns;
  scenarioResult.pass = scenarioResult.failedTurns === 0 && scenarioResult.reloadEquivalent;
  delete campaign.report.activeScenario;
  campaign.report.scenarios.push(scenarioResult);
  campaign.report.updatedAt = new Date().toISOString();
  campaign.report.summary = summarizeCampaign(campaign.report);
  if (campaign.report.artifactPath) await persist(campaign.report, campaign.report.artifactPath);
  return {
    done: false,
    index: index + 1,
    id: scenario.id,
    threadId: scenarioResult.threadId,
    passTurns: scenarioResult.passTurns,
    failedTurns: scenarioResult.failedTurns,
    reloadEquivalent: scenarioResult.reloadEquivalent,
    failures: scenarioResult.turns.filter((turn) => !turn.pass).map((turn) => ({
      turn: turn.turn,
      prompt: turn.prompt,
      response: turn.response,
      errors: turn.errors,
    })),
    summary: campaign.report.summary,
  };
}

export async function runNextScenario(tab, campaign) {
  const scenarioRun = await beginNextScenario(tab, campaign);
  if (scenarioRun.done) return scenarioRun;
  while (scenarioRun.scenarioResult.turns.length < scenarioRun.scenario.turns.length) {
    await runNextTurn(tab, scenarioRun);
  }
  return completeScenario(tab, campaign, scenarioRun);
}

export function summarizeCampaign(report) {
  const scenarios = report.scenarios || [];
  const turns = scenarios.flatMap((scenario) => scenario.turns || []);
  const passedTurns = turns.filter((turn) => turn.pass).length;
  const passedScenarios = scenarios.filter((scenario) => scenario.pass).length;
  return {
    completedScenarios: scenarios.length,
    totalTurns: turns.length,
    passedTurns,
    failedTurns: turns.length - passedTurns,
    turnPassRate: turns.length ? Number((passedTurns / turns.length).toFixed(4)) : 0,
    passedScenarios,
    failedScenarios: scenarios.length - passedScenarios,
    scenarioPassRate: scenarios.length ? Number((passedScenarios / scenarios.length).toFixed(4)) : 0,
    reloadFailures: scenarios.filter((scenario) => !scenario.reloadEquivalent).map((scenario) => scenario.id),
  };
}

export async function finishCampaign(campaign) {
  campaign.report.finishedAt = new Date().toISOString();
  campaign.report.summary = summarizeCampaign(campaign.report);
  if (campaign.report.artifactPath) await persist(campaign.report, campaign.report.artifactPath);
  return campaign.report.summary;
}
