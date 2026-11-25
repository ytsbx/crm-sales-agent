/**
 * 列表空态。
 *
 * 请求成功但没数据 → 显示页面的「还没有 xx」；
 * 请求本身失败（403 无权限 / 40302 不在数据范围等）→ 显示后端的错误说明，
 * 不能让业务员把「没权限」误解成「还没有数据」。
 *
 * 返回的是**节点**而不是字符串：全站空态统一成「图标 + 结论 + 怎么开始」。
 * 第三个参数是「怎么开始」，要指向具体按钮；不传就只显示结论。
 *
 * 实现写在 `shared/components/EmptyState.tsx`（要写 JSX），这里只做转发：
 * 本文件路径被 12 个页面 import 着，改成 `.tsx` 会让 Vite 的模块图失效
 * （实测：改名后懒加载页面全部报 `Failed to fetch dynamically imported module`）。
 *
 * 用法：`empty={emptyText(query, '还没有商机', '点右上角「新建商机」开始')}`
 */
export { emptyTextNode as emptyText } from '../components/EmptyState'
