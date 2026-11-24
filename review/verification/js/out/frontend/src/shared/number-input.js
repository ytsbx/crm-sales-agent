"use strict";
/**
 * 表单里的"数字文本 → 提交值"解析。**全项目只此一处**（2026-10-14 收口）。
 *
 * ## 为什么必须共用一份
 *
 * 各处原来直接写 `Number(x || 0)` 或 `Number(x)`，于是有两类**静默丢输入**：
 *
 * 1. `Number('abc' || 0)` —— `'abc'` 是 truthy，`|| 0` 根本不起作用，
 *    结果是 `NaN`；而 `JSON.stringify({x: NaN})` 产出 `{"x":null}`，
 *    后端把 `null` 理解成"**没提供**这个字段"。
 *    实测（价格中心成本表单）：采购成本填 `abc` 点保存 → 界面回"成本已保存"，
 *    库里那项变成空的 → 核价按更低的成本算 → 毛利虚高、本该触发的低价审批不触发。
 * 2. `Number('' || 0)` —— 空串被静默转成 **0**。对"必填"的公斤单价，
 *    清空后保存会**建一条 0 元/kg 的费率**，所有走这条线路的运费算成 0。
 *
 * 正确做法是把三种情况分开（这个判据原来长在
 * `PriceCenterPage.parseOptionalNumber` 里，注释已经写明"各处直接写 Number(x)"
 * 的教训，但**其它文件又各写了一遍**，于是同一个坑在别处复现 ——
 * 所以这次把它抽成共享件，让"再写一遍"不再是最省事的做法）：
 *
 * - 空串 / 只有空白 → `null`（= 明确清空，合法操作）
 * - 能解析成有限数字 → 数字
 * - 其它（字母、`1abc`、`Infinity`…）→ **`NaN`**，调用方**必须**拦下并提示
 *
 * ⚠️ 调用方不要自己再写 `Number(...)`；用本文件的函数，并在 `NaN` 时
 * 抛错/提示（`firstInvalidNumberField` 帮你点名是哪个字段）。
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.NUMBER_FIELD_LABEL = void 0;
exports.parseOptionalNumber = parseOptionalNumber;
exports.parseOptionalDecimal = parseOptionalDecimal;
exports.parseRequiredNumber = parseRequiredNumber;
exports.parseQuantity = parseQuantity;
exports.requiredQuantityText = requiredQuantityText;
exports.requiredNumberOrThrow = requiredNumberOrThrow;
exports.firstInvalidNumberField = firstInvalidNumberField;
exports.invalidNumberMessage = invalidNumberMessage;
exports.formatQuantityText = formatQuantityText;
/** 把表单里的数字文本解析成提交值。空串 → `null`；坏输入 → `NaN`。 */
function parseOptionalNumber(raw) {
    const text = String(raw ?? '').trim();
    if (text === '')
        return null;
    const n = Number(text);
    return Number.isFinite(n) ? n : NaN;
}
/** 金额提交保留十进制文本；校验沿用原解析器，旧非十进制数字写法保持原数值。 */
function parseOptionalDecimal(raw) {
    const numeric = parseOptionalNumber(raw);
    if (numeric === null || Number.isNaN(numeric))
        return numeric;
    const text = String(raw).trim();
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text) ? text : numeric;
}
/**
 * 把表单里的数字文本解析成**必填**数字（空串也算坏输入 → `NaN`）。
 *
 * 用于"清空 = 静默变 0"会出事的字段（例如运费费率的公斤单价）——
 * 空串与字母都不接受，由调用方提示。
 */
function parseRequiredNumber(raw) {
    const text = String(raw ?? '').trim();
    if (text === '')
        return NaN;
    const n = Number(text);
    return Number.isFinite(n) ? n : NaN;
}
/** 把表单里的数量文本解析成提交值，并限制最多 3 位小数（与库列 `Numeric(16,3)` 对齐）。
 *
 * 不在前端先拦的话，`1.2349` 会被数据库静默舍成 `1.235`，用户以为上限是 1.2349，
 * 之后看到"区间重叠"也复现不出原因（审查 R04 的那个坑）。
 */
function parseQuantity(raw) {
    const n = parseOptionalNumber(raw);
    if (n === null || Number.isNaN(n))
        return n;
    const decimals = (String(raw).split('.')[1] ?? '').length;
    return decimals > 3 ? NaN : n;
}
/** 校验明细数量并保留十进制文本，对齐 Numeric(16,3)。 */
function requiredQuantityText(raw, label = '数量', allowZero = false) {
    const text = raw.trim();
    const decimal = /^\+?(\d*)(?:\.(\d*))?$/.exec(text);
    if (!decimal || !(allowZero ? /\d/ : /[1-9]/).test(text)) {
        throw new Error(`${label}必须填${allowZero ? '大于或等于' : '大于'} 0 的数字`);
    }
    const [, whole, fraction = ''] = decimal;
    if (whole.replace(/^0+/, '').length > 13 || fraction.replace(/0+$/, '').length > 3) {
        throw new Error(`${label}整数最多 13 位，小数最多 3 位`);
    }
    return text;
}
/**
 * 必填数字：空串或坏输入都**抛错并点名**（不给"静默变 0"的机会）。
 *
 * 用于那些"清空 = 静默变 0 会出大事"的字段 —— 例如运费费率的公斤单价：
 * 清空后保存会建一条 **0 元/kg** 的费率，所有走这条线路的运费算成 0。
 */
function requiredNumberOrThrow(raw, label, hint = '必须填数字') {
    const n = parseRequiredNumber(raw);
    if (Number.isNaN(n)) {
        throw new Error(`${label ?? '这一项'}${hint}`);
    }
    return n;
}
/** 字段名 → 中文，用于"哪个字段填错了"的提示。缺省时回退字段名本身。 */
exports.NUMBER_FIELD_LABEL = {
    min_qty: '数量下限',
    max_qty: '数量上限',
    standard_price: '标准价',
    guide_price: '指导价',
    minimum_price: '最低保护价',
    target_margin: '目标利润率',
    agreed_price: '约定价',
    purchase_cost: '采购成本',
    package_cost: '包装成本',
    production_cost: '生产成本',
    processing_cost: '加工成本',
    minimum_margin: '最低利润率',
    unit_price_per_kg: '公斤单价',
    unit_price_per_volume: '体积单价',
    min_charge: '最低收费',
    eta_days: '最短交期',
    eta_days_max: '最长交期',
    new_customer_target: '新客目标',
    sales_target: '销售额目标',
    repeat_customer_target: '复购目标',
    sort_no: '排序号',
    expected_amount: '预计金额',
    price_assumption: '价格假设',
};
/** 从"已解析的值"里挑出坏字段（`NaN`）。返回字段名列表，空 = 全 OK。 */
function firstInvalidNumberField(values) {
    return Object.entries(values)
        .filter(([, v]) => typeof v === 'number' && Number.isNaN(v))
        .map(([k]) => k);
}
/** 给一句人话的错误信息（点名第一个坏字段）。全 OK 时返回空串。 */
function invalidNumberMessage(values, hint = '只能填数字') {
    const bad = firstInvalidNumberField(values);
    if (!bad.length)
        return '';
    return `${exports.NUMBER_FIELD_LABEL[bad[0]] ?? bad[0]}${hint}`;
}
/**
 * 精确数量文本分组展示，不先转浮点；普通小数保留原千分位/去尾零格式。
 *
 * ⚠️ 2026-11-17 视觉审视报了 P0：订单草稿详情整页白屏，
 * 控制台是 `TypeError: value.split is not a function`。
 * 根因就在这里：调用方（OrderDraftsPage / SampleFromSourceModal）把
 * `original_quantity` 直接传进来，而后端有时给字符串、有时给数字。
 * 正则 `test()` 会把数字隐式转成字符串、照样通过，于是下一行
 * `value.split()` 落在数字上 —— 一个字段的格式问题把整页错误边界打穿。
 *
 * 所以入参先统一成字符串再处理；数字/字符串/空值都不会再抛异常。
 */
function formatQuantityText(value) {
    if (value === null || value === undefined || value === '')
        return '-';
    const text = typeof value === 'string' ? value : String(value);
    if (!/^\d+(?:\.\d+)?$/.test(text))
        return text;
    const [whole, fraction] = text.split('.');
    const tail = fraction?.replace(/0+$/, '');
    return BigInt(whole).toLocaleString('zh-CN') + (tail ? `.${tail}` : '');
}
